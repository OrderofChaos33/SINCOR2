// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {BaseHook} from "v4-periphery/src/base/hooks/BaseHook.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {Hooks} from "v4-core/src/libraries/Hooks.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency, CurrencyLibrary} from "v4-core/src/types/Currency.sol";
import {ReentrancyGuardTransient} from "@openzeppelin/contracts/utils/ReentrancyGuardTransient.sol";

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
        uint256 declaredProfit,
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

    function executeMEV(
        PoolKey calldata key,
        uint256 flashAmount,
        uint256 expectedProfit,
        bytes calldata searcherPayload
    ) external nonReentrant {
        if (key.hooks != address(this)) revert InvalidPoolKey();
        if (expectedProfit == 0) revert ZeroProfitDeclared();

        bytes memory data = abi.encode(msg.sender, key, flashAmount, expectedProfit, searcherPayload);
        poolManager.unlock(data);
    }

    function unlockCallback(bytes calldata data) external override returns (bytes memory) {
        if (msg.sender != address(poolManager)) revert UnauthorizedCallback();

        (
            address searcher,
            PoolKey memory key,
            uint256 flashAmount,
            uint256 expectedProfit,
            bytes memory payload
        ) = abi.decode(data, (address, PoolKey, uint256, uint256, bytes));

        // === DYNAMIC REAL ASSET DETECTION ===
        bool isPTokenZero = Currency.unwrap(key.currency0) == address(pToken);
        Currency realAsset = isPTokenZero ? key.currency1 : key.currency0;

        uint256 requiredTax = (expectedProfit * BASE_TAX_BIPS) / 10000;

        // Record hook balance on the REAL asset before execution
        uint256 balanceBefore = realAsset.balanceOfSelf();

        // Issue flash credit
        pToken.mintEphemeral(searcher, flashAmount);

        // Execute searcher logic
        (bool success, ) = searcher.call(payload);
        if (!success) revert ExecutionFailed();

        // Enforce repayment
        pToken.burnEphemeral(searcher, flashAmount);

        // Enforce tax via hook balance delta on REAL asset
        uint256 balanceAfter = realAsset.balanceOfSelf();
        if (balanceAfter < balanceBefore + requiredTax) revert TaxEvasionDetected();

        if (requiredTax > 0) {
            uint256 protocolShare = (requiredTax * TREASURY_SHARE_BIPS) / 10000;
            uint256 lpShare = requiredTax - protocolShare;

            // === CORRECT V4 FLASH ACCOUNTING ===
            if (realAsset.isNative()) {
                // Native ETH path
                if (protocolShare > 0) {
                    realAsset.transfer(protocolTreasury, protocolShare);
                }
                if (lpShare > 0) {
                    realAsset.transfer(address(poolManager), lpShare);
                    poolManager.donate(key, 0, lpShare, ""); // amount1 since real asset is currency1
                    poolManager.settle{value: lpShare}(realAsset);
                }
            } else {
                // ERC20 path
                poolManager.sync(realAsset);

                if (protocolShare > 0) {
                    realAsset.transfer(protocolTreasury, protocolShare);
                }
                if (lpShare > 0) {
                    realAsset.transfer(address(poolManager), lpShare);
                    poolManager.donate(key, lpShare, 0, ""); // amount0 since real asset is currency0
                    poolManager.settle(realAsset);
                }
            }

            emit MEVLoopExecuted(searcher, flashAmount, expectedProfit, lpShare, protocolShare);
        }

        return "";
    }
}