// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {SINCLending} from "../src/SINCLending.sol";
import {MockERC20} from "./mocks/MockERC20.sol";
import {MockOracle, MockSwapRouter} from "./mocks/MockLoop.sol";

contract SINCLendingTest is Test {
    SINCLending lending;
    MockERC20 sinc;
    MockERC20 usdc;
    MockOracle oracle;
    MockSwapRouter router;

    address guardian = makeAddr("guardian");
    address treasury = 0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac; // canonical SINCOR2 treasury
    address supplier = makeAddr("supplier");
    address looper = makeAddr("looper");
    address liquidator = makeAddr("liquidator");

    uint256 constant ONE_DOLLAR = 1e6;

    function setUp() public {
        sinc = new MockERC20("SINC", "SINC", 18);
        usdc = new MockERC20("USD Coin", "USDC", 6);
        oracle = new MockOracle(ONE_DOLLAR);
        router = new MockSwapRouter(sinc, usdc, oracle);
        lending = new SINCLending(sinc, usdc, oracle, router, guardian, treasury);

        // seed router liquidity for loop swaps
        sinc.mint(address(router), 10_000_000e18);
        usdc.mint(address(router), 10_000_000e6);

        usdc.mint(supplier, 5_000_000e6);
        sinc.mint(looper, 1_000_000e18);
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

    // --------------------------------------------------------------- supply side

    function test_supplyAndRedeemWithInterest() public {
        // create borrow demand so interest accrues
        vm.startPrank(looper);
        lending.depositCollateral(100_000e18); // $100k at floor
        lending.borrow(50_000e6);
        vm.stopPrank();

        vm.warp(block.timestamp + 365 days);
        lending.accrueInterest();

        uint256 bal = lending.supplyBalanceUSDC(supplier);
        assertGt(bal, 1_000_000e6); // suppliers earned

        uint256 shares = lending.supplyShares(supplier);
        vm.prank(supplier);
        lending.redeemUSDC(shares / 2);
        assertGt(usdc.balanceOf(supplier), 4_000_000e6);
    }

    function test_reservesFlowToTreasury() public {
        vm.startPrank(looper);
        lending.depositCollateral(100_000e18);
        lending.borrow(50_000e6);
        vm.stopPrank();

        vm.warp(block.timestamp + 365 days);
        lending.sweepReserves();
        assertGt(usdc.balanceOf(treasury), 0);
    }

    // --------------------------------------------------------------- borrow side

    function test_borrowAndHealthFactor() public {
        vm.startPrank(looper);
        lending.depositCollateral(10_000e18); // $10k
        lending.borrow(7_000e6);
        vm.stopPrank();

        // HF = 10_000 * 0.8 / 7_000 ≈ 1.142
        assertApproxEqAbs(lending.healthFactor(looper), 1.142e18, 0.01e18);
    }

    function test_borrowBeyondThresholdReverts() public {
        vm.startPrank(looper);
        lending.depositCollateral(10_000e18);
        vm.expectRevert(SINCLending.UnhealthyPosition.selector);
        lending.borrow(8_001e6); // > 80% of $10k
        vm.stopPrank();
    }

    function test_repayRoundsUpObligation() public {
        vm.startPrank(looper);
        lending.depositCollateral(10_000e18);
        lending.borrow(5_000e6);
        vm.warp(block.timestamp + 30 days);
        uint256 debt = lending.borrowBalance(looper);
        usdc.mint(looper, 1_000e6);
        lending.repay(debt);
        vm.stopPrank();
        assertEq(lending.borrowSharesOf(looper), 0);
    }

    // --------------------------------------------------------------- price floor

    function test_priceFloorHoldsCollateralValue() public {
        oracle.setPrice(0.5e6); // oracle says 50 cents
        assertEq(lending.collateralPrice(), ONE_DOLLAR); // floor defends $1 valuation

        vm.prank(guardian);
        lending.setPriceFloor(0); // disable floor → pure oracle
        assertEq(lending.collateralPrice(), 0.5e6);
    }

    // --------------------------------------------------------------- liquidation

    function test_liquidationFlow() public {
        vm.startPrank(looper);
        lending.depositCollateral(10_000e18);
        lending.borrow(7_000e6);
        vm.stopPrank();

        // healthy positions can't be liquidated
        vm.prank(liquidator);
        vm.expectRevert();
        lending.liquidate(looper, 1_000e6);

        // break the floor and crash price → position goes underwater
        vm.prank(guardian);
        lending.setPriceFloor(0);
        oracle.setPrice(0.7e6);

        uint256 hfBefore = lending.healthFactor(looper);
        assertLt(hfBefore, 1e18);

        uint256 sincBefore = sinc.balanceOf(liquidator);
        vm.prank(liquidator);
        lending.liquidate(looper, 3_500e6); // max close factor = 50%

        // seized 3500 * 1.05 / 0.70 = 5250 SINC
        assertEq(sinc.balanceOf(liquidator) - sincBefore, 5_250e18);
        assertEq(lending.borrowBalance(looper), 3_500e6 + 1); // +1 wei roundup dust
    }

    // --------------------------------------------------------------- loops

    function test_openLoopConservativeLeverage() public {
        uint256 initial = 10_000e18; // $10k
        vm.prank(looper);
        lending.openLoop(0, initial, 2); // CONSERVATIVE

        // λ=0.5, 2 loops: collateral = C·(1+λ+λ²) = 1.75·C minus swap fees (30bps per hop)
        uint256 coll = lending.collateralOf(looper);
        assertGt(coll, 17_000e18);   // ≈1.75x gross, less fees
        assertLt(coll, 17_500e18);
        assertGe(lending.healthFactor(looper), 1e18);

        uint256 debt = lending.borrowBalance(looper);
        assertGt(debt, 7_000e6);     // ≈ C·(λ+λ²) = $7.5k less fee effects
        assertLt(debt, 7_600e6);
    }

    function test_openLoopRespectsVariantCaps() public {
        vm.prank(looper);
        vm.expectRevert(SINCLending.LoopLimitExceeded.selector);
        lending.openLoop(0, 10_000e18, 3); // CONSERVATIVE maxLoops = 2
    }

    function test_aggressiveLoopHigherLeverageThanBalanced() public {
        address l2 = makeAddr("looper2");
        sinc.mint(l2, 1_000_000e18);
        vm.prank(l2);
        sinc.approve(address(lending), type(uint256).max);

        vm.prank(looper);
        lending.openLoop(1, 10_000e18, 3); // BALANCED
        vm.prank(l2);
        lending.openLoop(2, 10_000e18, 4); // AGGRESSIVE

        assertGt(lending.collateralOf(l2), lending.collateralOf(looper));
        assertGe(lending.healthFactor(l2), 1e18);
        assertGe(lending.healthFactor(looper), 1e18);
    }

    function test_closeLoopUnwindsFully() public {
        vm.prank(looper);
        lending.openLoop(1, 10_000e18, 3); // BALANCED

        uint256 sincBefore = sinc.balanceOf(looper);
        vm.prank(looper);
        lending.closeLoop();

        assertLe(lending.borrowBalance(looper), 1); // dust-tolerant
        assertEq(lending.collateralOf(looper), 0);
        // got most collateral back (lost ~0.6% to two-way swap fees)
        uint256 returned = sinc.balanceOf(looper) - sincBefore;
        assertGt(returned, 9_000e18);
        assertLt(returned, 10_000e18);
    }

    // --------------------------------------------------------------- ROI simulation

    function test_simulateLoopROI_zeroPriceChangeLosesBorrowCost() public {
        (uint256 leverageBps, int256 roiBps, uint256 borrowCostBps) =
            lending.simulateLoopROI(10_000e6, 0, 0, 30); // CONSERVATIVE

        // leverage = 1 + 0.5 + 0.25 = 1.75x
        assertEq(leverageBps, 17_500);
        assertGt(borrowCostBps, 0);
        assertEq(roiBps, -int256(borrowCostBps)); // no price move → pure cost
    }

    function test_simulateLoopROI_priceGainAmplified() public {
        (, int256 roiBps, uint256 borrowCostBps) =
            lending.simulateLoopROI(10_000e6, 0, 1_000, 30); // CONSERVATIVE, +10% SINC

        // +10% at 1.75x = +1750bps gross, minus borrow cost
        assertEq(roiBps, 1_750 - int256(borrowCostBps));
    }

    function test_simulateLoopROI_variantOrdering() public {
        (uint256 levC,,) = lending.simulateLoopROI(10_000e6, 0, 0, 30);
        (uint256 levB,,) = lending.simulateLoopROI(10_000e6, 1, 0, 30);
        (uint256 levA,,) = lending.simulateLoopROI(10_000e6, 2, 0, 30);
        assertTrue(levC < levB && levB < levA);
        assertEq(levC, 17_500);   // 1.75x
        assertEq(levB, 23_471);   // 1 + .65 + .4225 + .274625
        assertEq(levA, 30_507);   // 1 + .75 + .5625 + .421875 + .31640625
    }

    // --------------------------------------------------------------- fuzz

    function testFuzz_loopAlwaysHealthy(uint96 amountRaw, uint8 loopsRaw) public {
        uint256 amount = 1_000e18 + uint256(amountRaw) % 100_000e18;
        uint256 loops = 1 + uint256(loopsRaw) % 4;
        vm.prank(looper);
        lending.openLoop(2, amount, loops); // AGGRESSIVE
        assertGe(lending.healthFactor(looper), 1e18);
    }

    function testFuzz_borrowNeverExceedsThreshold(uint96 collRaw, uint96 borrowRaw) public {
        uint256 coll = 1_000e18 + uint256(collRaw) % 200_000e18;
        vm.startPrank(looper);
        lending.depositCollateral(coll);
        uint256 maxBorrow = lending.sincValueUSDC(coll) * 8_000 / 10_000;
        uint256 want = uint256(borrowRaw) % (maxBorrow + 10_000e6);
        if (want <= maxBorrow && want > 0) {
            lending.borrow(want);
            assertGe(lending.healthFactor(looper), 1e18);
        } else if (want > maxBorrow) {
            vm.expectRevert(SINCLending.UnhealthyPosition.selector);
            lending.borrow(want);
        }
        vm.stopPrank();
    }
}
