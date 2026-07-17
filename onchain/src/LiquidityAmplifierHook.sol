// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {BaseHook} from "@uniswap/v4-periphery/src/base/hooks/BaseHook.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {Hooks} from "@uniswap/v4-core/src/libraries/Hooks.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "@uniswap/v4-core/src/types/PoolId.sol";
import {BalanceDelta} from "@uniswap/v4-core/src/types/BalanceDelta.sol";
import {BeforeSwapDelta, BeforeSwapDeltaLibrary} from "@uniswap/v4-core/src/types/BeforeSwapDelta.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/security/ReentrancyGuard.sol";

/// @title LiquidityAmplifierHook
/// @notice Production-oriented additive Uniswap V4 hook for SINCOR liquidity amplification.
///         Designed for shared/virtual liquidity (Aqua/Fluid-inspired) + yield vault integration.
///         Pulls idle capital from ERC-4626 style vaults or RehypothecationAdapter for swaps,
///         returns capital + fees immediately after. LPs earn continuous external yield + swap fees.
///         Fully additive to SincLimitOrderHook and IntentHookV2. No interference with existing pools.
///         Guardian-controlled. Ready for A2A agent interaction and AccountingHub wiring.
/// @dev Boss task #78 progress: core structure, events, permissions, and vault mapping complete.
///      Full JIT liquidity deployment and exact amountSpecified math left as production TODOs
///      (requires poolManager.unlock + modifyLiquidity in the same callback context).
contract LiquidityAmplifierHook is BaseHook, ReentrancyGuard {
    using SafeERC20 for IERC20;
    using BeforeSwapDeltaLibrary for BeforeSwapDelta;
    using PoolIdLibrary for PoolKey;

    /// @notice Yield vault (ERC-4626 compatible) or RehypothecationAdapter per pool
    mapping(PoolId => address) public yieldVaults;

    /// @notice Optional AccountingHub for protocol fee / rehypo tracking
    address public accountingHub;

    address public guardian;
    address public owner;

    // Safety
    uint256 public constant MAX_PULL_BPS = 5000; // 50% of available vault assets max per swap (tuneable)
    bool public paused;

    event LiquidityPulled(PoolId indexed poolId, address indexed vault, uint256 amount0, uint256 amount1);
    event LiquidityReturned(PoolId indexed poolId, address indexed vault, uint256 amount0, uint256 amount1, uint256 fees);
    event YieldVaultUpdated(PoolId indexed poolId, address vault);
    event AccountingHubUpdated(address hub);
    event GuardianUpdated(address newGuardian);
    event Paused(bool isPaused);

    error OnlyGuardian();
    error OnlyOwner();
    error PausedError();
    error InvalidVault();

    modifier onlyGuardian() {
        if (msg.sender != guardian && msg.sender != owner) revert OnlyGuardian();
        _;
    }

    modifier onlyOwner() {
        if (msg.sender != owner) revert OnlyOwner();
        _;
    }

    modifier whenNotPaused() {
        if (paused) revert PausedError();
        _;
    }

    constructor(IPoolManager _poolManager, address _guardian) BaseHook(_poolManager) {
        require(_guardian != address(0), "zero guardian");
        guardian = _guardian;
        owner = msg.sender;
    }

    function getHookPermissions() public pure override returns (Hooks.Permissions memory) {
        return Hooks.Permissions({
            beforeInitialize: false,
            afterInitialize: false,
            beforeAddLiquidity: true,
            afterAddLiquidity: true,
            beforeRemoveLiquidity: true,
            afterRemoveLiquidity: true,
            beforeSwap: true,
            afterSwap: true,
            beforeDonate: false,
            afterDonate: false,
            beforeSwapReturnDelta: false,
            afterSwapReturnDelta: false,
            afterAddLiquidityReturnDelta: false,
            afterRemoveLiquidityReturnDelta: false
        });
    }

    // ==================== ADMIN ====================

    function setYieldVault(PoolId poolId, address vault) external onlyGuardian {
        if (vault == address(0)) revert InvalidVault();
        yieldVaults[poolId] = vault;
        emit YieldVaultUpdated(poolId, vault);
    }

    function setAccountingHub(address hub) external onlyOwner {
        accountingHub = hub;
        emit AccountingHubUpdated(hub);
    }

    function setGuardian(address newGuardian) external onlyOwner {
        require(newGuardian != address(0), "zero");
        guardian = newGuardian;
        emit GuardianUpdated(newGuardian);
    }

    function setPaused(bool _paused) external onlyGuardian {
        paused = _paused;
        emit Paused(_paused);
    }

    // ==================== HOOK CALLBACKS ====================

    function _beforeAddLiquidity(
        address,
        PoolKey calldata,
        IPoolManager.ModifyLiquidityParams calldata,
        bytes calldata
    ) internal override whenNotPaused returns (bytes4) {
        return BaseHook.beforeAddLiquidity.selector;
    }

    function _afterAddLiquidity(
        address,
        PoolKey calldata,
        IPoolManager.ModifyLiquidityParams calldata,
        BalanceDelta delta,
        BalanceDelta,
        bytes calldata
    ) internal override returns (bytes4, BalanceDelta) {
        return (BaseHook.afterAddLiquidity.selector, delta);
    }

    function _beforeRemoveLiquidity(
        address,
        PoolKey calldata,
        IPoolManager.ModifyLiquidityParams calldata,
        bytes calldata
    ) internal override whenNotPaused returns (bytes4) {
        return BaseHook.beforeRemoveLiquidity.selector;
    }

    function _afterRemoveLiquidity(
        address,
        PoolKey calldata,
        IPoolManager.ModifyLiquidityParams calldata,
        BalanceDelta delta,
        BalanceDelta,
        bytes calldata
    ) internal override returns (bytes4, BalanceDelta) {
        return (BaseHook.afterRemoveLiquidity.selector, delta);
    }

    /// @dev Core amplification path. Production: compute required liquidity from params.amountSpecified,
    ///      pull from vault via ERC4626.withdraw or RehypothecationAdapter, temporarily add as concentrated
    ///      liquidity around current tick for the swap, then clean up in afterSwap.
    function _beforeSwap(
        address,
        PoolKey calldata key,
        IPoolManager.SwapParams calldata params,
        bytes calldata
    ) internal override whenNotPaused nonReentrant returns (bytes4, BeforeSwapDelta, uint24) {
        PoolId poolId = key.toId();
        address vault = yieldVaults[poolId];

        if (vault != address(0)) {
            // PRODUCTION TODO:
            // 1. Calculate exact amounts needed from params.amountSpecified + current tick/liquidity
            // 2. Call vault.withdraw(...) or adapter equivalent (non-custodial)
            // 3. Use poolManager.modifyLiquidity inside unlockCallback to deploy temporary range
            // 4. Track pulled amounts for afterSwap return
            // For now emit event so monitoring / A2A agents can observe activity
            emit LiquidityPulled(poolId, vault, 0, 0);
        }

        // Fee remains 0 here (additive). Dynamic fees handled by SincLimitOrderHook if both present.
        return (BaseHook.beforeSwap.selector, BeforeSwapDeltaLibrary.ZERO_DELTA, 0);
    }

    function _afterSwap(
        address,
        PoolKey calldata key,
        IPoolManager.SwapParams calldata,
        BalanceDelta delta,
        bytes calldata
    ) internal override nonReentrant returns (bytes4, int128) {
        PoolId poolId = key.toId();
        address vault = yieldVaults[poolId];

        if (vault != address(0)) {
            // PRODUCTION TODO:
            // 1. Remove temporary liquidity
            // 2. Calculate fees earned (delta)
            // 3. Return principal + fees to vault / AccountingHub
            // 4. Optional: route protocol cut of fees to treasury via hub.recordProtocolFee
            emit LiquidityReturned(poolId, vault, 0, 0, 0);
        }

        return (BaseHook.afterSwap.selector, 0);
    }

    // ==================== VIEW HELPERS ====================

    function getVault(PoolId poolId) external view returns (address) {
        return yieldVaults[poolId];
    }
}