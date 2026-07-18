// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {SINCLending} from "../src/SINCLending.sol";
import {MockERC20} from "./mocks/MockERC20.sol";
import {MockOracle, MockSwapRouter} from "./mocks/MockLoop.sol";

/// @notice Pins behavior against the REAL SINC v3 token spec: 8 decimals, 100M supply (Base).
///         The lending market must value collateral identically regardless of token decimals —
///         10_000 SINC at $1 is $10k whether the token is 8dp or 18dp.
contract SINCLendingDecimalsTest is Test {
    SINCLending lending;
    MockERC20 sinc; // 8 decimals — canonical SINC v3 spec
    MockERC20 usdc;
    MockOracle oracle;
    MockSwapRouter router;

    address guardian = makeAddr("guardian");
    address treasury = 0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac;
    address supplier = makeAddr("supplier");
    address looper = makeAddr("looper");
    address liquidator = makeAddr("liquidator");

    uint256 constant ONE_DOLLAR = 1e6;

    function setUp() public {
        sinc = new MockERC20("SINC v3", "SINC", 8); // real token spec
        usdc = new MockERC20("USD Coin", "USDC", 6);
        oracle = new MockOracle(ONE_DOLLAR);
        router = new MockSwapRouter(sinc, usdc, oracle);
        lending = new SINCLending(sinc, usdc, oracle, router, guardian, treasury);

        sinc.mint(address(router), 10_000_000e8);
        usdc.mint(address(router), 10_000_000e6);

        usdc.mint(supplier, 5_000_000e6);
        sinc.mint(looper, 1_000_000e8);
        usdc.mint(liquidator, 1_000_000e6);

        vm.prank(supplier);
        usdc.approve(address(lending), type(uint256).max);
        vm.startPrank(looper);
        sinc.approve(address(lending), type(uint256).max);
        usdc.approve(address(lending), type(uint256).max);
        vm.stopPrank();
        vm.prank(liquidator);
        usdc.approve(address(lending), type(uint256).max);

        vm.prank(supplier);
        lending.supplyUSDC(1_000_000e6);
    }

    // --------------------------------------------------------------- decimal detection

    function test_sincUnitMatchesTokenDecimals() public {
        assertEq(lending.sincUnit(), 1e8);
        assertEq(sinc.decimals(), 8);
    }

    function test_constructorRejectsExoticDecimals() public {
        MockERC20 exotic = new MockERC20("X", "X", 19);
        vm.expectRevert(SINCLending.InvalidConfig.selector);
        new SINCLending(exotic, usdc, oracle, router, guardian, treasury);
    }

    // --------------------------------------------------------------- valuation parity

    function test_collateralValuationWith8Decimals() public {
        // 10_000 SINC (8dp) at $1.00 floor == $10_000 USDC (6dp)
        assertEq(lending.sincValueUSDC(10_000e8), 10_000e6);
        assertEq(lending.sincValueUSDC(1e8), 1e6); // 1 whole SINC = $1
    }

    function test_borrowAndHealthFactor8Decimals() public {
        vm.startPrank(looper);
        lending.depositCollateral(10_000e8); // $10k
        lending.borrow(7_000e6);
        vm.stopPrank();

        // identical HF to the 18-decimal suite: 10_000 * 0.8 / 7_000 ≈ 1.142
        assertApproxEqAbs(lending.healthFactor(looper), 1.142e18, 0.01e18);
    }

    function test_liquidationSeizesCorrect8DecimalAmount() public {
        vm.startPrank(looper);
        lending.depositCollateral(10_000e8);
        lending.borrow(7_000e6);
        vm.stopPrank();

        vm.prank(guardian);
        lending.setPriceFloor(0);
        oracle.setPrice(0.7e6);

        uint256 sincBefore = sinc.balanceOf(liquidator);
        vm.prank(liquidator);
        lending.liquidate(looper, 3_500e6);

        // seized 3500 * 1.05 / 0.70 = 5250 SINC — in 8-decimal raw units
        assertEq(sinc.balanceOf(liquidator) - sincBefore, 5_250e8);
        assertEq(lending.borrowBalance(looper), 3_500e6 + 1);
    }

    // --------------------------------------------------------------- loops with 8 decimals

    function test_openCloseLoop8Decimals() public {
        vm.prank(looper);
        lending.openLoop(0, 10_000e8, 2); // CONSERVATIVE

        uint256 coll = lending.collateralOf(looper);
        assertGt(coll, 17_000e8); // same 1.75x leverage, 8dp units
        assertLt(coll, 17_500e8);
        assertGe(lending.healthFactor(looper), 1e18);

        uint256 sincBefore = sinc.balanceOf(looper);
        vm.prank(looper);
        lending.closeLoop();

        assertLe(lending.borrowBalance(looper), 1);
        assertEq(lending.collateralOf(looper), 0);
        uint256 returned = sinc.balanceOf(looper) - sincBefore;
        assertGt(returned, 9_000e8);
        assertLt(returned, 10_000e8);
    }

    // --------------------------------------------------------------- fuzz parity

    function testFuzz_loopAlwaysHealthy8Decimals(uint96 amountRaw, uint8 loopsRaw) public {
        uint256 amount = 1_000e8 + uint256(amountRaw) % 100_000e8;
        uint256 loops = 1 + uint256(loopsRaw) % 4;
        vm.prank(looper);
        lending.openLoop(2, amount, loops); // AGGRESSIVE
        assertGe(lending.healthFactor(looper), 1e18);
    }
}
