// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IERC20Metadata} from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";

import {IAeroExecutionAdapter, ISincFeeFlywheel, ISincRiskModule} from "../interfaces/ISincVaultSystem.sol";
import {SincConstants} from "./SincConstants.sol";

interface IRiskModuleExt {
    function validateExecutionPrice(uint256 dexPriceE18, uint256 referencePriceE18, uint16 deviationBps)
        external
        pure
        returns (bool ok);
}

contract SincFeeFlywheel is ISincFeeFlywheel, AccessControl, ReentrancyGuard {
    using SafeERC20 for IERC20;

    bytes32 public constant KEEPER_ROLE = keccak256("KEEPER_ROLE");

    address public immutable override treasury;
    address public immutable aeroToken;
    IAeroExecutionAdapter public immutable aeroAdapter;
    ISincRiskModule public immutable riskModule;

    uint16 public maxOracleDeviationBps;
    uint256 public lockDuration;

    mapping(address gauge => bool) public gaugeWhitelist;
    mapping(address gauge => uint256) public gaugeWeight;

    error InvalidConfig();
    error GaugeNotAllowed();
    error OraclePriceMismatch();

    constructor(
        address admin,
        address _aeroToken,
        address _aeroAdapter,
        address _riskModule,
        address _treasury,
        uint16 _maxOracleDeviationBps,
        uint256 _lockDuration
    ) {
        if (
            admin == address(0) || _aeroToken == address(0) || _aeroAdapter == address(0) || _riskModule == address(0)
                || _treasury == address(0) || _maxOracleDeviationBps > SincConstants.BPS_DENOM
        ) revert InvalidConfig();

        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(KEEPER_ROLE, admin);

        aeroToken = _aeroToken;
        aeroAdapter = IAeroExecutionAdapter(_aeroAdapter);
        riskModule = ISincRiskModule(_riskModule);
        treasury = _treasury;
        maxOracleDeviationBps = _maxOracleDeviationBps;
        lockDuration = _lockDuration;
    }

    function setGaugeWhitelist(address gauge, bool allowed) external onlyRole(DEFAULT_ADMIN_ROLE) {
        gaugeWhitelist[gauge] = allowed;
    }

    function setGaugeWeight(address gauge, uint256 weight) external onlyRole(DEFAULT_ADMIN_ROLE) {
        gaugeWeight[gauge] = weight;
    }

    function setLockDuration(uint256 newDuration) external onlyRole(DEFAULT_ADMIN_ROLE) {
        lockDuration = newDuration;
    }

    function setMaxOracleDeviationBps(uint16 newDeviation) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (newDeviation > SincConstants.BPS_DENOM) revert InvalidConfig();
        maxOracleDeviationBps = newDeviation;
    }

    function processFees(address baseAsset, uint256 feeAmount, address gauge)
        external
        onlyRole(KEEPER_ROLE)
        nonReentrant
        returns (uint256 buybackAmount, uint256 treasuryAmount)
    {
        if (!gaugeWhitelist[gauge]) revert GaugeNotAllowed();
        if (feeAmount == 0) return (0, 0);

        IERC20(baseAsset).safeTransferFrom(msg.sender, address(this), feeAmount);

        buybackAmount = Math.mulDiv(feeAmount, SincConstants.FEE_SPLIT_BUYBACK_BPS, SincConstants.BPS_DENOM);
        treasuryAmount = feeAmount - buybackAmount;

        if (treasuryAmount > 0) {
            IERC20(baseAsset).safeTransfer(treasury, treasuryAmount);
        }

        if (buybackAmount > 0) {
            uint256 expectedOutOracle = _expectedAeroOut(baseAsset, buybackAmount);
            ISincRiskModule.SwapCheckParams memory p = ISincRiskModule.SwapCheckParams({
                tokenIn: baseAsset,
                tokenOut: aeroToken,
                amountIn: buybackAmount,
                expectedOutOracle: expectedOutOracle,
                maxSlippageBps: SincConstants.MAX_SLIPPAGE_BPS,
                maxOracleDeviationBps: maxOracleDeviationBps
            });

            uint256 minOut = riskModule.validateSwap(p);
            IERC20(baseAsset).forceApprove(address(aeroAdapter), buybackAmount);
            uint256 aeroOut = aeroAdapter.swapToAero(baseAsset, buybackAmount, minOut);

            if (aeroOut < minOut) revert InvalidConfig();

            uint256 dexPriceE18 = Math.mulDiv(buybackAmount, 1e18, aeroOut);
            (uint256 inUsd,) = riskModule.validatePrice(baseAsset);
            (uint256 aeroUsd,) = riskModule.validatePrice(aeroToken);
            uint256 refPriceE18 = Math.mulDiv(inUsd, 1e18, aeroUsd);

            if (!IRiskModuleExt(address(riskModule)).validateExecutionPrice(dexPriceE18, refPriceE18, maxOracleDeviationBps)) {
                revert OraclePriceMismatch();
            }

            IERC20(aeroToken).forceApprove(address(aeroAdapter), aeroOut);
            aeroAdapter.lockVeAero(aeroOut, lockDuration);
            aeroAdapter.voteGauge(gauge, gaugeWeight[gauge]);

            emit AeroPurchased(buybackAmount, aeroOut);
            emit VeAeroLocked(aeroOut, block.timestamp + lockDuration);
            emit GaugeVoted(gauge, gaugeWeight[gauge]);
        }

        emit HarvestSplit(feeAmount, buybackAmount, treasuryAmount);
    }

    function _expectedAeroOut(address tokenIn, uint256 amountIn) internal view returns (uint256 expectedOut) {
        (uint256 tokenInUsd,) = riskModule.validatePrice(tokenIn);
        (uint256 aeroUsd,) = riskModule.validatePrice(aeroToken);

        uint8 inDecimals = IERC20Metadata(tokenIn).decimals();
        uint8 outDecimals = IERC20Metadata(aeroToken).decimals();

        uint256 usdValue = Math.mulDiv(amountIn, tokenInUsd, 10 ** inDecimals);
        expectedOut = Math.mulDiv(usdValue, 10 ** outDecimals, aeroUsd);
    }
}
