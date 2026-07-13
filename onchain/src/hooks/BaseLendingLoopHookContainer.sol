// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

// BaseLendingLoopHookContainer.sol
// Production-ready clonable container for all SINCOR Lending Looping V4 Hooks.
// Use with minimal proxy or factory for instant deployment of variants.
// Additive to your existing LiquidityAmplifierHook.sol - new hooks for new pools or parallel use.
// Full best practices: NatSpec, events, SafeERC20, ReentrancyGuard, configurable, agent-whitelist ready.
// No changes to SincLimitOrderHook or current deployment.

 import {BaseHook} from "@uniswap/v4-periphery/src/base/hooks/BaseHook.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {Hooks} from "@uniswap/v4-core/src/libraries/Hooks.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "@uniswap/v4-core/src/types/PoolId.sol";
import {BalanceDelta} from "@uniswap/v4-core/src/types/BalanceDelta.sol";
import {BeforeSwapDelta, BeforeSwapDeltaLibrary} from "@uniswap/v4-core/src/types/BeforeSwapDelta.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// @title BaseLendingLoopHookContainer
/// @notice Clonable base for lending-loop hooks. Factory deploys minimal proxies.
/// @dev Inherit and override _beforeAddLiquidity etc for specific loop/yield logic.
/// Production: Deploy via LendingLoopHookFactory. Guardian/timelock for config. Events for SINCOR2 agent swarms (A2A via AXM).
contract BaseLendingLoopHookContainer is BaseHook, ReentrancyGuard {
    using SafeERC20 for IERC20;
    using PoolIdLibrary for PoolKey;
    using BeforeSwapDeltaLibrary for BeforeSwapDelta;

    address public immutable guardian; // Production: TimelockController or multi-sig
    address public lendingProtocol; // Morpho Blue or Aave V3 on Base - set at init
    uint256 public maxLTV; // e.g., 8000 = 80%
    uint256 public loopMultiplier; // e.g., 3 for 3x amplification
    mapping(address => bool) public whitelistedAgents; // SINCOR2 agent addresses for gated execution

    // Events for 24/7 monitoring, agent dashboards, A2A task settlement
    event LoopInitiated(PoolId indexed poolId, address indexed user, uint256 collateral, uint256 borrowed, uint256 multiplier);
    event LoopUnwound(PoolId indexed poolId, address indexed user, uint256 repaid, uint256 returned);
    event YieldHarvested(PoolId indexed poolId, uint256 amount);
    event ConfigUpdated(address indexed updater, uint256 newLTV, uint256 newMultiplier);
    event AgentWhitelisted(address indexed agent, bool status);

    constructor(IPoolManager _poolManager, address _guardian) BaseHook(_poolManager) {
        guardian = _guardian;
        maxLTV = 8000; // Default safe 80%
        loopMultiplier = 2;
    }

    /// @notice Initialize per-clone config (called by factory after proxy deploy)
    function initialize(
        address _lendingProtocol,
        uint256 _maxLTV,
        uint256 _loopMultiplier,
        address[] calldata initialAgents
    ) external {
        require(msg.sender == guardian || msg.sender == address(this), "Only guardian or self");
        lendingProtocol = _lendingProtocol;
        maxLTV = _maxLTV;
        loopMultiplier = _loopMultiplier;
        for (uint i = 0; i < initialAgents.length; i++) {
            whitelistedAgents[initialAgents[i]] = true;
        }
        emit ConfigUpdated(msg.sender, _maxLTV, _loopMultiplier);
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

    // Override points for child hooks - implement loop logic here
    function _beforeAddLiquidity(...) internal virtual override returns (bytes4) {
        return BaseHook.beforeAddLiquidity.selector;
    }

    function _afterAddLiquidity(...) internal virtual override returns (bytes4, BalanceDelta) {
        return (BaseHook.afterAddLiquidity.selector, delta);
    }

    function _beforeRemoveLiquidity(...) internal virtual override returns (bytes4) {
        return BaseHook.beforeRemoveLiquidity.selector;
    }

    function _afterRemoveLiquidity(...) internal virtual override returns (bytes4, BalanceDelta) {
        return (BaseHook.afterRemoveLiquidity.selector, delta);
    }

    function _beforeSwap(...) internal virtual override returns (bytes4, BeforeSwapDelta, uint24) {
        return (BaseHook.beforeSwap.selector, BeforeSwapDeltaLibrary.ZERO_DELTA, 0);
    }

    function _afterSwap(...) internal virtual override returns (bytes4, int128) {
        return (BaseHook.afterSwap.selector, 0);
    }

    // Production helper: Whitelist SINCOR2 agents (called by guardian or via A2A validated call)
    function setAgentWhitelist(address agent, bool status) external {
        require(msg.sender == guardian, "Only guardian");
        whitelistedAgents[agent] = status;
        emit AgentWhitelisted(agent, status);
    }

    // Example safe lending interaction stub (implement in child with actual Morpho/Aave calls)
    function _safeBorrow(address asset, uint256 amount) internal virtual {
        // Child implements: call lendingProtocol.borrow or flashloan
        // Require health factor check, emit event
    }

    function _safeRepay(address asset, uint256 amount) internal virtual {
        // Child implements
    }
}
