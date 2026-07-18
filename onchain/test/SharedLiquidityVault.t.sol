// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {SharedLiquidityVault} from "../src/SharedLiquidityVault.sol";
import {MockERC20} from "./mocks/MockERC20.sol";

/// @notice Unit tests for Aqua/Fluid-style shared-liquidity virtual accounting.
contract SharedLiquidityVaultTest is Test {
    SharedLiquidityVault vault;
    MockERC20 sinc;
    MockERC20 usdc;

    address guardian = makeAddr("guardian");
    address treasury = 0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac; // canonical SINCOR2 treasury
    address lpA = makeAddr("lpA");
    address lpB = makeAddr("lpB");
    address hook = makeAddr("strategyHook");
    address hook2 = makeAddr("strategyHook2");

    uint256 constant SINC_AMT = 1_000_000e18;
    uint256 constant USDC_AMT = 1_000_000e6;

    function setUp() public {
        sinc = new MockERC20("SINC", "SINC", 18);
        usdc = new MockERC20("USD Coin", "USDC", 6);
        vault = new SharedLiquidityVault(sinc, usdc, guardian, treasury);

        sinc.mint(lpA, SINC_AMT);
        usdc.mint(lpA, USDC_AMT);
        sinc.mint(lpB, SINC_AMT);
        usdc.mint(lpB, USDC_AMT);
        vm.startPrank(lpA);
        sinc.approve(address(vault), type(uint256).max);
        usdc.approve(address(vault), type(uint256).max);
        vm.stopPrank();
        vm.startPrank(lpB);
        sinc.approve(address(vault), type(uint256).max);
        usdc.approve(address(vault), type(uint256).max);
        vm.stopPrank();
    }

    function _registerDefault() internal returns (uint256 sid) {
        vm.prank(guardian);
        sid = vault.registerStrategy(hook, treasury, 0, 0); // uncapped
    }

    function _depositA() internal {
        vm.startPrank(lpA);
        vault.deposit(address(sinc), 100_000e18);
        vault.deposit(address(usdc), 100_000e6);
        vm.stopPrank();
    }

    // --------------------------------------------------------------- deposits

    function test_depositCreditsRealBalance() public {
        _depositA();
        assertEq(vault.realBalance(lpA, address(sinc)), 100_000e18);
        assertEq(vault.realBalance(lpA, address(usdc)), 100_000e6);
        assertEq(vault.totalDepositsSINC(), 100_000e18);
        assertTrue(vault.checkInvariant());
    }

    function test_withdrawRespectsOutstanding() public {
        _depositA();
        uint256 sid = _registerDefault();
        vm.prank(lpA);
        vault.allocateVirtual(sid, address(usdc), 80_000e6);

        vm.prank(hook);
        vault.drawDown(sid, lpA, address(usdc), 30_000e6);

        vm.prank(lpA);
        vm.expectRevert(); // only 70k free
        vault.withdraw(address(usdc), 70_001e6);

        vm.prank(lpA);
        vault.withdraw(address(usdc), 70_000e6); // exactly the free balance works
        assertEq(vault.freeBalance(lpA, address(usdc)), 0);
        assertTrue(vault.checkInvariant());
    }

    // --------------------------------------------------------------- Aqua semantics: overlapping virtual allocations

    function test_overlappingVirtualAllocationsAllowed() public {
        _depositA();
        vm.startPrank(guardian);
        uint256 s1 = vault.registerStrategy(hook, treasury, 0, 0);
        uint256 s2 = vault.registerStrategy(hook2, treasury, 0, 0);
        vm.stopPrank();

        // Same 100k USDC committed to BOTH strategies — the Aqua capital-efficiency property
        vm.startPrank(lpA);
        vault.allocateVirtual(s1, address(usdc), 100_000e6);
        vault.allocateVirtual(s2, address(usdc), 100_000e6);
        vm.stopPrank();

        assertEq(vault.virtualAlloc(lpA, s1, address(usdc)), 100_000e6);
        assertEq(vault.virtualAlloc(lpA, s2, address(usdc)), 100_000e6);
        // Nothing moved: capital still fully withdrawable
        assertEq(vault.freeBalance(lpA, address(usdc)), 100_000e6);
    }

    function test_drawsAcrossStrategiesBoundedByRealCapital() public {
        _depositA();
        vm.startPrank(guardian);
        uint256 s1 = vault.registerStrategy(hook, treasury, 0, 0);
        uint256 s2 = vault.registerStrategy(hook2, treasury, 0, 0);
        vm.stopPrank();
        vm.startPrank(lpA);
        vault.allocateVirtual(s1, address(usdc), 100_000e6);
        vault.allocateVirtual(s2, address(usdc), 100_000e6);
        vm.stopPrank();

        vm.prank(hook);
        vault.drawDown(s1, lpA, address(usdc), 60_000e6);

        // s2 committed 100k but only 40k real remains — execution-time bound kicks in
        vm.prank(hook2);
        vm.expectRevert();
        vault.drawDown(s2, lpA, address(usdc), 40_001e6);

        vm.prank(hook2);
        vault.drawDown(s2, lpA, address(usdc), 40_000e6);
        assertEq(vault.lpOutstandingTotal(lpA, address(usdc)), 100_000e6);
        assertTrue(vault.checkInvariant());
    }

    // --------------------------------------------------------------- Fluid semantics: caps & settlement

    function test_strategyCapEnforced() public {
        _depositA();
        vm.prank(guardian);
        uint256 sid = vault.registerStrategy(hook, treasury, 0, 50_000e6);
        vm.prank(lpA);
        vault.allocateVirtual(sid, address(usdc), 100_000e6);

        vm.prank(hook);
        vm.expectRevert();
        vault.drawDown(sid, lpA, address(usdc), 50_001e6);

        vm.prank(hook);
        vault.drawDown(sid, lpA, address(usdc), 50_000e6);
    }

    function test_settleSplitsFeesToTreasuryAndLP() public {
        _depositA();
        uint256 sid = _registerDefault();
        vm.prank(lpA);
        vault.allocateVirtual(sid, address(usdc), 100_000e6);
        vm.prank(hook);
        vault.drawDown(sid, lpA, address(usdc), 50_000e6);

        // hook returns principal + 1,000 USDC gross fee, 10% protocol cut
        usdc.mint(hook, 51_000e6);
        vm.startPrank(hook);
        usdc.approve(address(vault), type(uint256).max);
        vault.settleUp(sid, lpA, address(usdc), 50_000e6, 1_000e6, 1_000);
        vm.stopPrank();

        assertEq(usdc.balanceOf(treasury), 100e6);             // 10% of 1,000
        assertEq(vault.accruedFees(lpA, address(usdc)), 900e6); // remainder to LP
        assertEq(vault.outstanding(lpA, sid, address(usdc)), 0);
        assertTrue(vault.checkInvariant());

        vm.prank(lpA);
        vault.harvestFees(address(usdc), lpA);
        assertEq(usdc.balanceOf(lpA), USDC_AMT - 100_000e6 + 900e6);
        assertTrue(vault.checkInvariant());
    }

    function test_onlyRegisteredHookCanDraw() public {
        _depositA();
        uint256 sid = _registerDefault();
        vm.prank(lpA);
        vault.allocateVirtual(sid, address(usdc), 10_000e6);

        vm.prank(hook2); // not the registered hook
        vm.expectRevert();
        vault.drawDown(sid, lpA, address(usdc), 1_000e6);
    }

    function test_deallocateBlockedBelowOutstanding() public {
        _depositA();
        uint256 sid = _registerDefault();
        vm.prank(lpA);
        vault.allocateVirtual(sid, address(usdc), 50_000e6);
        vm.prank(hook);
        vault.drawDown(sid, lpA, address(usdc), 20_000e6);

        vm.prank(lpA);
        vm.expectRevert(); // would drop allocation below the 20k drawn
        vault.deallocateVirtual(sid, address(usdc), 30_001e6);

        vm.prank(lpA);
        vault.deallocateVirtual(sid, address(usdc), 30_000e6); // leaves exactly the drawn amount
        assertEq(vault.virtualAlloc(lpA, sid, address(usdc)), 20_000e6);
    }

    // --------------------------------------------------------------- fuzz: invariant under random draw/settle sequences

    function testFuzz_invariantHoldsAcrossDrawSettle(uint96 draw1, uint96 draw2, uint96 fee) public {
        _depositA();
        uint256 sid = _registerDefault();
        vm.prank(lpA);
        vault.allocateVirtual(sid, address(usdc), 100_000e6);

        uint256 free = 100_000e6;
        uint256 d1 = uint256(draw1) % free;
        if (d1 > 0) {
            vm.prank(hook);
            vault.drawDown(sid, lpA, address(usdc), d1);
            free -= d1;
        }
        uint256 d2 = uint256(draw2) % (free + 1);
        if (d2 > 0) {
            vm.prank(hook);
            vault.drawDown(sid, lpA, address(usdc), d2);
        }
        uint256 drawn = vault.outstanding(lpA, sid, address(usdc));
        uint256 f = uint256(fee) % 10_000e6;
        usdc.mint(hook, drawn + f);
        vm.startPrank(hook);
        usdc.approve(address(vault), type(uint256).max);
        if (drawn + f > 0) vault.settleUp(sid, lpA, address(usdc), drawn, f, 500);
        vm.stopPrank();

        assertTrue(vault.checkInvariant());
        assertEq(vault.lpOutstandingTotal(lpA, address(usdc)), 0);
    }

    function test_pauseBlocksDrawsButNotSettleOrWithdraw() public {
        _depositA();
        uint256 sid = _registerDefault();
        vm.prank(lpA);
        vault.allocateVirtual(sid, address(usdc), 50_000e6);
        vm.prank(hook);
        vault.drawDown(sid, lpA, address(usdc), 10_000e6);

        vm.prank(guardian);
        vault.pause();

        vm.prank(hook);
        vm.expectRevert();
        vault.drawDown(sid, lpA, address(usdc), 1_000e6);

        // settlement still works while paused (capital can always come home)
        vm.startPrank(hook);
        usdc.approve(address(vault), type(uint256).max);
        vault.settleUp(sid, lpA, address(usdc), 10_000e6, 0, 0);
        vm.stopPrank();
    }
}
