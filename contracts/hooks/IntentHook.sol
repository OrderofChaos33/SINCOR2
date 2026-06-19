// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {BeforeSwapDelta, toBeforeSwapDelta} from "@uniswap/v4-core/src/types/BeforeSwapDelta.sol";
import {BalanceDelta} from "@uniswap/v4-core/src/types/BalanceDelta.sol";
import {Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/security/ReentrancyGuard.sol";

/**
 * @title IntentHook
 * @notice Uniswap V4 Hook with pre-hook + post-hook support and basic protocol fee + surplus capture to treasury.
 * Phase 1 version for SINCOR / DAE bootstrapping.
 *
 * Features:
 * - Pre-hook execution via hookData
 * - Post-hook execution via hookData
 * - Protocol fee capture to treasury
 * - Placeholder for surplus capture (extend in Phase 2)
 *
 * Security: Reentrancy protected. Owner can update fee and treasury.
 */
contract IntentHook is IHooks, ReentrancyGuard {
    using SafeERC20 for IERC20;

    address public treasury;
    address public owner;
    uint256 public protocolFeeBps; // e.g. 25 = 0.25%

    event ProtocolFeeUpdated(uint256 newFeeBps);
    event TreasuryUpdated(address newTreasury);
    event FeeCaptured(Currency indexed currency, uint256 amount);
    event PreHookExecuted(address indexed target, bool success);
    event PostHookExecuted(address indexed target, bool success);

    modifier onlyOwner() {
        require(msg.sender == owner, "IntentHook: Not owner");
        _;
    }

    constructor(address _initialTreasury, uint256 _initialFeeBps) {
        require(_initialTreasury != address(0), "Invalid treasury");
        treasury = _initialTreasury;
        owner = msg.sender;
        protocolFeeBps = _initialFeeBps;
    }

    // ==================== ADMIN ====================

    function setTreasury(address _newTreasury) external onlyOwner {
        require(_newTreasury != address(0), "Invalid treasury");
        treasury = _newTreasury;
        emit TreasuryUpdated(_newTreasury);
    }

    function setProtocolFee(uint256 _newFeeBps) external onlyOwner {
        require(_newFeeBps <= 100, "Fee too high (max 1%)");
        protocolFeeBps = _newFeeBps;
        emit ProtocolFeeUpdated(_newFeeBps);
    }

    function transferOwnership(address _newOwner) external onlyOwner {
        require(_newOwner != address(0), "Invalid owner");
        owner = _newOwner;
    }

    // ==================== HOOK LOGIC ====================

    function beforeSwap(
        address sender,
        PoolKey calldata key,
        IPoolManager.SwapParams calldata params,
        bytes calldata hookData
    ) external override returns (bytes4, BeforeSwapDelta, uint24) {
        // Execute pre-hook if provided in hookData: abi.encode(target, data)
        if (hookData.length > 0) {
            (address preTarget, bytes memory preData) = abi.decode(hookData, (address, bytes));
            if (preTarget != address(0) && preData.length > 0) {
                (bool success, ) = preTarget.call(preData);
                emit PreHookExecuted(preTarget, success);
                require(success, "Pre-hook call failed");
            }
        }
        return (IHooks.beforeSwap.selector, toBeforeSwapDelta(0, 0), 0);
    }

    function afterSwap(
        address sender,
        PoolKey calldata key,
        IPoolManager.SwapParams calldata params,
        BalanceDelta delta,
        bytes calldata hookData
    ) external override nonReentrant returns (bytes4, int128) {
        // 1. Capture protocol fee (simplified - extend with proper currency math in production)
        _captureProtocolFee(key, delta);

        // 2. Execute post-hook if provided
        if (hookData.length > 0) {
            (address postTarget, bytes memory postData) = abi.decode(hookData, (address, bytes));
            if (postTarget != address(0) && postData.length > 0) {
                (bool success, ) = postTarget.call(postData);
                emit PostHookExecuted(postTarget, success);
            }
        }

        return (IHooks.afterSwap.selector, 0);
    }

    // ==================== INTERNAL FEE LOGIC ====================

    function _captureProtocolFee(PoolKey calldata key, BalanceDelta delta) internal {
        if (protocolFeeBps == 0) return;

        // Simplified fee calculation on the positive delta side
        // In production: properly determine which currency is output and calculate fee
        uint256 feeAmount = 0;

        // Example: take fee on token1 if positive
        if (delta.amount1() > 0) {
            feeAmount = (uint256(uint128(delta.amount1())) * protocolFeeBps) / 10000;
        } else if (delta.amount0() > 0) {
            feeAmount = (uint256(uint128(delta.amount0())) * protocolFeeBps) / 10000;
        }

        if (feeAmount > 0) {
            // In real implementation, transfer the fee token to treasury here
            // This is a template - full version needs Currency library calls
            emit FeeCaptured(key.currency1, feeAmount);
            // TODO: Implement actual token transfer to treasury using Currency library
        }
    }
}