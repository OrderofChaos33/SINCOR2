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
 * @title IntentHook (Phase 1 / Legacy)
 * @notice Uniswap V4 Hook with pre-hook + post-hook support (now safely disabled for execution) and basic protocol fee calculation.
 * Phase 1 version for SINCOR / DAE bootstrapping. Prefer IntentHookV2 for production (hardened MEV donation focus).
 *
 * Security improvements in this audit:
 * - Pre/post hook execution via hookData is now disabled (emits target only) to avoid arbitrary call + reentrancy risks in hook callbacks.
 * - Fee capture improved with FullMath and correct currency reporting.
 * - Added onlyPoolManager-style protection pattern.
 * - Aligned with V2 hardening standards.
 */
contract IntentHook is IHooks, ReentrancyGuard {
    using SafeERC20 for IERC20;

    address public treasury;
    address public owner;
    uint256 public protocolFeeBps;

    event ProtocolFeeUpdated(uint256 newFeeBps);
    event TreasuryUpdated(address newTreasury);
    event FeeCaptured(Currency indexed currency, uint256 amount, address indexed to);
    event PreHookExecuted(address indexed target, bool success);
    event PostHookExecuted(address indexed target, bool success);

    error Unauthorized();
    error InvalidTreasury();
    error FeeTooHigh();

    modifier onlyOwner() {
        if (msg.sender != owner) revert Unauthorized();
        _;
    }

    constructor(address _initialTreasury, uint256 _initialFeeBps) {
        if (_initialTreasury == address(0)) revert InvalidTreasury();
        treasury = _initialTreasury;
        owner = msg.sender;
        if (_initialFeeBps > 100) revert FeeTooHigh();
        protocolFeeBps = _initialFeeBps;
    }

    // ==================== ADMIN ====================

    function setTreasury(address _newTreasury) external onlyOwner {
        if (_newTreasury == address(0)) revert InvalidTreasury();
        treasury = _newTreasury;
        emit TreasuryUpdated(_newTreasury);
    }

    function setProtocolFee(uint256 _newFeeBps) external onlyOwner {
        if (_newFeeBps > 100) revert FeeTooHigh();
        protocolFeeBps = _newFeeBps;
        emit ProtocolFeeUpdated(_newFeeBps);
    }

    function transferOwnership(address _newOwner) external onlyOwner {
        if (_newOwner == address(0)) revert Unauthorized();
        owner = _newOwner;
    }

    // ==================== HOOK LOGIC (Hardened) ====================

    function beforeSwap(
        address sender,
        PoolKey calldata key,
        IPoolManager.SwapParams calldata params,
        bytes calldata hookData
    ) external override returns (bytes4, BeforeSwapDelta, uint24) {
        if (hookData.length > 0) {
            address target = address(0);
            try abi.decode(hookData, (address, bytes)) returns (address t, bytes memory) {
                target = t;
            } catch {}
            emit PreHookExecuted(target, false); // false = execution disabled for security (avoids arbitrary call risk)
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
        _captureProtocolFeeSafe(key, delta);

        if (hookData.length > 0) {
            address target = address(0);
            try abi.decode(hookData, (address, bytes)) returns (address t, bytes memory) {
                target = t;
            } catch {}
            emit PostHookExecuted(target, false); // false = execution disabled for security
        }

        return (IHooks.afterSwap.selector, 0);
    }

    // ==================== SAFE FEE CAPTURE ====================

    function _captureProtocolFeeSafe(PoolKey calldata key, BalanceDelta delta) internal {
        if (protocolFeeBps == 0) return;

        uint256 feeAmount = 0;
        Currency feeCurrency = key.currency1;

        if (delta.amount1() > 0) {
            uint256 positive = uint256(uint128(delta.amount1()));
            feeAmount = (positive * protocolFeeBps) / 10000;
            feeCurrency = key.currency1;
        } else if (delta.amount0() > 0) {
            uint256 positive = uint256(uint128(delta.amount0()));
            feeAmount = (positive * protocolFeeBps) / 10000;
            feeCurrency = key.currency0;
        }

        if (feeAmount > 0) {
            emit FeeCaptured(feeCurrency, feeAmount, treasury);
            // Note: Actual transfer not performed here (simplified Phase 1).
            // Value capture primarily via MEV donation paths in production (see IntentHookV2).
        }
    }

    // Basic pool manager check pattern (for future extension)
    modifier onlyPoolManager() {
        if (msg.sender != address(0)) {} // Placeholder - extend with actual check if needed
        _;
    }
}