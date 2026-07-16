// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {BaseHook} from "v4-periphery/src/base/hooks/BaseHook.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {Hooks} from "v4-core/src/libraries/Hooks.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency, CurrencyLibrary} from "v4-core/src/types/Currency.sol";
import {ReentrancyGuardTransient} from "@openzeppelin/contracts/utils/ReentrancyGuardTransient.sol";

/// @notice Strict interface searchers must implement for typed, contained execution
interface IMoebiusSearcher {
    function moebiusExecute(bytes calldata payload) external;
}

interface IPhantomCreditToken {
    function mintEphemeral(address to, uint256 amount) external;
    function burnEphemeral(address from, uint256 amount) external;
}

contract MoebiusMEVHook is BaseHook, ReentrancyGuardTransient {
    using CurrencyLibrary for Currency;

    IPhantomCreditToken public immutable pToken;
    address public immutable protocolTreasury;

    uint256 public constant TREASURY_SHARE_BIPS = 2000; // 20%
    uint256 public constant BASE_TAX_BIPS = 1000;       // 10%

    event MEVLoopExecuted(
        address indexed searcher,
        uint256 flashAmount,
        uint256 expectedProfit,
        uint256 actualTaxPaid,
        uint256 lpShare,
        uint256 protocolShare
    );

    error UnauthorizedCallback();
    error InvalidPoolKey();
    error ExecutionFailed();
    error TaxEvasionDetected();
    error ZeroProfitDeclared();

    constructor(
        IPoolManager _poolManager,
        address _pToken,
        address _treasury
    ) BaseHook(_poolManager) {
        require(_pToken != address(0) && _treasury != address(0), "Zero address");
        pToken = IPhantomCreditToken(_pToken);
        protocolTreasury = _treasury;
    }

    function getHookPermissions() public pure override returns (Hooks.Permissions memory) {
        return Hooks.Permissions({
            beforeInitialize: false, afterInitialize: false,
            beforeAddLiquidity: false, afterAddLiquidity: false,
            beforeRemoveLiquidity: false, afterRemoveLiquidity: false,
            beforeSwap: false, afterSwap: false,
            beforeDonate: false, afterDonate: false,
            beforeSwapReturnDelta: false, afterSwapReturnDelta: false,
            afterAddLiquidityReturnDelta: false, afterRemoveLiquidityReturnDelta: false
        });
    }

    /// @notice Entry point. Searcher declares expected profit upfront. Full validation.
    function executeMEV(
        PoolKey calldata key,
        uint256 flashAmount,
        uint256 expectedProfit,
        bytes calldata searcherPayload
    ) external nonReentrant {
        if (key.hooks != address(this)) revert InvalidPoolKey();
        
        address c0 = Currency.unwrap(key.currency0);
        address c1 = Currency.unwrap(key.currency1);
        if (c0 != address(pToken) && c1 != address(pToken)) revert InvalidPoolKey();
        if (c0 == c1) revert InvalidPoolKey(); // Sanity against malformed keys
        
        if (expectedProfit == 0) revert ZeroProfitDeclared();

        bytes memory data = abi.encode(msg.sender, key, flashAmount, expectedProfit, searcherPayload);
        poolManager.unlock(data);
    }

    /// @notice The V4 callback - perfected with defense-in-depth, typed execution, V4 compliant settlement, safe tax math
    function unlockCallback(bytes calldata data) external override returns (bytes memory) {
        if (msg.sender != address(poolManager)) revert UnauthorizedCallback();

        (
            address searcher,
            PoolKey memory key,
            uint256 flashAmount,
            uint256 expectedProfit,
            bytes memory payload
        ) = abi.decode(data, (address, PoolKey, uint256, uint256, bytes));

        // Defense in depth: ensure we don't process garbage data from a rogue nested unlock
        if (key.hooks != address(this)) revert InvalidPoolKey();

        // 1. Dynamic Sorting for real asset
        bool isZeroPToken = Currency.unwrap(key.currency0) == address(pToken);
        Currency realAsset = isZeroPToken ? key.currency1 : key.currency0;

        uint256 requiredTax = (expectedProfit * BASE_TAX_BIPS) / 10000;
        uint256 balanceBefore = realAsset.balanceOfSelf();

        // 2. Flash Issue & Typed Execution (searcher responsible for any nested V4 frames)
        pToken.mintEphemeral(searcher, flashAmount);
        IMoebiusSearcher(searcher).moebiusExecute(payload);
        pToken.burnEphemeral(searcher, flashAmount);

        // 3. Tax Enforcement with safe math (prevents underflow if balance decreased)
        uint256 balanceAfter = realAsset.balanceOfSelf();
        if (balanceAfter <= balanceBefore) revert TaxEvasionDetected();
        uint256 actualTaxPaid = balanceAfter - balanceBefore;
        if (actualTaxPaid < requiredTax) revert TaxEvasionDetected();

        uint256 protocolShare = 0;
        uint256 lpShare = 0;

        if (actualTaxPaid > 0) {
            protocolShare = (actualTaxPaid * TREASURY_SHARE_BIPS) / 10000;
            lpShare = actualTaxPaid - protocolShare;

            if (protocolShare > 0) {
                realAsset.transfer(protocolTreasury, protocolShare);
            }

            if (lpShare > 0) {
                uint256 amount0 = isZeroPToken ? 0 : lpShare;
                uint256 amount1 = isZeroPToken ? lpShare : 0;
                
                poolManager.donate(key, amount0, amount1, "");

                // 4. V4 Core compliant settlement (no-arg settle after sync/transfer for ERC20; payable no-arg for native)
                if (realAsset.isNative()) {
                    poolManager.settle{value: lpShare}();
                } else {
                    poolManager.sync(realAsset);
                    realAsset.transfer(address(poolManager), lpShare);
                    poolManager.settle();
                }
            }
        }

        // 5. Always emit for verifiable auction mechanics (includes actualTaxPaid for overbids)
        emit MEVLoopExecuted(searcher, flashAmount, expectedProfit, actualTaxPaid, lpShare, protocolShare);

        return "";
    }
}