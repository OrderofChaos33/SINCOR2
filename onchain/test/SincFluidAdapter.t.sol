// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {SincFluidAdapter} from "../src/fluid/SincFluidAdapter.sol";
import {ComplianceGuard} from "../src/ComplianceGuard.sol";
import {MockERC20} from "./mocks/MockERC20.sol";
import {MockFToken, MockDexT1} from "./mocks/MockFluid.sol";

/// @notice Unit + fuzz tests for SincFluidAdapter using etched mocks at the
///         adapter's hardcoded Fluid Base addresses. Live-path proof is the fork
///         suite (SincFluidAdapter.fork.t.sol); this suite covers accounting,
///         permissions, pause, compliance, rescue, and token-order behavior.
contract SincFluidAdapterTest is Test {
    // must mirror SincFluidAdapter constants
    address constant F_USDC_ADDR = 0xf42f5795D9ac7e9D757dB633D693cD548Cfd9169;
    address constant LIQ = 0x52Aa899454998Be5b000Ad077a46Bbe360F4e497;

    SincFluidAdapter adapter;
    ComplianceGuard guard;
    MockERC20 sinc;
    MockERC20 usdc;

    address guardian = makeAddr("guardian");
    address treasury = makeAddr("treasury");
    address user = makeAddr("user");
    address stranger = makeAddr("stranger");

    function setUp() public {
        sinc = new MockERC20("SINC", "SINC", 8);
        usdc = new MockERC20("USD Coin", "USDC", 6);

        // etch Fluid-behavior mocks at the hardcoded addresses
        MockFToken ft = new MockFToken(usdc);
        vm.etch(F_USDC_ADDR, address(ft).code);
        MockDexT1 dex = new MockDexT1(usdc, sinc); // token0=USDC, token1=SINC
        vm.etch(LIQ, address(dex).code);

        adapter = new SincFluidAdapter(sinc, usdc, guardian, treasury);
        guard = new ComplianceGuard(guardian);

        vm.startPrank(guardian);
        adapter.setCompliance(address(guard));
        adapter.setFluidDex(LIQ);
        vm.stopPrank();

        sinc.mint(user, 1_000_000e8);
        usdc.mint(user, 1_000_000e6);
        vm.startPrank(user);
        sinc.approve(address(adapter), type(uint256).max);
        usdc.approve(address(adapter), type(uint256).max);
        vm.stopPrank();

        // fund mock DEX so borrows have liquidity to send
        sinc.mint(LIQ, 1_000_000e8);
        usdc.mint(LIQ, 1_000_000e6);
    }

    // ---------------- Stage 1: fUSDC ----------------

    function test_depositWithdrawUSDC() public {
        vm.startPrank(user);
        uint256 shares = adapter.depositUSDC(1_000e6);
        assertEq(shares, 1_000e6);
        assertEq(adapter.fUsdcShares(user), shares);
        assertEq(adapter.userValueUSDC(user), 1_000e6);

        uint256 out = adapter.withdrawUSDC(shares);
        assertEq(out, 1_000e6);
        assertEq(adapter.fUsdcShares(user), 0);
        vm.stopPrank();
    }

    function testFuzz_depositWithdrawUSDC(uint96 amt) public {
        vm.assume(amt > 0 && amt <= 1_000_000e6);
        vm.startPrank(user);
        uint256 shares = adapter.depositUSDC(amt);
        assertEq(adapter.fUsdcShares(user), shares);
        uint256 out = adapter.withdrawUSDC(shares);
        assertEq(out, amt);
        vm.stopPrank();
        assertEq(usdc.balanceOf(user), 1_000_000e6);
    }

    // ---------------- Stage 2: DEX smart collateral ----------------

    function test_supplyWithdrawDex_tokenOrderAndAccounting() public {
        uint256 sincAmt = 500e8;
        uint256 usdcAmt = 500e6;
        vm.startPrank(user);
        uint256 shares = adapter.supplyToDex(sincAmt, usdcAmt, 0);
        assertEq(shares, sincAmt + usdcAmt); // mock: token0+token1
        assertEq(adapter.dexColShares(user), shares);
        assertEq(adapter.totalDexColShares(), shares);

        // withdraw everything, tokens return to user
        uint256 burned = adapter.withdrawFromDex(sincAmt, usdcAmt, shares);
        assertEq(burned, shares);
        assertEq(adapter.dexColShares(user), 0);
        assertEq(sinc.balanceOf(user), 1_000_000e8);
        assertEq(usdc.balanceOf(user), 1_000_000e6);
        vm.stopPrank();
    }

    function testFuzz_supplyDex(uint64 sincAmt, uint64 usdcAmt) public {
        vm.assume(sincAmt > 0 || usdcAmt > 0);
        vm.startPrank(user);
        uint256 shares = adapter.supplyToDex(sincAmt, usdcAmt, 0);
        assertEq(shares, uint256(sincAmt) + usdcAmt);
        assertEq(adapter.dexColShares(user), shares);
        vm.stopPrank();
    }

    // ---------------- Stage 3: guardian-only smart debt ----------------

    function test_borrowPayback_guardianOnly() public {
        vm.prank(user);
        vm.expectRevert(SincFluidAdapter.OnlyGuardian.selector);
        adapter.borrowSmartDebt(100e6, 50e6, 0, treasury);

        vm.prank(guardian);
        adapter.borrowSmartDebt(100e6, 50e6, 0, treasury);
        assertEq(adapter.totalDebtShares(), 100e6);
        assertEq(usdc.balanceOf(treasury), 50e6);

        // guardian repays the 50 USDC leg
        usdc.mint(guardian, 50e6);
        vm.startPrank(guardian);
        usdc.approve(address(adapter), type(uint256).max);
        uint256 burned = adapter.paybackSmartDebt(50e6, 0, 0);
        assertEq(burned, 50e6);
        assertEq(adapter.totalDebtShares(), 50e6);
        vm.stopPrank();
    }

    // ---------------- compliance gate ----------------

    function test_complianceBlocksUser() public {
        vm.prank(guardian);
        guard.block(user);

        vm.prank(user);
        vm.expectRevert(abi.encodeWithSelector(SincFluidAdapter.ComplianceBlocked.selector, user));
        adapter.depositUSDC(100e6);

        vm.prank(user);
        vm.expectRevert(abi.encodeWithSelector(SincFluidAdapter.ComplianceBlocked.selector, user));
        adapter.supplyToDex(1e8, 1e6, 0);

        vm.prank(guardian);
        guard.unblock(user);
        vm.prank(user);
        adapter.depositUSDC(100e6); // works again
        assertGt(adapter.fUsdcShares(user), 0);
    }

    function test_strangerCannotAdmin() public {
        vm.startPrank(stranger);
        vm.expectRevert(SincFluidAdapter.OnlyGuardian.selector);
        adapter.setFluidDex(stranger);
        vm.expectRevert(SincFluidAdapter.OnlyGuardian.selector);
        adapter.setCompliance(stranger);
        vm.expectRevert(SincFluidAdapter.OnlyGuardian.selector);
        adapter.pause();
        vm.expectRevert(SincFluidAdapter.OnlyGuardian.selector);
        adapter.rescue(address(usdc), 1);
        vm.stopPrank();
    }

    // ---------------- pause + rescue ----------------

    function test_pauseBlocksEntryNotAdmin() public {
        vm.prank(guardian);
        adapter.pause();
        vm.prank(user);
        vm.expectRevert();
        adapter.depositUSDC(100e6);
        vm.prank(guardian);
        adapter.unpause();
        vm.prank(user);
        adapter.depositUSDC(100e6);
    }

    function test_rescueSweepsToTreasury() public {
        usdc.mint(address(adapter), 777e6);
        vm.prank(guardian);
        adapter.rescue(address(usdc), 777e6);
        assertEq(usdc.balanceOf(treasury), 777e6);
    }
}
