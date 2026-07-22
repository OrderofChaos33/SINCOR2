// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {PoolManager} from "v4-core/src/PoolManager.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency, CurrencyLibrary} from "v4-core/src/types/Currency.sol";
import {TickMath} from "v4-core/src/libraries/TickMath.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {Hooks} from "v4-core/src/libraries/Hooks.sol";
import {LiquidityAmounts} from "v4-periphery/src/libraries/LiquidityAmounts.sol";

import {AutoCapitalizeMonetizeHook} from "../src/hooks/AutoCapitalizeMonetizeHook.sol";
import {MockERC20} from "./mocks/MockERC20.sol";
import {MockSwapRouter} from "./mocks/MockSwapRouter.sol";

contract AutoCapitalizeMonetizeHookTest is Test {
    PoolManager manager;
    AutoCapitalizeMonetizeHook hook;
    MockERC20 token0;
    MockERC20 token1;
    MockSwapRouter router;

    address treasury = makeAddr("treasury");
    address stranger = makeAddr("stranger");

    PoolKey key;

    uint24 constant POOL_FEE = 3000;
    int24 constant TICK_SPACING = 60;
    uint160 constant SQRT_PRICE_1_1 = 79228162514264337593543950336;

    uint256 constant FEE_BPS = 25; // 0.25% protocol fee on unspecified side
    uint256 constant TREASURY_BPS = 3000; // 30% of swept -> treasury

    uint160 constant REQUIRED_FLAGS =
        uint160(Hooks.AFTER_SWAP_FLAG) | uint160(Hooks.AFTER_SWAP_RETURNS_DELTA_FLAG);
    uint160 constant ALL_HOOK_MASK = 0x3FFF;

    function setUp() public {
        manager = new PoolManager(address(this));

        // Mine hook address carrying exactly AFTER_SWAP | AFTER_SWAP_RETURNS_DELTA
        bytes32 initHash = keccak256(
            abi.encodePacked(
                type(AutoCapitalizeMonetizeHook).creationCode,
                abi.encode(address(manager), treasury, FEE_BPS, TREASURY_BPS)
            )
        );
        bytes32 salt;
        address predicted;
        for (uint256 i = 0;; i++) {
            salt = bytes32(i);
            predicted = vm.computeCreate2Address(salt, initHash, address(this));
            if (uint160(predicted) & ALL_HOOK_MASK == REQUIRED_FLAGS) break;
        }
        hook = new AutoCapitalizeMonetizeHook{salt: salt}(
            IPoolManager(address(manager)), treasury, FEE_BPS, TREASURY_BPS
        );
        assertEq(address(hook), predicted, "CREATE2 prediction");

        MockERC20 a = new MockERC20("Token A", "TKA", 18);
        MockERC20 b = new MockERC20("Token B", "TKB", 18);
        (token0, token1) = address(a) < address(b) ? (a, b) : (b, a);

        key = PoolKey({
            currency0: Currency.wrap(address(token0)),
            currency1: Currency.wrap(address(token1)),
            fee: POOL_FEE,
            tickSpacing: TICK_SPACING,
            hooks: IHooks(address(hook))
        });

        manager.initialize(key, SQRT_PRICE_1_1);

        router = new MockSwapRouter(address(manager));
        token0.mint(address(this), 1000 ether);
        token1.mint(address(this), 1000 ether);
        token0.approve(address(router), type(uint256).max);
        token1.approve(address(router), type(uint256).max);

        uint256 liquidity = LiquidityAmounts.getLiquidityForAmounts(
            SQRT_PRICE_1_1,
            TickMath.getSqrtPriceAtTick(-600),
            TickMath.getSqrtPriceAtTick(600),
            100 ether,
            100 ether
        );
        router.addLiquidity(key, -600, 600, liquidity);
    }

    function test_capturesFeeOnSwap_unspecifiedSide() public {
        uint256 amountIn = 10 ether;
        uint256 myToken1Before = token1.balanceOf(address(this));

        uint256 out = router.swapExactIn(key, true, amountIn); // token0 -> token1, exact-in

        // Fee taken on the output (unspecified) side
        uint256 expectedFee = (out * FEE_BPS) / (10_000 - FEE_BPS); // gross = net + fee
        uint256 hookBal = token1.balanceOf(address(hook));

        assertGt(hookBal, 0, "hook captured fees");
        assertEq(hook.totalCaptured(Currency.wrap(address(token1))), hookBal, "accounting matches balance");
        // Swapper received gross output minus hook fee
        assertEq(token1.balanceOf(address(this)) - myToken1Before, out, "router output realized");
        assertApproxEqAbs(hookBal, expectedFee, 2, "fee ~= bps of gross output");
    }

    function test_sweep_splitsTreasuryAndReserve() public {
        router.swapExactIn(key, true, 10 ether);

        Currency c1 = Currency.wrap(address(token1));
        uint256 captured = hook.totalCaptured(c1);
        uint256 expectedTreasury = (captured * TREASURY_BPS) / 10_000;

        hook.sweep(c1);

        assertEq(token1.balanceOf(treasury), expectedTreasury, "treasury got its split");
        assertEq(token1.balanceOf(address(hook)), captured - expectedTreasury, "reserve retained");
        assertEq(hook.totalSwept(c1), captured, "sweep accounted");
    }

    function test_releaseReserve_ownerOnly_paysTreasuryOnly() public {
        router.swapExactIn(key, true, 10 ether);
        Currency c1 = Currency.wrap(address(token1));
        hook.sweep(c1);
        uint256 reserve = token1.balanceOf(address(hook));

        vm.prank(stranger);
        vm.expectRevert(AutoCapitalizeMonetizeHook.Unauthorized.selector);
        hook.releaseReserve(c1, reserve);

        // Release is hard-wired to the governed treasury sink
        uint256 treasuryBefore = token1.balanceOf(treasury);
        hook.releaseReserve(c1, reserve);
        assertEq(token1.balanceOf(address(hook)), 0, "reserve released");
        assertEq(token1.balanceOf(treasury) - treasuryBefore, reserve, "treasury received reserve");
    }

    /// @dev Accept native output from native-pool swaps.
    receive() external payable {}

    function test_nativePool_capturesAndSweepsNativeFees() public {
        // Native/TKB pool: native (address 0) always sorts as currency0
        PoolKey memory nkey = PoolKey({
            currency0: Currency.wrap(address(0)),
            currency1: Currency.wrap(address(token1)),
            fee: POOL_FEE,
            tickSpacing: TICK_SPACING,
            hooks: IHooks(address(hook))
        });
        manager.initialize(nkey, SQRT_PRICE_1_1);

        vm.deal(address(this), 200 ether);
        uint256 liquidity = LiquidityAmounts.getLiquidityForAmounts(
            SQRT_PRICE_1_1,
            TickMath.getSqrtPriceAtTick(-600),
            TickMath.getSqrtPriceAtTick(600),
            100 ether, // native side
            100 ether  // token1 side
        );
        router.addLiquidity{value: 120 ether}(nkey, -600, 600, liquidity);

        // Swap token1 -> native (exact-in): protocol fee lands on the NATIVE output
        router.swapExactIn(nkey, false, 10 ether);

        Currency nativeCur = Currency.wrap(address(0));
        uint256 captured = hook.totalCaptured(nativeCur);
        assertGt(captured, 0, "hook captured native fees");
        assertEq(captured, address(hook).balance, "native accounting matches balance");

        uint256 treasuryBefore = treasury.balance;
        hook.sweep(nativeCur);

        uint256 expectedTreasury = (captured * TREASURY_BPS) / 10_000;
        assertEq(treasury.balance - treasuryBefore, expectedTreasury, "treasury got native split");
        assertEq(address(hook).balance, captured - expectedTreasury, "native reserve retained");

        // Native reserve releases to treasury only
        hook.releaseReserve(nativeCur, captured - expectedTreasury);
        assertEq(address(hook).balance, 0, "native reserve released");
    }

    function test_adminGates() public {
        vm.prank(stranger);
        vm.expectRevert(AutoCapitalizeMonetizeHook.Unauthorized.selector);
        hook.setFeeBps(50);

        vm.expectRevert(AutoCapitalizeMonetizeHook.FeeTooHigh.selector);
        hook.setFeeBps(1001);

        hook.setFeeBps(50);
        assertEq(hook.feeBps(), 50);
    }
}
