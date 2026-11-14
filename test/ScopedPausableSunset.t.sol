// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {ScopedPausable} from "../contracts/security/ScopedPausable.sol";
import {CommitRevealAuction} from "../contracts/CommitRevealAuction.sol";
import {ExecutionEscrowManager} from "../contracts/ExecutionEscrowManager.sol";
import {IExecutionEscrowManager} from "../contracts/IExecutionEscrowManager.sol";

/// @dev Minimal concrete harness exposing the pause modifier.
contract SunsetHarness is ScopedPausable {
    constructor(address guardian_) ScopedPausable(guardian_) {}

    function inject() external whenNotPaused returns (bool) {
        return true;
    }

    /// @dev Models a settlement-class path: never carries the modifier.
    function settle() external pure returns (bool) {
        return true;
    }
}

/// @notice Fuzz + boundary coverage for the ScopedPausable sunset.
///         Ratified semantics (contracts/security/ScopedPausable.sol,
///         tests/pytest/test_scoped_pausable.py):
///         - SUNSET_BLOCK = deploy block + 7,776,000 (180 days at 2s Base
///           blocks). Before it, the guardian may pause and `whenNotPaused`
///           freezes ONLY state/capital injection (openAuction, commit,
///           depositStake).
///         - AT/AFTER the sunset, `setPause` reverts (PauseExpired) and
///           `whenNotPaused` stops enforcing — even a stale `paused == true`
///           can no longer block anything. The pause mechanism itself is
///           what gets locked down; the protocol becomes unpausable.
///         - Settlement-class paths (reveal, selectWinnerAndFund, timeout,
///           escrow timeout) NEVER carry the pause modifier, on either side
///           of the boundary: a pause can never trap in-flight settlement.
contract ScopedPausableSunsetTest is Test {
    uint256 internal constant SUNSET_DURATION = 7_776_000;
    uint64 internal constant START = 1_700_000_000;

    SunsetHarness internal harness;
    CommitRevealAuction internal auction;
    ExecutionEscrowManager internal escrow;

    address internal guardian = makeAddr("guardian");
    address internal poster = makeAddr("poster");
    address internal adjudicator = makeAddr("adjudicator");
    address internal bidder = makeAddr("bidder");
    address internal anyone = makeAddr("anyone");
    address internal stranger = makeAddr("stranger");

    uint256 internal deployBlock;
    uint256 internal sunset;

    bytes32 internal constant SALT = keccak256("salt");
    bytes32 internal constant AGENT = keccak256("agent");

    function setUp() public {
        vm.warp(START);
        deployBlock = block.number;
        harness = new SunsetHarness(guardian);
        auction = new CommitRevealAuction(guardian);
        escrow = new ExecutionEscrowManager(address(auction), adjudicator, 5000, 0.01 ether, guardian);
        auction.setEscrowManager(address(escrow));
        sunset = harness.SUNSET_BLOCK();
        vm.deal(poster, 1_000 ether);
        vm.deal(bidder, 100 ether);
    }

    function _commitment(bytes32 aid, uint256 price) internal view returns (bytes32) {
        return keccak256(abi.encodePacked(aid, block.chainid, bytes32(price), SALT, AGENT));
    }

    // ------------------------------------------------------------------
    // Sunset arithmetic / wiring
    // ------------------------------------------------------------------

    function test_sunsetArithmetic() public {
        uint256 b = block.number;
        SunsetHarness fresh = new SunsetHarness(guardian);
        assertEq(fresh.SUNSET_DURATION_BLOCKS(), SUNSET_DURATION);
        assertEq(fresh.SUNSET_BLOCK(), b + SUNSET_DURATION);
        assertEq(fresh.guardianMultisig(), guardian);
        assertFalse(fresh.paused());
        // Both production consumers wire the same sunset.
        assertEq(auction.SUNSET_BLOCK(), deployBlock + SUNSET_DURATION);
        assertEq(escrow.SUNSET_BLOCK(), deployBlock + SUNSET_DURATION);
        assertEq(sunset, deployBlock + SUNSET_DURATION);
    }

    function test_zeroGuardianReverts() public {
        vm.expectRevert(bytes("Invalid guardian"));
        new SunsetHarness(address(0));
    }

    // ------------------------------------------------------------------
    // setPause: live before the boundary, dead at/after it
    // ------------------------------------------------------------------

    function test_setPause_atSunsetMinusOne_succeeds() public {
        vm.roll(sunset - 1);
        vm.prank(guardian);
        harness.setPause(true);
        assertTrue(harness.paused());
        vm.prank(guardian);
        harness.setPause(false);
        assertFalse(harness.paused());
    }

    function test_setPause_atSunset_reverts() public {
        vm.roll(sunset);
        vm.prank(guardian);
        vm.expectRevert(ScopedPausable.PauseExpired.selector);
        harness.setPause(true);
    }

    function testFuzz_setPause_beforeSunset_succeeds(uint256 before) public {
        before = bound(before, 0, SUNSET_DURATION - 1);
        vm.roll(sunset - 1 - before);
        vm.prank(guardian);
        harness.setPause(true);
        assertTrue(harness.paused());
    }

    function testFuzz_setPause_atOrAfterSunset_reverts(uint256 extra) public {
        extra = bound(extra, 0, 100_000_000);
        vm.roll(sunset + extra);
        vm.prank(guardian);
        vm.expectRevert(ScopedPausable.PauseExpired.selector);
        harness.setPause(true);
        // Unpausing is equally dead: the state is frozen as-is forever.
        vm.prank(guardian);
        vm.expectRevert(ScopedPausable.PauseExpired.selector);
        harness.setPause(false);
    }

    function testFuzz_nonGuardian_cannotPause_beforeSunset(uint256 before) public {
        before = bound(before, 0, SUNSET_DURATION - 1);
        vm.roll(sunset - 1 - before);
        vm.prank(stranger);
        vm.expectRevert(ScopedPausable.UnauthorizedGuardian.selector);
        harness.setPause(true);
        assertFalse(harness.paused());
    }

    function test_nonGuardian_afterSunset_getsGuardianErrorNotPauseExpired() public {
        // Guardian check runs first: a stranger past the sunset still hits
        // UnauthorizedGuardian, not PauseExpired.
        vm.roll(sunset + 1);
        vm.prank(stranger);
        vm.expectRevert(ScopedPausable.UnauthorizedGuardian.selector);
        harness.setPause(true);
    }

    // ------------------------------------------------------------------
    // whenNotPaused enforcement across the boundary
    // ------------------------------------------------------------------

    function test_inject_blockedWhilePaused_beforeSunset() public {
        vm.prank(guardian);
        harness.setPause(true);
        vm.expectRevert(ScopedPausable.EmergencyPaused.selector);
        harness.inject();
        assertTrue(harness.settle()); // settlement-class path unaffected
    }

    function test_inject_allowedWhileUnpaused_beforeSunset() public {
        assertTrue(harness.inject());
    }

    function testFuzz_inject_blockedWhilePaused_anywhereBeforeSunset(uint256 before) public {
        before = bound(before, 0, SUNSET_DURATION - 1);
        vm.prank(guardian);
        harness.setPause(true);
        vm.roll(sunset - 1 - before);
        vm.expectRevert(ScopedPausable.EmergencyPaused.selector);
        harness.inject();
    }

    function test_inject_blockedAtSunsetMinusOne_allowedAtSunset() public {
        vm.prank(guardian);
        harness.setPause(true);
        vm.roll(sunset - 1);
        vm.expectRevert(ScopedPausable.EmergencyPaused.selector);
        harness.inject();
        // The exact boundary: enforcement stops AT SUNSET_BLOCK, even with
        // `paused` still stale-true. The protocol is unpausable from here.
        vm.roll(sunset);
        assertTrue(harness.paused());
        assertTrue(harness.inject());
    }

    function testFuzz_inject_allowedAtOrAfterSunset_despiteStalePause(uint256 extra) public {
        extra = bound(extra, 0, 100_000_000);
        vm.prank(guardian);
        harness.setPause(true);
        vm.roll(sunset + extra);
        assertTrue(harness.paused()); // stale flag can never be cleared now
        assertTrue(harness.inject());
        assertTrue(harness.settle());
    }

    function test_pauseEvents() public {
        vm.expectEmit(true, false, false, true);
        emit ScopedPausable.ProtocolPaused(guardian);
        vm.prank(guardian);
        harness.setPause(true);
        vm.expectEmit(true, false, false, true);
        emit ScopedPausable.ProtocolUnpaused(guardian);
        vm.prank(guardian);
        harness.setPause(false);
    }

    // ------------------------------------------------------------------
    // Real consumer (CommitRevealAuction): injection scope + sunset
    // ------------------------------------------------------------------

    function test_auction_pauseFreezesInjectionNotSettlement() public {
        bytes32 aid = keccak256("sunset-a1");
        vm.prank(poster);
        auction.openAuction(aid, 60, 60);
        vm.prank(bidder);
        auction.commit(aid, _commitment(aid, 1 ether));
        (,,,, uint64 revealD) = auction.auctions(aid);

        vm.prank(guardian);
        auction.setPause(true);

        // Injection frozen.
        vm.prank(poster);
        vm.expectRevert(ScopedPausable.EmergencyPaused.selector);
        auction.openAuction(keccak256("sunset-a2"), 60, 60);
        vm.prank(anyone);
        vm.expectRevert(ScopedPausable.EmergencyPaused.selector);
        auction.commit(aid, _commitment(aid, 2 ether));

        // Settlement-class paths stay live under pause.
        vm.warp(START + 61);
        vm.prank(bidder);
        auction.reveal(aid, 1 ether, SALT, AGENT);
        vm.warp(uint256(revealD) + 1);
        vm.prank(poster);
        auction.selectWinnerAndFund{value: 1 ether}(aid, 0);
        (, bool finalized, , , ) = auction.auctions(aid);
        assertTrue(finalized);
    }

    function test_auction_timeoutWorksWhilePaused() public {
        bytes32 aid = keccak256("sunset-a3");
        vm.prank(poster);
        auction.openAuction(aid, 60, 60);
        (,,,, uint64 revealD) = auction.auctions(aid);
        vm.prank(guardian);
        auction.setPause(true);
        vm.warp(uint256(revealD) + 1);
        vm.prank(anyone);
        auction.timeout(aid);
        (, bool finalized, , , ) = auction.auctions(aid);
        assertTrue(finalized);
    }

    function test_auction_unpausableAfterSunset_evenIfPausedAtBoundary() public {
        // Guardian pauses one block before the sunset; at the sunset the
        // stale pause loses all force and injection re-opens by construction.
        vm.roll(sunset - 1);
        vm.prank(guardian);
        auction.setPause(true);
        vm.roll(sunset);
        bytes32 aid = keccak256("sunset-a4");
        vm.prank(poster);
        auction.openAuction(aid, 60, 60);
        vm.prank(bidder);
        auction.commit(aid, _commitment(aid, 1 ether));
        (bytes32 stored, , ) = auction.commits(aid, bidder);
        assertTrue(stored != bytes32(0));
    }

    function testFuzz_auction_injectionAfterSunset_despiteStalePause(uint256 extra) public {
        extra = bound(extra, 0, 100_000_000);
        vm.prank(guardian);
        auction.setPause(true);
        vm.roll(sunset + extra);
        bytes32 aid = keccak256(abi.encodePacked("sunset-a5", extra));
        vm.prank(poster);
        auction.openAuction(aid, 60, 60);
        vm.prank(bidder);
        auction.commit(aid, _commitment(aid, 1 ether));
    }

    // ------------------------------------------------------------------
    // Escrow consumer: stake deposit is injection; timeout is settlement
    // ------------------------------------------------------------------

    function _selectedEscrow(bytes32 aid) internal returns (uint256 price) {
        price = 2 ether;
        vm.prank(poster);
        auction.openAuction(aid, 60, 60);
        vm.prank(bidder);
        auction.commit(aid, _commitment(aid, price));
        (,,,, uint64 revealD) = auction.auctions(aid);
        vm.warp(START + 61);
        vm.prank(bidder);
        auction.reveal(aid, price, SALT, AGENT);
        vm.warp(uint256(revealD) + 1);
        vm.prank(poster);
        auction.selectWinnerAndFund{value: price}(aid, 0);
    }

    function test_escrow_pauseFreezesStakeDeposit_notTimeout() public {
        bytes32 aid = keccak256("sunset-e1");
        uint256 price = _selectedEscrow(aid);
        uint256 stake = (price * 5000) / 10_000;

        vm.prank(guardian);
        escrow.setPause(true);
        vm.prank(bidder);
        vm.expectRevert(ScopedPausable.EmergencyPaused.selector);
        escrow.depositStake{value: stake}(aid);

        // The poster's escape hatch — escrow timeout refund — is a
        // settlement-class path and stays live under pause. The stake
        // window (default execution params: 1 day) lapses, then timeout
        // refunds the poster in full.
        uint256 posterBefore = poster.balance;
        vm.warp(block.timestamp + 1 days + 1);
        vm.prank(anyone);
        escrow.timeout(aid);
        IExecutionEscrowManager.Escrow memory e = escrow.getEscrow(aid);
        assertTrue(e.state == IExecutionEscrowManager.ExecutionState.TimedOut);
        assertEq(poster.balance, posterBefore + price);
    }

    function test_escrow_stakeDepositWorksAfterSunset_withStalePause() public {
        bytes32 aid = keccak256("sunset-e2");
        uint256 price = _selectedEscrow(aid);
        uint256 stake = (price * 5000) / 10_000;
        vm.prank(guardian);
        escrow.setPause(true);
        // Roll both escrow and auction past their (identical) sunsets: the
        // escrow's stale pause can no longer freeze the stake deposit.
        vm.roll(sunset);
        vm.prank(bidder);
        escrow.depositStake{value: stake}(aid);
        IExecutionEscrowManager.Escrow memory e = escrow.getEscrow(aid);
        assertTrue(e.state == IExecutionEscrowManager.ExecutionState.ExecutionPhase);
    }
}
