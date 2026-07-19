// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {BaseHook} from "@uniswap/v4-periphery/src/base/hooks/BaseHook.sol";
import {Hooks} from "@uniswap/v4-core/src/libraries/Hooks.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "@uniswap/v4-core/src/types/PoolId.sol";
import {BalanceDelta} from "@uniswap/v4-core/src/types/BalanceDelta.sol";
import {Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {SINCLending} from "../SINCLending.sol";

/// @title YieldWrapLendingHook — virtualizes lending-loop collateral as V4 liquidity
contract YieldWrapLendingHook is BaseHook, ReentrancyGuard {
    using SafeERC20 for IERC20;
    using PoolIdLibrary for PoolKey;

    SINCLending public immutable lending;
    IERC20 public immutable sinc;
    IERC20 public immutable usdc;
    address public guardian;
    mapping(PoolId => address) public yieldVaults;

    event VirtualizedPositionOpened(PoolId indexed poolId, address indexed user, uint256 amount);

    constructor(IPoolManager _manager, SINCLending _lending) BaseHook(_manager) {
        lending = _lending;
        sinc = _lending.SINC();
        usdc = _lending.USDC();
        guardian = msg.sender;
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

    function _beforeAddLiquidity(address, PoolKey calldata, IPoolManager.ModifyLiquidityParams calldata, bytes calldata)
        internal override returns (bytes4)
    {
        return BaseHook.beforeAddLiquidity.selector;
    }

    function _afterAddLiquidity(address, PoolKey calldata, IPoolManager.ModifyLiquidityParams calldata, BalanceDelta delta, BalanceDelta, bytes calldata)
        internal override returns (bytes4, BalanceDelta)
    {
        return (BaseHook.afterAddLiquidity.selector, delta);
    }

    function _beforeRemoveLiquidity(address, PoolKey calldata, IPoolManager.ModifyLiquidityParams calldata, bytes calldata)
        internal override returns (bytes4)
    {
        return BaseHook.beforeRemoveLiquidity.selector;
    }

    function _afterRemoveLiquidity(address, PoolKey calldata, IPoolManager.ModifyLiquidityParams calldata, BalanceDelta delta, BalanceDelta, bytes calldata)
        internal override returns (bytes4, BalanceDelta)
    {
        return (BaseHook.afterRemoveLiquidity.selector, delta);
    }

    function _beforeSwap(address, PoolKey calldata key, IPoolManager.SwapParams calldata, bytes calldata)
        internal override returns (bytes4, int128, uint24)
    {
        PoolId poolId = key.toId();
        address vault = yieldVaults[poolId];
        if (vault != address(0)) {
            emit YieldVaultUpdated(poolId, vault);
        }
        return (BaseHook.beforeSwap.selector, 0, 0);
    }

    function _afterSwap(address, PoolKey calldata key, IPoolManager.SwapParams calldata, BalanceDelta, bytes calldata)
        internal override returns (bytes4, int128)
    {
        PoolId poolId = key.toId();
        address vault = yieldVaults[poolId];
        if (vault != address(0)) {
            emit LiquidityReturned(poolId, vault, 0, 0, 0);
        }
        return (BaseHook.afterSwap.selector, 0);
    }
}
