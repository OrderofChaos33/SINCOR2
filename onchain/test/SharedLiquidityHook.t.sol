// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {SharedLiquidityVault} from "../src/SharedLiquidityVault.sol";
import {SharedLiquidityHook} from "../src/SharedLiquidityHook.sol";
import {MockERC20} from "./mocks/MockERC20.sol";

import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "@uniswap/v4-core/src/types/PoolId.sol";
import {SwapParams} from "@uniswap/v4-core/src/types/PoolOperation.sol";
import {Currency, CurrencyLibrary} from "@uniswap/v4-core/src/types/Currency.sol";
import {BalanceDelta, toBalanceDelta} from "@uniswap/v4-core/src/types/BalanceDelta.sol";

/// @notice Hook↔vault integration tests driven by a mock PoolManager caller.
contract SharedLiquidityHookTest is Test {
    using PoolIdLibrary for PoolKey;

    SharedLiquidityVault vault;
    SharedLiquidityHook hook;
    MockERC20 sinc;
    MockERC20 usdc;

    address guardian = makeAddr("guardian");
    address treasury = 0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac;
    address pm = makeAddr("poolManager");
    address lpA = makeAddr("lpA");
    address lpB = makeAddr("lpB");
    address trader = makeAddr("trader");

    uint256 strategyId;
    PoolKey key;

    function setUp() public {
        sinc = new MockERC20("SINC", "SINC", 18);
        usdc = new MockERC20("USD Coin", "USDC", 6);
        vault = new SharedLiquidityVault(sinc, usdc, guardian, treasury);
        hook = new SharedLiquidityHook(IPoolManager(pm), vault, treasury, 10);

        vm.prank(guardian);
        strategyId = vault.registerStrategy(address(hook), lpA, 0, 0);

        (Currency c0, Currency c1) = address(sinc) < address(usdc)
            ? (Currency.wrap(address(sinc)), Currency.wrap(address(usdc)))
            : (Currency.wrap(address(usdc)), Currency.wrap(address(sinc)));
        key = PoolKey({currency0: c0, currency1: c1, fee: 3000, tickSpacing: 60, hooks: IHooks(address(hook))});

        hook.registerPoolStrategy(key, strategyId);

        usdc.mint(lpA, 500_000e6);
        usdc.mint(lpB, 500_000e6);
        sinc.mint(lpA, 500_000e18);
        vm.prank(lpA);
        usdc.approve(address(vault), type(uint256).max);
        vm.prank(lpA);
        sinc.approve(address(vault), type(uint256).max);
        vm.prank(lpB);
        usdc.approve(address(vault), type(uint256).max);
    }

    function _fundLpA() internal {
        vm.startPrank(lpA);
        vault.deposit(address(usdc), 200_000e6);
        vault.allocateVirtual(strategyId, address(usdc), 200_000e6);
        vm.stopPrank();
    }

    function _swapParams(bool zeroForOne, int256 amountSpecified) internal pure returns (SwapParams memory) {
        return SwapParams({zeroForOne: zeroForOne, amountSpecified: amountSpecified, sqrtPriceLimitX96: 0});
    }

    function test_onlyPoolManagerCanCallHooks() public {
        vm.expectRevert(SharedLiquidityHook.Unauthorized.selector);
        hook.beforeSwap(trader, key, _swapParams(true, -1000e6), "");

        vm.expectRevert(SharedLiquidityHook.Unauthorized.selector);
        hook.afterSwap(trader, key, _swapParams(true, -1000e6), toBalanceDelta(0, 0), "");
    }

    function test_beforeSwapPullsOutputSideVirtualLiquidity() public {
        _fundLpA();
        bool usdcIs0 = Currency.unwrap(key.currency0) == address(usdc);
        bool zeroForOne = !usdcIs0; // selling SINC for USDC

        vm.prank(pm);
        hook.beforeSwap(trader, key, _swapParams(zeroForOne, -50_000e6), "");

        assertEq(vault.outstanding(lpA, strategyId, address(usdc)), 50_000e6);
        assertEq(usdc.balanceOf(address(hook)), 50_000e6);
    }

    function test_afterSwapSettlesPrincipalBackToVault() public {
        _fundLpA();
        bool usdcIs0 = Currency.unwrap(key.currency0) == address(usdc);
        bool zeroForOne = !usdcIs0;

        vm.prank(pm);
        hook.beforeSwap(trader, key, _swapParams(zeroForOne, -50_000e6), "");

        // swap consumes less than pulled; hook settles only what it actually holds
        BalanceDelta delta = usdcIs0 ? toBalanceDelta(-48_000e6, 50_000e6) : toBalanceDelta(50_000e6, -48_000e6);
        vm.prank(pm);
        hook.afterSwap(trader, key, _swapParams(zeroForOne, -50_000e6), delta, "");

        assertEq(vault.outstanding(lpA, strategyId, address(usdc)), 0);
        assertEq(usdc.balanceOf(address(hook)), 0);
        assertTrue(vault.checkInvariant());
    }

    function test_hookDataRoutesToSpecificLP() public {
        _fundLpA();
        vm.startPrank(lpB);
        vault.deposit(address(usdc), 100_000e6);
        vault.allocateVirtual(strategyId, address(usdc), 100_000e6);
        vm.stopPrank();

        bool usdcIs0 = Currency.unwrap(key.currency0) == address(usdc);
        bool zeroForOne = !usdcIs0;

        vm.prank(pm);
        hook.beforeSwap(trader, key, _swapParams(zeroForOne, -10_000e6), abi.encode(lpB));

        assertEq(vault.outstanding(lpB, strategyId, address(usdc)), 10_000e6);
        assertEq(vault.outstanding(lpA, strategyId, address(usdc)), 0);
    }

    function test_unregisteredPoolPassesThrough() public {
        _fundLpA();
        PoolKey memory otherKey = PoolKey({
            currency0: key.currency0,
            currency1: key.currency1,
            fee: 500,
            tickSpacing: 10,
            hooks: IHooks(address(hook))
        });
        vm.prank(pm);
        (bytes4 sel,,) = hook.beforeSwap(trader, otherKey, _swapParams(true, -10_000e6), "");
        assertEq(sel, IHooks.beforeSwap.selector);
        assertEq(usdc.balanceOf(address(hook)), 0);
    }

    function test_depletedLiquidityDoesNotRevertSwap() public {
        // no LPs funded — beforeSwap must not revert, just skip the pull
        vm.prank(pm);
        hook.beforeSwap(trader, key, _swapParams(true, -10_000e6), "");
        assertEq(usdc.balanceOf(address(hook)), 0);
    }

    function test_protocolFeeEmittedOnPositiveDelta() public {
        _fundLpA();
        BalanceDelta delta = toBalanceDelta(-48_000e6, 50_000e6);
        vm.expectEmit(true, true, true, true);
        emit SharedLiquidityHook.FeeCaptured(key.currency1, 50e6, treasury);
        vm.prank(pm);
        hook.afterSwap(trader, key, _swapParams(true, -48_000e6), delta, "");
    }

    function test_mevDonationRoutesToTreasury() public {
        uint256 before = treasury.balance;
        vm.deal(trader, 1 ether);
        vm.prank(trader);
        (bool ok,) = address(hook).call{value: 1 ether}("");
        assertTrue(ok);
        assertEq(treasury.balance, before + 1 ether);
        assertEq(hook.totalMEVDonated(), 1 ether);
    }
}
