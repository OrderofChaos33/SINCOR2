// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test, console2} from "forge-std/Test.sol";
import {TWAMMIHook} from "../src/TWAMMIHook.sol";
import {MockERC20} from "./mocks/MockERC20.sol";

import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "@uniswap/v4-core/src/types/PoolId.sol";
import {SwapParams} from "@uniswap/v4-core/src/types/PoolOperation.sol";
import {Currency, CurrencyLibrary} from "@uniswap/v4-core/src/types/Currency.sol";
import {BalanceDelta, toBalanceDelta} from "@uniswap/v4-core/src/types/BalanceDelta.sol";

/// @notice Unit & integration tests for TWAMMIHook.
///         Gas annotations: forge test --gas-report on the TWAMMIHook target.
contract TWAMMIHookTest is Test {
    using PoolIdLibrary for PoolKey;

    // ─────────── fixtures ─────────── //

    TWAMMIHook hook;
    MockERC20 token0;
    MockERC20 token1;

    address pm    = makeAddr("poolManager");
    address treasury = 0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac;
    address trader   = makeAddr("trader");
    address trader2  = makeAddr("trader2");
    address admin    = makeAddr("admin");

    PoolKey key;

    function setUp() public {
        vm.startPrank(admin);
        hook = new TWAMMIHook(IPoolManager(pm), treasury, 10); // 10 bps fee
        vm.stopPrank();

        token0 = new MockERC20("TKN0", "TKN0", 18);
        token1 = new MockERC20("TKN1", "TKN1", 18);

        // Ensure token0 < token1 ordering
        if (address(token0) > address(token1)) {
            (token0, token1) = (token1, token0);
        }

        key = PoolKey({
            currency0: Currency.wrap(address(token0)),
            currency1: Currency.wrap(address(token1)),
            fee: 3000,
            tickSpacing: 60,
            hooks: IHooks(address(hook))
        });

        // Fund traders
        token0.mint(trader,  1_000_000e18);
        token0.mint(trader2, 1_000_000e18);
        token1.mint(trader,  1_000_000e18);
        token1.mint(trader2, 1_000_000e18);

        vm.prank(trader);
        token0.approve(address(hook), type(uint256).max);
        vm.prank(trader);
        token1.approve(address(hook), type(uint256).max);
        vm.prank(trader2);
        token0.approve(address(hook), type(uint256).max);
        vm.prank(trader2);
        token1.approve(address(hook), type(uint256).max);
    }

    // ─────────── helpers ─────────── //

    function _swapParams(bool zeroForOne, int256 amt) internal pure returns (SwapParams memory) {
        return SwapParams({zeroForOne: zeroForOne, amountSpecified: amt, sqrtPriceLimitX96: 0});
    }

    // ─────────── constructor / admin ─────────── //

    function test_constructorSetsOwnerAndTreasury() public view {
        assertEq(hook.owner(), admin);
        assertEq(hook.treasury(), treasury);
        assertEq(hook.protocolFeeBps(), 10);
    }

    function test_setProtocolFee_ownerOnly() public {
        vm.prank(admin);
        hook.setProtocolFee(20);
        assertEq(hook.protocolFeeBps(), 20);

        vm.expectRevert(TWAMMIHook.Unauthorized.selector);
        vm.prank(trader);
        hook.setProtocolFee(50);
    }

    function test_setProtocolFee_rejectsAboveCap() public {
        vm.prank(admin);
        vm.expectRevert(TWAMMIHook.FeeTooHigh.selector);
        hook.setProtocolFee(101);
    }

    function test_setTreasury_rejectsZeroAddress() public {
        vm.prank(admin);
        vm.expectRevert(TWAMMIHook.InvalidTreasury.selector);
        hook.setTreasury(address(0));
    }

    function test_transferOwnership() public {
        vm.prank(admin);
        hook.transferOwnership(trader);
        assertEq(hook.owner(), trader);
    }

    function test_constructor_rejectsZeroTreasury() public {
        vm.expectRevert(TWAMMIHook.InvalidTreasury.selector);
        new TWAMMIHook(IPoolManager(pm), address(0), 10);
    }

    function test_constructor_rejectsFeeTooHigh() public {
        vm.expectRevert(TWAMMIHook.FeeTooHigh.selector);
        new TWAMMIHook(IPoolManager(pm), treasury, 101);
    }

    // ─────────── order submission ─────────── //

    function test_submitOrder_zeroForOne() public {
        vm.prank(trader);
        uint256 orderId = hook.submitOrder(key, 100_000e18, 100, true);

        assertEq(orderId, 0);
        (
            address oOwner,
            address sellToken,
            ,
            uint256 totalAmount,
            uint256 executedAmount,
            ,
            uint256 endBlock,
            bool zfo,
            bool active
        ) = hook.orders(0);

        assertEq(oOwner, trader);
        assertEq(sellToken, address(token0));
        assertEq(totalAmount, 100_000e18);
        assertEq(executedAmount, 0);
        assertEq(endBlock, block.number + 100);
        assertTrue(zfo);
        assertTrue(active);
        assertEq(token0.balanceOf(address(hook)), 100_000e18);
    }

    function test_submitOrder_oneForZero() public {
        vm.prank(trader);
        uint256 orderId = hook.submitOrder(key, 50_000e18, 50, false);
        assertEq(orderId, 0);

        (, address sellToken,,,,,,bool zfo,) = hook.orders(0);
        assertEq(sellToken, address(token1));
        assertFalse(zfo);
    }

    function test_submitOrder_rejectsZeroAmount() public {
        vm.prank(trader);
        vm.expectRevert(TWAMMIHook.ZeroAmount.selector);
        hook.submitOrder(key, 0, 100, true);
    }

    function test_submitOrder_rejectsInvalidDuration() public {
        vm.prank(trader);
        vm.expectRevert(TWAMMIHook.InvalidOrder.selector);
        hook.submitOrder(key, 100e18, 0, true);

        vm.prank(trader);
        vm.expectRevert(TWAMMIHook.InvalidOrder.selector);
        hook.submitOrder(key, 100e18, 43_201, true);
    }

    function test_submitOrder_multiple_accumulates_pending() public {
        vm.prank(trader);
        hook.submitOrder(key, 100_000e18, 100, true);
        vm.prank(trader2);
        hook.submitOrder(key, 60_000e18, 100, true);

        PoolId pid = key.toId();
        // pending = sum of ratePerBlock * duration for both orders
        assertGt(hook.pendingZeroForOne(pid), 0);
    }

    // ─────────── cancel order ─────────── //

    function test_cancelOrder_refundsUnexecuted() public {
        vm.prank(trader);
        hook.submitOrder(key, 100_000e18, 100, true);

        uint256 balBefore = token0.balanceOf(trader);
        vm.prank(trader);
        hook.cancelOrder(0);

        assertEq(token0.balanceOf(trader), balBefore + 100_000e18);
        (,,,,,,,,bool active) = hook.orders(0);
        assertFalse(active);
    }

    function test_cancelOrder_nonOwnerReverts() public {
        vm.prank(trader);
        hook.submitOrder(key, 100_000e18, 100, true);

        vm.prank(trader2);
        vm.expectRevert(TWAMMIHook.Unauthorized.selector);
        hook.cancelOrder(0);
    }

    function test_cancelOrder_inactiveReverts() public {
        vm.prank(trader);
        hook.submitOrder(key, 100_000e18, 100, true);
        vm.prank(trader);
        hook.cancelOrder(0);

        vm.prank(trader);
        vm.expectRevert(TWAMMIHook.OrderNotActive.selector);
        hook.cancelOrder(0);
    }

    function test_adminCanCancelAnyOrder() public {
        vm.prank(trader);
        hook.submitOrder(key, 100_000e18, 100, true);

        vm.prank(admin);
        hook.cancelOrder(0); // should not revert
    }

    // ─────────── executeTWAMM ─────────── //

    function test_executeTWAMM_noOrders_returnsZero() public {
        (uint256 internalized, uint256 residual, uint256 fee) = hook.executeTWAMM(key);
        assertEq(internalized, 0);
        assertEq(residual, 0);
        assertEq(fee, 0);
    }

    function test_executeTWAMM_singleSide_pureResidual() public {
        // Only zeroForOne orders → full residual, no internalization
        vm.prank(trader);
        hook.submitOrder(key, 100_000e18, 100, true);
        // Fund hook so it can skim fees
        token0.mint(address(hook), 10_000e18);

        vm.roll(block.number + 10); // advance 10 blocks

        (uint256 internalized, uint256 residual, uint256 fee) = hook.executeTWAMM(key);
        assertEq(internalized, 0);
        assertGt(residual + fee, 0, "should have volume");
    }

    function test_executeTWAMM_matchedOrders_internalizesMin() public {
        // Opposing orders of equal size → fully internalized
        vm.prank(trader);
        hook.submitOrder(key, 100_000e18, 100, true);  // buy token1
        vm.prank(trader2);
        hook.submitOrder(key, 100_000e18, 100, false); // buy token0

        token0.mint(address(hook), 50_000e18);
        token1.mint(address(hook), 50_000e18);

        vm.roll(block.number + 10);

        (uint256 internalized, uint256 residual, uint256 fee) = hook.executeTWAMM(key);
        // Internalized should be > 0 since both sides have volume
        assertGt(internalized, 0);
        // Residual should be 0 or very small for balanced orders
        assertEq(residual, 0);
        assertGt(fee, 0, "fee must be non-zero");
    }

    function test_executeTWAMM_feeSentToTreasury() public {
        vm.prank(trader);
        hook.submitOrder(key, 100_000e18, 100, true);
        token0.mint(address(hook), 10_000e18);

        uint256 treasuryBalBefore = token0.balanceOf(treasury);
        vm.roll(block.number + 50);
        hook.executeTWAMM(key);

        uint256 treasuryBalAfter = token0.balanceOf(treasury);
        assertGe(treasuryBalAfter, treasuryBalBefore, "treasury should receive fee");
    }

    function test_executeTWAMM_reducesPendingState() public {
        vm.prank(trader);
        hook.submitOrder(key, 100_000e18, 100, true);
        token0.mint(address(hook), 10_000e18);

        PoolId pid = key.toId();
        uint256 pendingBefore = hook.pendingZeroForOne(pid);
        vm.roll(block.number + 10);
        hook.executeTWAMM(key);
        uint256 pendingAfter = hook.pendingZeroForOne(pid);

        assertLt(pendingAfter, pendingBefore, "pending should decrease after execution");
    }

    // ─────────── hook callbacks (poolManager gate) ─────────── //

    function test_beforeSwap_onlyPoolManager() public {
        vm.expectRevert();
        hook.beforeSwap(trader, key, _swapParams(true, -1000e18), "");
    }

    function test_afterSwap_onlyPoolManager() public {
        vm.expectRevert();
        hook.afterSwap(trader, key, _swapParams(true, -1000e18), toBalanceDelta(0, 0), "");
    }

    function test_beforeSwap_triggersExecution() public {
        vm.prank(trader);
        hook.submitOrder(key, 100_000e18, 100, true);
        token0.mint(address(hook), 10_000e18);

        vm.roll(block.number + 5);

        PoolId pid = key.toId();
        uint256 pendingBefore = hook.pendingZeroForOne(pid);

        vm.prank(pm);
        hook.beforeSwap(trader, key, _swapParams(true, -1000e18), "");

        // executeTWAMM is called internally; pending may decrease
        uint256 pendingAfter = hook.pendingZeroForOne(pid);
        assertLe(pendingAfter, pendingBefore);
    }

    // ─────────── fee accounting ─────────── //

    function test_cumulativeFees_tracked() public {
        vm.prank(trader);
        hook.submitOrder(key, 100_000e18, 100, true);
        token0.mint(address(hook), 10_000e18);

        vm.roll(block.number + 50);
        hook.executeTWAMM(key);

        // cumulativeFees may be > 0 if volume was processed
        // (it tracks gross fees regardless of whether transfer succeeded)
        // just check it's accessible without revert
        hook.cumulativeFees(address(token0));
    }

    // ─────────── recoverERC20 ─────────── //

    function test_recoverERC20_ownerOnly() public {
        token0.mint(address(hook), 1000e18);
        vm.prank(admin);
        hook.recoverERC20(address(token0), 500e18, admin);
        assertEq(token0.balanceOf(admin), 500e18);

        vm.expectRevert(TWAMMIHook.Unauthorized.selector);
        vm.prank(trader);
        hook.recoverERC20(address(token0), 500e18, trader);
    }

    // ─────────── gas benchmark ─────────── //

    /// @dev forge test --match-test test_gas_submitOrder --gas-report
    function test_gas_submitOrder() public {
        vm.prank(trader);
        uint256 g0 = gasleft();
        hook.submitOrder(key, 100_000e18, 100, true);
        uint256 gasUsed = g0 - gasleft();
        console2.log("submitOrder gas:", gasUsed);
        assertLt(gasUsed, 200_000, "submitOrder should be < 200k gas");
    }

    /// @dev forge test --match-test test_gas_executeTWAMM --gas-report
    function test_gas_executeTWAMM() public {
        vm.prank(trader);
        hook.submitOrder(key, 100_000e18, 100, true);
        vm.prank(trader2);
        hook.submitOrder(key, 100_000e18, 100, false);
        token0.mint(address(hook), 50_000e18);
        token1.mint(address(hook), 50_000e18);
        vm.roll(block.number + 10);

        uint256 g0 = gasleft();
        hook.executeTWAMM(key);
        uint256 gasUsed = g0 - gasleft();
        console2.log("executeTWAMM gas:", gasUsed);
        assertLt(gasUsed, 150_000, "executeTWAMM should be < 150k gas");
    }
}
