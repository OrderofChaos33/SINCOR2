// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IERC4626} from "@openzeppelin/contracts/interfaces/IERC4626.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

import {AggregatorV3Interface} from "../interfaces/AggregatorV3Interface.sol";
import {ISincPSM} from "../interfaces/ISincVaultSystem.sol";
import {SincConstants} from "./SincConstants.sol";

contract SincPSM is ISincPSM, AccessControl, ReentrancyGuard {
    using SafeERC20 for IERC20;

    bytes32 public constant RISK_ADMIN_ROLE = keccak256("RISK_ADMIN_ROLE");

    IERC4626 public immutable vault;
    IERC20 public immutable assetToken;
    IERC20 public immutable scToken;
    address public immutable treasury;

    AggregatorV3Interface public scPriceFeed;
    AggregatorV3Interface public targetPriceFeed;

    uint256 public band0ThresholdBps;
    uint256 public band1ThresholdBps;

    uint16[3] public mintFeeBps;
    uint16[3] public redeemFeeBps;

    uint8 public override currentBand;

    error InvalidConfig();
    error InvalidPrice();

    constructor(
        address admin,
        address vault_,
        address treasury_,
        address scPriceFeed_,
        address targetPriceFeed_,
        uint256 b0,
        uint256 b1
    ) {
        if (
            admin == address(0) || vault_ == address(0) || treasury_ == address(0) || scPriceFeed_ == address(0)
                || targetPriceFeed_ == address(0) || b0 > b1
        ) revert InvalidConfig();

        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(RISK_ADMIN_ROLE, admin);

        vault = IERC4626(vault_);
        assetToken = IERC20(vault.asset());
        scToken = IERC20(vault_);
        treasury = treasury_;

        scPriceFeed = AggregatorV3Interface(scPriceFeed_);
        targetPriceFeed = AggregatorV3Interface(targetPriceFeed_);

        band0ThresholdBps = b0;
        band1ThresholdBps = b1;

        mintFeeBps = [10, 25, 50];
        redeemFeeBps = [10, 25, 50];
    }

    function setPriceFeeds(address scFeed, address targetFeed) external onlyRole(RISK_ADMIN_ROLE) {
        if (scFeed == address(0) || targetFeed == address(0)) revert InvalidConfig();
        scPriceFeed = AggregatorV3Interface(scFeed);
        targetPriceFeed = AggregatorV3Interface(targetFeed);
    }

    function setBandThresholds(uint256 b0, uint256 b1) external onlyRole(RISK_ADMIN_ROLE) {
        if (b0 > b1) revert InvalidConfig();
        band0ThresholdBps = b0;
        band1ThresholdBps = b1;
    }

    function setFees(uint16[3] calldata mintFees, uint16[3] calldata redeemFees) external onlyRole(RISK_ADMIN_ROLE) {
        for (uint256 i; i < 3; ++i) {
            if (mintFees[i] > SincConstants.BPS_DENOM || redeemFees[i] > SincConstants.BPS_DENOM) revert InvalidConfig();
            mintFeeBps[i] = mintFees[i];
            redeemFeeBps[i] = redeemFees[i];
        }
    }

    function mint(uint256 assetAmount, address receiver) external nonReentrant returns (uint256 scOut) {
        if (assetAmount == 0) revert InvalidConfig();
        uint8 band = _refreshBand();

        uint256 fee = (assetAmount * mintFeeBps[band]) / SincConstants.BPS_DENOM;
        uint256 netAssets = assetAmount - fee;

        assetToken.safeTransferFrom(msg.sender, address(this), assetAmount);
        if (fee > 0) {
            assetToken.safeTransfer(treasury, fee);
        }

        assetToken.forceApprove(address(vault), netAssets);
        scOut = vault.deposit(netAssets, receiver);

        emit Minted(msg.sender, assetAmount, scOut);
    }

    function redeem(uint256 scAmount, address receiver) external nonReentrant returns (uint256 assetOut) {
        if (scAmount == 0) revert InvalidConfig();
        uint8 band = _refreshBand();

        uint256 feeShares = (scAmount * redeemFeeBps[band]) / SincConstants.BPS_DENOM;
        uint256 netShares = scAmount - feeShares;

        scToken.safeTransferFrom(msg.sender, address(this), scAmount);

        if (feeShares > 0) {
            vault.redeem(feeShares, treasury, address(this));
        }

        assetOut = vault.redeem(netShares, receiver, address(this));
        emit Redeemed(msg.sender, scAmount, assetOut);
    }

    function psmArb() external onlyRole(RISK_ADMIN_ROLE) returns (uint8 band, uint256 deviation) {
        deviation = pegDeviationBps();
        band = _bandFromDeviation(deviation);
        currentBand = band;
        emit PegBandUpdated(band, deviation);
    }

    function pegDeviationBps() public view returns (uint256) {
        uint256 scPrice = _readPrice(scPriceFeed);
        uint256 targetPrice = _readPrice(targetPriceFeed);
        if (targetPrice == 0) revert InvalidPrice();

        uint256 diff = scPrice > targetPrice ? scPrice - targetPrice : targetPrice - scPrice;
        return (diff * SincConstants.BPS_DENOM) / targetPrice;
    }

    function _refreshBand() internal returns (uint8 band) {
        uint256 deviation = pegDeviationBps();
        band = _bandFromDeviation(deviation);
        if (band != currentBand) {
            currentBand = band;
            emit PegBandUpdated(band, deviation);
        }
    }

    function _bandFromDeviation(uint256 deviation) internal view returns (uint8) {
        if (deviation <= band0ThresholdBps) return 0;
        if (deviation <= band1ThresholdBps) return 1;
        return 2;
    }

    function _readPrice(AggregatorV3Interface feed) internal view returns (uint256) {
        (, int256 answer,,,) = feed.latestRoundData();
        if (answer <= 0) revert InvalidPrice();

        uint8 d = feed.decimals();
        if (d > 8) {
            return uint256(answer) / (10 ** (d - 8));
        }
        return uint256(answer) * (10 ** (8 - d));
    }
}
