// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {CommitRevealAuction} from "../contracts/CommitRevealAuction.sol";
import {ExecutionEscrowManager} from "../contracts/ExecutionEscrowManager.sol";
import {IExecutionEscrowManager} from "../contracts/IExecutionEscrowManager.sol";

// UNION (2026-11-16): this file carries BOTH CommitRevealAuction suites —
// the main-branch suite (CommitRevealAuctionTest) and the A+ overhaul
// suite (CommitRevealAuctionOverhaulTest, with its MockEscrowManager).
// No test from either side was dropped.

/// @notice Minimal escrow-manager stand-in: records exactly what the
///         auction core forwards at selection. The auction stores the
///         manager behind IExecutionEscrowManager and only ever calls
///         initializeEscrow, so an ABI-compatible recorder is sufficient.
contract MockEscrowManager {
    bytes32 public lastAuctionId;
    address public lastPoster;
    address public lastWorker;
    uint96 public lastBid;
    uint96 public lastCredit;
    uint256 public lastValue;
    uint256 public calls;

    function initializeEscrow(
        bytes32 auctionId,
        address poster,
        address selectedAgent,
        uint96 bidAmount,
        uint96 creditToApply,
        uint32,
        uint32,
        uint32,
        uint32
    ) external payable {
        lastAuctionId = auctionId;
        lastPoster = poster;
        lastWorker = selectedAgent;
        lastBid = bidAmount;
        lastCredit = creditToApply;
        lastValue = msg.value;
        calls++;
    }
}


/// @notice Foundry unit + fuzz coverage for CommitRevealAuction.
///         Behavioral spec: tests/pytest/test_selection_bridge.py,
///         test_w4_commitment_auctionid.py and test_scoped_pausable.py
///         (the eth-tester suites). This file is the Foundry counterpart:
///         commit/reveal phases, Vickrey selection + escrow funding, and
///         the edge-case timing boundaries of both windows.
contract CommitRevealAuctionTest is Test {
    CommitRevealAuction internal auction;
    ExecutionEscrowManager internal escrow;

    address internal guardian = makeAddr("guardian");
    address internal poster = makeAddr("poster");
    address internal adjudicator = makeAddr("adjudicator");
    address internal bidderA = makeAddr("bidderA");
    address internal bidderB = makeAddr("bidderB");
    address internal bidderC = makeAddr("bidderC");
    address internal anyone = makeAddr("anyone");

    uint256 internal constant MIN_STAKE_BPS = 5000;
    uint256 internal constant CHALLENGER_BOND = 0.01 ether;
    uint64 internal constant START = 1_700_000_000;

    bytes32 internal constant SALT_A = keccak256("salt-a");
    bytes32 internal constant SALT_B = keccak256("salt-b");
    bytes32 internal constant SALT_C = keccak256("salt-c");
    bytes32 internal constant AGENT_A = keccak256("agent-a");
    bytes32 internal constant AGENT_B = keccak256("agent-b");
    bytes32 internal constant AGENT_C = keccak256("agent-c");

    function setUp() public {
        vm.warp(START);
        auction = new CommitRevealAuction(guardian);
        escrow = new ExecutionEscrowManager(
            address(auction), adjudicator, MIN_STAKE_BPS, CHALLENGER_BOND, guardian
        );
        auction.setEscrowManager(address(escrow));
        vm.deal(poster, 1_000 ether);
        vm.deal(bidderA, 100 ether);
        vm.deal(bidderB, 100 ether);
        vm.deal(bidderC, 100 ether);
    }

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------

    function _commitment(bytes32 aid, uint256 price, bytes32 salt, bytes32 agentIdHash)
        internal
        view
        returns (bytes32)
    {
        // Mirrors the contract preimage exactly:
        // Hash(auctionId || chainId || price || salt || keccak(agentId)).
        return keccak256(abi.encodePacked(aid, block.chainid, bytes32(price), salt, agentIdHash));
    }

    function _open(bytes32 aid, uint64 commitW, uint64 revealW) internal {
        vm.prank(poster);
        auction.openAuction(aid, commitW, revealW);
    }

    function _commitAs(address bidder, bytes32 aid, uint256 price, bytes32 salt, bytes32 agent) internal {
        vm.prank(bidder);
        auction.commit(aid, _commitment(aid, price, salt, agent));
    }

    function _revealAs(address bidder, bytes32 aid, uint256 price, bytes32 salt, bytes32 agent) internal {
        vm.prank(bidder);
        auction.reveal(aid, price, salt, agent);
    }

    function _deadlines(bytes32 aid) internal view returns (uint64 commitD, uint64 revealD) {
        (,,, commitD, revealD) = auction.auctions(aid);
    }

    function _openCommitReveal(
        bytes32 aid,
        uint256 priceA,
        uint256 priceB
    ) internal returns (uint64 revealD) {
        _open(aid, 60, 60);
        _commitAs(bidderA, aid, priceA, SALT_A, AGENT_A);
        _commitAs(bidderB, aid, priceB, SALT_B, AGENT_B);
        (uint64 commitD, uint64 _revealD) = _deadlines(aid);
        revealD = _revealD;
        vm.warp(uint256(commitD) + 1);
        _revealAs(bidderA, aid, priceA, SALT_A, AGENT_A);
        _revealAs(bidderB, aid, priceB, SALT_B, AGENT_B);
    }

    // ------------------------------------------------------------------
    // openAuction
    // ------------------------------------------------------------------

    function test_openAuction_defaultWindows() public {
        bytes32 aid = keccak256("a1");
        _open(aid, 0, 0);
        (bool opened, bool finalized, address p, uint64 commitD, uint64 revealD) = auction.auctions(aid);
        assertTrue(opened);
        assertFalse(finalized);
        assertEq(p, poster);
        assertEq(commitD, START + 300);
        assertEq(revealD, START + 600);
    }

    function test_openAuction_customWindows() public {
        bytes32 aid = keccak256("a2");
        _open(aid, 60, 120);
        (, , , uint64 commitD, uint64 revealD) = auction.auctions(aid);
        assertEq(commitD, START + 60);
        assertEq(revealD, START + 180);
    }

    function test_openAuction_twiceReverts() public {
        bytes32 aid = keccak256("a3");
        _open(aid, 60, 60);
        vm.prank(anyone);
        vm.expectRevert(CommitRevealAuction.AlreadyOpened.selector);
        auction.openAuction(aid, 60, 60);
    }

    function testFuzz_openAuction_windows(uint64 commitW, uint64 revealW) public {
        commitW = uint64(bound(commitW, 1, 1 days));
        revealW = uint64(bound(revealW, 1, 1 days));
        bytes32 aid = keccak256(abi.encodePacked("fz-open", commitW, revealW));
        _open(aid, commitW, revealW);
        (, , , uint64 commitD, uint64 revealD) = auction.auctions(aid);
        assertEq(commitD, START + commitW);
        assertEq(revealD, START + commitW + revealW);
    }

    // ------------------------------------------------------------------
    // commit phase
    // ------------------------------------------------------------------

    function test_commit_happyPath() public {
        bytes32 aid = keccak256("c1");
        _open(aid, 60, 60);
        bytes32 hash = _commitment(aid, 1 ether, SALT_A, AGENT_A);
        vm.expectEmit(true, true, false, true);
        emit CommitRevealAuction.Committed(aid, bidderA, hash);
        vm.prank(bidderA);
        auction.commit(aid, hash);
        (bytes32 stored, bool revealed, uint256 price) = auction.commits(aid, bidderA);
        assertEq(stored, hash);
        assertFalse(revealed);
        assertEq(price, 0);
        assertEq(auction.bidders(aid, 0), bidderA);
    }

    function test_commit_unopenedReverts() public {
        bytes32 aid = keccak256("c2");
        vm.prank(bidderA);
        vm.expectRevert(CommitRevealAuction.AuctionNotOpen.selector);
        auction.commit(aid, _commitment(aid, 1 ether, SALT_A, AGENT_A));
    }

    function test_commit_emptyHashReverts() public {
        bytes32 aid = keccak256("c3");
        _open(aid, 60, 60);
        vm.prank(bidderA);
        vm.expectRevert(CommitRevealAuction.EmptyCommit.selector);
        auction.commit(aid, bytes32(0));
    }

    function test_commit_doubleCommitReverts() public {
        bytes32 aid = keccak256("c4");
        _open(aid, 60, 60);
        _commitAs(bidderA, aid, 1 ether, SALT_A, AGENT_A);
        vm.prank(bidderA);
        vm.expectRevert(CommitRevealAuction.AlreadyCommitted.selector);
        auction.commit(aid, _commitment(aid, 2 ether, SALT_A, AGENT_A));
    }

    function test_commit_atExactDeadlineSucceeds() public {
        bytes32 aid = keccak256("c5");
        _open(aid, 60, 60);
        (uint64 commitD, ) = _deadlines(aid);
        // Contract closes the window strictly AFTER the deadline (>).
        vm.warp(commitD);
        _commitAs(bidderA, aid, 1 ether, SALT_A, AGENT_A);
        (bytes32 stored, , ) = auction.commits(aid, bidderA);
        assertTrue(stored != bytes32(0));
    }

    function test_commit_oneSecondAfterDeadlineReverts() public {
        bytes32 aid = keccak256("c6");
        _open(aid, 60, 60);
        (uint64 commitD, ) = _deadlines(aid);
        vm.warp(uint256(commitD) + 1);
        vm.prank(bidderA);
        vm.expectRevert(CommitRevealAuction.CommitWindowClosed.selector);
        auction.commit(aid, _commitment(aid, 1 ether, SALT_A, AGENT_A));
    }

    function testFuzz_commit_afterDeadlineReverts(uint64 lateBy) public {
        lateBy = uint64(bound(lateBy, 1, 30 days));
        bytes32 aid = keccak256(abi.encodePacked("c7", lateBy));
        _open(aid, 60, 60);
        (uint64 commitD, ) = _deadlines(aid);
        vm.warp(uint256(commitD) + lateBy);
        vm.prank(bidderA);
        vm.expectRevert(CommitRevealAuction.CommitWindowClosed.selector);
        auction.commit(aid, _commitment(aid, 1 ether, SALT_A, AGENT_A));
    }

    // ------------------------------------------------------------------
    // reveal phase
    // ------------------------------------------------------------------

    function test_reveal_happyPath() public {
        bytes32 aid = keccak256("r1");
        _open(aid, 60, 60);
        _commitAs(bidderA, aid, 1 ether, SALT_A, AGENT_A);
        (uint64 commitD, ) = _deadlines(aid);
        vm.warp(uint256(commitD) + 1);
        vm.expectEmit(true, true, false, true);
        emit CommitRevealAuction.Revealed(aid, bidderA, 1 ether);
        _revealAs(bidderA, aid, 1 ether, SALT_A, AGENT_A);
        (, bool revealed, uint256 price) = auction.commits(aid, bidderA);
        assertTrue(revealed);
        assertEq(price, 1 ether);
    }

    function test_reveal_duringCommitWindowReverts() public {
        bytes32 aid = keccak256("r2");
        _open(aid, 60, 60);
        _commitAs(bidderA, aid, 1 ether, SALT_A, AGENT_A);
        vm.prank(bidderA);
        vm.expectRevert(CommitRevealAuction.RevealTooEarly.selector);
        auction.reveal(aid, 1 ether, SALT_A, AGENT_A);
    }

    function test_reveal_atCommitDeadlineStillTooEarly() public {
        bytes32 aid = keccak256("r3");
        _open(aid, 60, 60);
        _commitAs(bidderA, aid, 1 ether, SALT_A, AGENT_A);
        (uint64 commitD, ) = _deadlines(aid);
        // Reveal opens strictly AFTER the commit deadline (<= is too early).
        vm.warp(commitD);
        vm.prank(bidderA);
        vm.expectRevert(CommitRevealAuction.RevealTooEarly.selector);
        auction.reveal(aid, 1 ether, SALT_A, AGENT_A);
    }

    function test_reveal_atRevealDeadlineSucceeds() public {
        bytes32 aid = keccak256("r4");
        _open(aid, 60, 60);
        _commitAs(bidderA, aid, 1 ether, SALT_A, AGENT_A);
        (, uint64 revealD) = _deadlines(aid);
        vm.warp(revealD);
        _revealAs(bidderA, aid, 1 ether, SALT_A, AGENT_A);
        (, bool revealed, ) = auction.commits(aid, bidderA);
        assertTrue(revealed);
    }

    function test_reveal_afterRevealDeadlineReverts() public {
        bytes32 aid = keccak256("r5");
        _open(aid, 60, 60);
        _commitAs(bidderA, aid, 1 ether, SALT_A, AGENT_A);
        (, uint64 revealD) = _deadlines(aid);
        vm.warp(uint256(revealD) + 1);
        vm.prank(bidderA);
        vm.expectRevert(CommitRevealAuction.RevealWindowClosed.selector);
        auction.reveal(aid, 1 ether, SALT_A, AGENT_A);
    }

    function testFuzz_reveal_afterRevealDeadlineReverts(uint64 lateBy) public {
        lateBy = uint64(bound(lateBy, 1, 30 days));
        bytes32 aid = keccak256(abi.encodePacked("r6", lateBy));
        _open(aid, 60, 60);
        _commitAs(bidderA, aid, 1 ether, SALT_A, AGENT_A);
        (, uint64 revealD) = _deadlines(aid);
        vm.warp(uint256(revealD) + lateBy);
        vm.prank(bidderA);
        vm.expectRevert(CommitRevealAuction.RevealWindowClosed.selector);
        auction.reveal(aid, 1 ether, SALT_A, AGENT_A);
    }

    function test_reveal_wrongSaltReverts() public {
        bytes32 aid = keccak256("r7");
        _open(aid, 60, 60);
        _commitAs(bidderA, aid, 1 ether, SALT_A, AGENT_A);
        (uint64 commitD, ) = _deadlines(aid);
        vm.warp(uint256(commitD) + 1);
        vm.prank(bidderA);
        vm.expectRevert(CommitRevealAuction.BadReveal.selector);
        auction.reveal(aid, 1 ether, SALT_B, AGENT_A);
    }

    function test_reveal_wrongAmountReverts() public {
        bytes32 aid = keccak256("r8");
        _open(aid, 60, 60);
        _commitAs(bidderA, aid, 1 ether, SALT_A, AGENT_A);
        (uint64 commitD, ) = _deadlines(aid);
        vm.warp(uint256(commitD) + 1);
        vm.prank(bidderA);
        vm.expectRevert(CommitRevealAuction.BadReveal.selector);
        auction.reveal(aid, 2 ether, SALT_A, AGENT_A);
    }

    function testFuzz_reveal_wrongSaltReverts(bytes32 wrongSalt) public {
        vm.assume(wrongSalt != SALT_A);
        bytes32 aid = keccak256("r9");
        _open(aid, 60, 60);
        _commitAs(bidderA, aid, 1 ether, SALT_A, AGENT_A);
        (uint64 commitD, ) = _deadlines(aid);
        vm.warp(uint256(commitD) + 1);
        vm.prank(bidderA);
        vm.expectRevert(CommitRevealAuction.BadReveal.selector);
        auction.reveal(aid, 1 ether, wrongSalt, AGENT_A);
    }

    function test_reveal_withoutCommitReverts() public {
        bytes32 aid = keccak256("r10");
        _open(aid, 60, 60);
        (uint64 commitD, ) = _deadlines(aid);
        vm.warp(uint256(commitD) + 1);
        vm.prank(bidderC);
        vm.expectRevert(CommitRevealAuction.NoCommit.selector);
        auction.reveal(aid, 1 ether, SALT_C, AGENT_C);
    }

    function test_reveal_doubleRevealReverts() public {
        bytes32 aid = keccak256("r11");
        _open(aid, 60, 60);
        _commitAs(bidderA, aid, 1 ether, SALT_A, AGENT_A);
        (uint64 commitD, ) = _deadlines(aid);
        vm.warp(uint256(commitD) + 1);
        _revealAs(bidderA, aid, 1 ether, SALT_A, AGENT_A);
        vm.prank(bidderA);
        vm.expectRevert(CommitRevealAuction.AlreadyRevealed.selector);
        auction.reveal(aid, 1 ether, SALT_A, AGENT_A);
    }

    function test_reveal_crossAuctionReplayReverts() public {
        // W-4: the preimage binds the auctionId, so a commitment made for
        // one auction cannot be revealed against another.
        bytes32 aid1 = keccak256("r12-a");
        bytes32 aid2 = keccak256("r12-b");
        _open(aid1, 60, 60);
        _open(aid2, 60, 60);
        bytes32 replayed = _commitment(aid1, 1 ether, SALT_A, AGENT_A);
        vm.prank(bidderA);
        auction.commit(aid1, replayed);
        vm.prank(bidderA);
        auction.commit(aid2, replayed);
        (uint64 commitD, ) = _deadlines(aid2);
        vm.warp(uint256(commitD) + 1);
        vm.prank(bidderA);
        vm.expectRevert(CommitRevealAuction.BadReveal.selector);
        auction.reveal(aid2, 1 ether, SALT_A, AGENT_A);
        // The original auction still reveals fine.
        _revealAs(bidderA, aid1, 1 ether, SALT_A, AGENT_A);
    }

    function test_reveal_priceAboveUint96Reverts() public {
        uint256 bigPrice = uint256(type(uint96).max) + 1;
        bytes32 aid = keccak256("r13");
        _open(aid, 60, 60);
        _commitAs(bidderA, aid, bigPrice, SALT_A, AGENT_A);
        (uint64 commitD, ) = _deadlines(aid);
        vm.warp(uint256(commitD) + 1);
        vm.prank(bidderA);
        vm.expectRevert(CommitRevealAuction.PriceTooLarge.selector);
        auction.reveal(aid, bigPrice, SALT_A, AGENT_A);
    }

    function test_reveal_priceAtUint96MaxAccepted() public {
        uint256 maxPrice = uint256(type(uint96).max);
        bytes32 aid = keccak256("r14");
        _open(aid, 60, 60);
        _commitAs(bidderA, aid, maxPrice, SALT_A, AGENT_A);
        (uint64 commitD, ) = _deadlines(aid);
        vm.warp(uint256(commitD) + 1);
        _revealAs(bidderA, aid, maxPrice, SALT_A, AGENT_A);
        (, bool revealed, uint256 price) = auction.commits(aid, bidderA);
        assertTrue(revealed);
        assertEq(price, maxPrice);
    }

    // ------------------------------------------------------------------
    // Vickrey result (view)
    // ------------------------------------------------------------------

    function test_vickrey_lowestWinsAtSecondPrice() public {
        bytes32 aid = keccak256("v1");
        _openCommitReveal(aid, 3 ether, 1 ether);
        (address winner, uint256 price) = auction.vickreyResult(aid);
        assertEq(winner, bidderB);
        assertEq(price, 3 ether);
    }

    function test_vickrey_singleBidderFirstPrice() public {
        bytes32 aid = keccak256("v2");
        _open(aid, 60, 60);
        _commitAs(bidderA, aid, 2 ether, SALT_A, AGENT_A);
        (uint64 commitD, ) = _deadlines(aid);
        vm.warp(uint256(commitD) + 1);
        _revealAs(bidderA, aid, 2 ether, SALT_A, AGENT_A);
        (address winner, uint256 price) = auction.vickreyResult(aid);
        assertEq(winner, bidderA);
        assertEq(price, 2 ether);
    }

    function test_vickrey_tieBreaksToEarliestCommitter() public {
        bytes32 aid = keccak256("v3");
        _openCommitReveal(aid, 1 ether, 1 ether);
        (address winner, uint256 price) = auction.vickreyResult(aid);
        assertEq(winner, bidderA);
        assertEq(price, 1 ether);
    }

    function test_vickrey_ignoresUnrevealedCommits() public {
        bytes32 aid = keccak256("v4");
        _open(aid, 60, 60);
        _commitAs(bidderA, aid, 5 ether, SALT_A, AGENT_A);
        _commitAs(bidderB, aid, 1 ether, SALT_B, AGENT_B); // never revealed
        (uint64 commitD, ) = _deadlines(aid);
        vm.warp(uint256(commitD) + 1);
        _revealAs(bidderA, aid, 5 ether, SALT_A, AGENT_A);
        (address winner, uint256 price) = auction.vickreyResult(aid);
        assertEq(winner, bidderA);
        assertEq(price, 5 ether);
    }

    function test_vickrey_noRevealedBidsReverts() public {
        bytes32 aid = keccak256("v5");
        _open(aid, 60, 60);
        _commitAs(bidderA, aid, 1 ether, SALT_A, AGENT_A);
        vm.expectRevert(CommitRevealAuction.NoRevealedBids.selector);
        auction.vickreyResult(aid);
    }

    function testFuzz_vickrey_twoDistinctBids(uint256 pA, uint256 pB) public {
        pA = bound(pA, 1, 100 ether);
        pB = bound(pB, 1, 100 ether);
        vm.assume(pA != pB);
        bytes32 aid = keccak256(abi.encodePacked("v6", pA, pB));
        _openCommitReveal(aid, pA, pB);
        (address winner, uint256 price) = auction.vickreyResult(aid);
        assertEq(winner, pA < pB ? bidderA : bidderB);
        assertEq(price, pA < pB ? pB : pA);
    }

    // ------------------------------------------------------------------
    // W-8 scale exercise (red-team register W-8/A5): vickreyResult loops
    // bidders[auctionId] uncapped and no MAX_COMMITTERS exists. This
    // test is evidence about the loop at scale — it drives 500 distinct
    // committers through commit + reveal, measures the gas
    // vickreyResult consumes, and settles through selectWinnerAndFund.
    // It does NOT fix W-8; the committer-cap decision (500 vs 2000) is
    // the founder's and is still pending.
    // ------------------------------------------------------------------

    event W8VickreyGas(uint256 committers, uint256 gasUsed);

    function test_w8_manyBidders_vickreyResult_completes_at_500() public {
        uint256 n = 500;
        bytes32 aid = keccak256("w8-500");
        _open(aid, 60, 60);
        // Distinct prices, strictly decreasing by commit order: bidder i
        // bids (n - i) * 0.001 ether, so the LAST committer holds the
        // lowest bid (wins at the second-lowest price, 0.002 ether).
        for (uint256 i = 0; i < n; i++) {
            address bidder = address(uint160(0xB000 + i));
            uint256 bidPrice = (n - i) * 0.001 ether;
            bytes32 salt = keccak256(abi.encodePacked("w8-salt", i));
            bytes32 agent = keccak256(abi.encodePacked("w8-agent", i));
            _commitAs(bidder, aid, bidPrice, salt, agent);
        }
        (uint64 commitD, uint64 revealD) = _deadlines(aid);
        vm.warp(uint256(commitD) + 1);
        for (uint256 i = 0; i < n; i++) {
            address bidder = address(uint160(0xB000 + i));
            uint256 bidPrice = (n - i) * 0.001 ether;
            bytes32 salt = keccak256(abi.encodePacked("w8-salt", i));
            bytes32 agent = keccak256(abi.encodePacked("w8-agent", i));
            _revealAs(bidder, aid, bidPrice, salt, agent);
        }
        uint256 gasBefore = gasleft();
        (address winner, uint256 price) = auction.vickreyResult(aid);
        uint256 gasUsed = gasBefore - gasleft();
        emit W8VickreyGas(n, gasUsed);
        address expectedWinner = address(uint160(0xB000 + (n - 1)));
        assertEq(winner, expectedWinner);
        assertEq(price, 0.002 ether);
        // Settle path at the same scale: poster funds the escrow at the
        // second-lowest price.
        vm.warp(uint256(revealD) + 1);
        vm.prank(poster);
        auction.selectWinnerAndFund{value: price}(aid, 0);
        (, bool finalized, , , ) = auction.auctions(aid);
        assertTrue(finalized);
        IExecutionEscrowManager.Escrow memory e = escrow.getEscrow(aid);
        assertEq(e.selectedAgent, expectedWinner);
        assertEq(uint256(e.bidAmount), 0.002 ether);
    }

    // ------------------------------------------------------------------
    // selectWinnerAndFund (close/settle happy path + guards)
    // ------------------------------------------------------------------

    function test_select_happyPath_fundsEscrowAtSecondPrice() public {
        bytes32 aid = keccak256("s1");
        uint64 revealD = _openCommitReveal(aid, 3 ether, 1 ether);
        vm.warp(uint256(revealD) + 1);
        vm.expectEmit(true, true, false, true);
        emit CommitRevealAuction.WinnerSelected(aid, bidderB, 3 ether);
        vm.prank(poster);
        auction.selectWinnerAndFund{value: 3 ether}(aid, 0);
        (, bool finalized, , , ) = auction.auctions(aid);
        assertTrue(finalized);
        IExecutionEscrowManager.Escrow memory e = escrow.getEscrow(aid);
        assertEq(e.poster, poster);
        assertEq(e.selectedAgent, bidderB);
        assertEq(uint256(e.bidAmount), 3 ether);
        assertEq(uint256(e.ethDeposited), 3 ether);
        assertTrue(e.state == IExecutionEscrowManager.ExecutionState.AwaitingStake);
        assertEq(address(escrow).balance, 3 ether);
    }

    function test_select_singleBidder_fundsAtOwnBid() public {
        bytes32 aid = keccak256("s2");
        _open(aid, 60, 60);
        _commitAs(bidderA, aid, 2 ether, SALT_A, AGENT_A);
        (uint64 commitD, uint64 revealD) = _deadlines(aid);
        vm.warp(uint256(commitD) + 1);
        _revealAs(bidderA, aid, 2 ether, SALT_A, AGENT_A);
        vm.warp(uint256(revealD) + 1);
        vm.prank(poster);
        auction.selectWinnerAndFund{value: 2 ether}(aid, 0);
        IExecutionEscrowManager.Escrow memory e = escrow.getEscrow(aid);
        assertEq(e.selectedAgent, bidderA);
        assertEq(uint256(e.bidAmount), 2 ether);
    }

    function test_select_byNonPosterReverts() public {
        bytes32 aid = keccak256("s3");
        uint64 revealD = _openCommitReveal(aid, 3 ether, 1 ether);
        vm.warp(uint256(revealD) + 1);
        vm.deal(anyone, 10 ether); // funded, so the call reaches the poster check
        vm.prank(anyone);
        vm.expectRevert(CommitRevealAuction.NotPoster.selector);
        auction.selectWinnerAndFund{value: 3 ether}(aid, 0);
    }

    function test_select_beforeRevealDeadlineReverts() public {
        bytes32 aid = keccak256("s4");
        _openCommitReveal(aid, 3 ether, 1 ether);
        // Still inside the reveal window's end (not yet past revealDeadline).
        vm.prank(poster);
        vm.expectRevert(CommitRevealAuction.RevealTooEarly.selector);
        auction.selectWinnerAndFund{value: 3 ether}(aid, 0);
    }

    function test_select_atRevealDeadlineReverts() public {
        bytes32 aid = keccak256("s5");
        uint64 revealD = _openCommitReveal(aid, 3 ether, 1 ether);
        // Selection opens strictly AFTER the reveal deadline.
        vm.warp(revealD);
        vm.prank(poster);
        vm.expectRevert(CommitRevealAuction.RevealTooEarly.selector);
        auction.selectWinnerAndFund{value: 3 ether}(aid, 0);
    }

    function test_select_fundingMismatchRevertsAndStaysSelectable() public {
        bytes32 aid = keccak256("s6");
        uint64 revealD = _openCommitReveal(aid, 3 ether, 1 ether);
        vm.warp(uint256(revealD) + 1);
        vm.prank(poster);
        vm.expectRevert(
            abi.encodeWithSelector(
                IExecutionEscrowManager.FundingMismatch.selector, 3 ether, 2 ether
            )
        );
        auction.selectWinnerAndFund{value: 2 ether}(aid, 0);
        (, bool finalized, , , ) = auction.auctions(aid);
        assertFalse(finalized);
        // Correct funding then succeeds — the revert rolled back atomically.
        vm.prank(poster);
        auction.selectWinnerAndFund{value: 3 ether}(aid, 0);
        (, finalized, , , ) = auction.auctions(aid);
        assertTrue(finalized);
    }

    function test_select_twiceReverts() public {
        bytes32 aid = keccak256("s7");
        uint64 revealD = _openCommitReveal(aid, 3 ether, 1 ether);
        vm.warp(uint256(revealD) + 1);
        vm.prank(poster);
        auction.selectWinnerAndFund{value: 3 ether}(aid, 0);
        vm.prank(poster);
        vm.expectRevert(CommitRevealAuction.AlreadyFinalized.selector);
        auction.selectWinnerAndFund{value: 3 ether}(aid, 0);
    }

    function test_select_noEscrowManagerReverts() public {
        CommitRevealAuction bare = new CommitRevealAuction(guardian);
        bytes32 aid = keccak256("s8");
        vm.prank(poster);
        bare.openAuction(aid, 60, 60);
        vm.prank(bidderA);
        bare.commit(aid, _commitment(aid, 1 ether, SALT_A, AGENT_A));
        (,,,, uint64 revealD) = bare.auctions(aid);
        vm.warp(START + 61);
        vm.prank(bidderA);
        bare.reveal(aid, 1 ether, SALT_A, AGENT_A);
        vm.warp(uint256(revealD) + 1);
        vm.prank(poster);
        vm.expectRevert(CommitRevealAuction.NoEscrowManager.selector);
        bare.selectWinnerAndFund{value: 1 ether}(aid, 0);
    }

    function testFuzz_select_fundsEscrowAtSecondPrice(uint256 pA, uint256 pB) public {
        pA = bound(pA, 1, 100 ether);
        pB = bound(pB, 1, 100 ether);
        vm.assume(pA != pB);
        bytes32 aid = keccak256(abi.encodePacked("s9", pA, pB));
        uint64 revealD = _openCommitReveal(aid, pA, pB);
        vm.warp(uint256(revealD) + 1);
        uint256 expectedPrice = pA < pB ? pB : pA;
        address expectedWinner = pA < pB ? bidderA : bidderB;
        vm.prank(poster);
        auction.selectWinnerAndFund{value: expectedPrice}(aid, 0);
        IExecutionEscrowManager.Escrow memory e = escrow.getEscrow(aid);
        assertEq(e.selectedAgent, expectedWinner);
        assertEq(uint256(e.bidAmount), expectedPrice);
        assertEq(address(escrow).balance, expectedPrice);
    }

    // ------------------------------------------------------------------
    // timeout (permissionless close)
    // ------------------------------------------------------------------

    function test_timeout_happyPath_permissionless() public {
        bytes32 aid = keccak256("t1");
        _open(aid, 60, 60);
        (, uint64 revealD) = _deadlines(aid);
        vm.warp(uint256(revealD) + 1);
        vm.expectEmit(true, false, false, true);
        emit CommitRevealAuction.TimedOut(aid);
        vm.prank(anyone);
        auction.timeout(aid);
        (, bool finalized, , , ) = auction.auctions(aid);
        assertTrue(finalized);
    }

    function test_timeout_beforeRevealDeadlineReverts() public {
        bytes32 aid = keccak256("t2");
        _open(aid, 60, 60);
        vm.prank(anyone);
        vm.expectRevert(CommitRevealAuction.TooEarlyToTimeout.selector);
        auction.timeout(aid);
    }

    function test_timeout_atRevealDeadlineReverts() public {
        bytes32 aid = keccak256("t3");
        _open(aid, 60, 60);
        (, uint64 revealD) = _deadlines(aid);
        vm.warp(revealD);
        vm.prank(anyone);
        vm.expectRevert(CommitRevealAuction.TooEarlyToTimeout.selector);
        auction.timeout(aid);
    }

    function test_timeout_unopenedReverts() public {
        vm.prank(anyone);
        vm.expectRevert(CommitRevealAuction.AuctionNotOpen.selector);
        auction.timeout(keccak256("t4"));
    }

    function test_timeout_afterSelectReverts() public {
        bytes32 aid = keccak256("t5");
        uint64 revealD = _openCommitReveal(aid, 3 ether, 1 ether);
        vm.warp(uint256(revealD) + 1);
        vm.prank(poster);
        auction.selectWinnerAndFund{value: 3 ether}(aid, 0);
        vm.prank(anyone);
        vm.expectRevert(CommitRevealAuction.AlreadyFinalized.selector);
        auction.timeout(aid);
    }

    function test_timeout_blocksLaterSelection() public {
        bytes32 aid = keccak256("t6");
        uint64 revealD = _openCommitReveal(aid, 3 ether, 1 ether);
        vm.warp(uint256(revealD) + 1);
        vm.prank(anyone);
        auction.timeout(aid);
        vm.prank(poster);
        vm.expectRevert(CommitRevealAuction.AlreadyFinalized.selector);
        auction.selectWinnerAndFund{value: 3 ether}(aid, 0);
    }

    function testFuzz_timeout_timingBoundary(uint64 lateBy) public {
        lateBy = uint64(bound(lateBy, 1, 30 days));
        bytes32 aid = keccak256(abi.encodePacked("t7", lateBy));
        _open(aid, 60, 60);
        (, uint64 revealD) = _deadlines(aid);
        vm.warp(uint256(revealD) + lateBy);
        vm.prank(anyone);
        auction.timeout(aid);
        (, bool finalized, , , ) = auction.auctions(aid);
        assertTrue(finalized);
    }
}


/// @title CommitRevealAuction lifecycle tests (A+ overhaul, 2026-11-14)
/// @notice Covers the full lifecycle of contracts/CommitRevealAuction.sol:
///         open -> commit -> reveal -> Vickrey selection / timeout, plus
///         the forfeit edges. There are NO non-reveal bonds in this
///         contract (see its NatSpec): an unrevealed commit forfeits only
///         eligibility — it is ignored by vickreyResult and the auction
///         finalizes without it. Slashing of worker stake lives in
///         ExecutionEscrowManager / StakeSlashManager, downstream.
/// @dev    Constants are read from the contract itself
///         (DEFAULT_COMMIT_WINDOW / DEFAULT_REVEAL_WINDOW = 5 minutes);
///         nothing here re-pins the stacked-PR Denim constants, which do
///         not exist on this tree.
contract CommitRevealAuctionOverhaulTest is Test {
    CommitRevealAuction internal auction;
    MockEscrowManager internal escrow;

    address internal guardian = address(0x6A12D);
    address internal poster = address(0x9057);
    address internal bidder1 = address(0xB1D1);
    address internal bidder2 = address(0xB1D2);
    address internal bidder3 = address(0xB1D3);
    address internal stranger = address(0x57AA);

    bytes32 internal constant AUCTION_ID = bytes32(uint256(0xA0C710));

    function setUp() public {
        vm.warp(1_700_000_000);
        auction = new CommitRevealAuction(guardian);
        escrow = new MockEscrowManager();
        vm.deal(poster, 100 ether);
        vm.deal(stranger, 100 ether); // so value-carrying calls reach the auth checks
    }

    // --- helpers -----------------------------------------------------------

    function _agentHash(address who) internal pure returns (bytes32) {
        return keccak256(abi.encodePacked("agent:", who));
    }

    function _commitment(bytes32 auctionId, uint256 price, bytes32 salt, address who)
        internal
        view
        returns (bytes32)
    {
        return keccak256(abi.encodePacked(auctionId, block.chainid, bytes32(price), salt, _agentHash(who)));
    }

    function _salt(address who) internal pure returns (bytes32) {
        return keccak256(abi.encodePacked("salt:", who));
    }

    function _open(bytes32 id, uint64 commitW, uint64 revealW) internal {
        vm.prank(poster);
        auction.openAuction(id, commitW, revealW);
    }

    function _commit(bytes32 id, address who, uint256 price) internal {
        vm.prank(who);
        auction.commit(id, _commitment(id, price, _salt(who), who));
    }

    function _reveal(bytes32 id, address who, uint256 price) internal {
        vm.prank(who);
        auction.reveal(id, price, _salt(who), _agentHash(who));
    }

    function _deadlines(bytes32 id) internal view returns (uint64 commitD, uint64 revealD) {
        (, , , commitD, revealD) = auction.auctions(id);
    }

    // --- open --------------------------------------------------------------

    function test_OpenAuctionDefaults() public {
        _open(AUCTION_ID, 0, 0);
        (bool opened, bool finalized, address p, uint64 commitD, uint64 revealD) = auction.auctions(AUCTION_ID);
        assertTrue(opened);
        assertFalse(finalized);
        assertEq(p, poster);
        assertEq(commitD, uint64(block.timestamp) + 5 minutes);
        assertEq(revealD, uint64(block.timestamp) + 10 minutes);
    }

    function test_OpenAuctionCustomWindows() public {
        _open(AUCTION_ID, 1 hours, 2 hours);
        (uint64 commitD, uint64 revealD) = _deadlines(AUCTION_ID);
        assertEq(commitD, uint64(block.timestamp) + 1 hours);
        assertEq(revealD, uint64(block.timestamp) + 3 hours);
    }

    function test_OpenAuctionTwiceReverts() public {
        _open(AUCTION_ID, 0, 0);
        vm.prank(poster);
        vm.expectRevert(CommitRevealAuction.AlreadyOpened.selector);
        auction.openAuction(AUCTION_ID, 0, 0);
    }

    // --- commit ------------------------------------------------------------

    function test_CommitHappyPath() public {
        _open(AUCTION_ID, 0, 0);
        _commit(AUCTION_ID, bidder1, 100);
        (bytes32 c, bool revealed, uint256 price) = auction.commits(AUCTION_ID, bidder1);
        assertEq(c, _commitment(AUCTION_ID, 100, _salt(bidder1), bidder1));
        assertFalse(revealed);
        assertEq(price, 0);
        assertEq(auction.bidders(AUCTION_ID, 0), bidder1);
    }

    function test_CommitEmptyReverts() public {
        _open(AUCTION_ID, 0, 0);
        vm.prank(bidder1);
        vm.expectRevert(CommitRevealAuction.EmptyCommit.selector);
        auction.commit(AUCTION_ID, bytes32(0));
    }

    function test_CommitTwiceReverts() public {
        _open(AUCTION_ID, 0, 0);
        _commit(AUCTION_ID, bidder1, 100);
        vm.prank(bidder1);
        vm.expectRevert(CommitRevealAuction.AlreadyCommitted.selector);
        auction.commit(AUCTION_ID, _commitment(AUCTION_ID, 90, _salt(bidder1), bidder1));
    }

    function test_CommitUnopenedReverts() public {
        vm.prank(bidder1);
        vm.expectRevert(CommitRevealAuction.AuctionNotOpen.selector);
        auction.commit(AUCTION_ID, bytes32(uint256(1)));
    }

    function test_CommitAtExactDeadlineSucceeds() public {
        _open(AUCTION_ID, 0, 0);
        (uint64 commitD, ) = _deadlines(AUCTION_ID);
        vm.warp(commitD); // guard is `timestamp > deadline`, so == is open
        _commit(AUCTION_ID, bidder1, 100);
    }

    function test_CommitAfterDeadlineReverts() public {
        _open(AUCTION_ID, 0, 0);
        (uint64 commitD, ) = _deadlines(AUCTION_ID);
        vm.warp(commitD + 1);
        vm.prank(bidder1);
        vm.expectRevert(CommitRevealAuction.CommitWindowClosed.selector);
        auction.commit(AUCTION_ID, _commitment(AUCTION_ID, 100, _salt(bidder1), bidder1));
    }

    // --- reveal ------------------------------------------------------------

    function test_RevealTooEarlyAtCommitDeadlineReverts() public {
        _open(AUCTION_ID, 0, 0);
        _commit(AUCTION_ID, bidder1, 100);
        (uint64 commitD, ) = _deadlines(AUCTION_ID);
        vm.warp(commitD); // reveal requires timestamp > commitDeadline
        vm.prank(bidder1);
        vm.expectRevert(CommitRevealAuction.RevealTooEarly.selector);
        auction.reveal(AUCTION_ID, 100, _salt(bidder1), _agentHash(bidder1));
    }

    function test_RevealHappyPath() public {
        _open(AUCTION_ID, 0, 0);
        _commit(AUCTION_ID, bidder1, 100);
        (uint64 commitD, ) = _deadlines(AUCTION_ID);
        vm.warp(commitD + 1);
        _reveal(AUCTION_ID, bidder1, 100);
        (, bool revealed, uint256 price) = auction.commits(AUCTION_ID, bidder1);
        assertTrue(revealed);
        assertEq(price, 100);
    }

    function test_RevealBadPreimageReverts() public {
        _open(AUCTION_ID, 0, 0);
        _commit(AUCTION_ID, bidder1, 100);
        (uint64 commitD, ) = _deadlines(AUCTION_ID);
        vm.warp(commitD + 1);
        vm.prank(bidder1);
        vm.expectRevert(CommitRevealAuction.BadReveal.selector);
        auction.reveal(AUCTION_ID, 99, _salt(bidder1), _agentHash(bidder1));
    }

    function test_RevealNoCommitReverts() public {
        _open(AUCTION_ID, 0, 0);
        (uint64 commitD, ) = _deadlines(AUCTION_ID);
        vm.warp(commitD + 1);
        vm.prank(bidder1);
        vm.expectRevert(CommitRevealAuction.NoCommit.selector);
        auction.reveal(AUCTION_ID, 100, _salt(bidder1), _agentHash(bidder1));
    }

    function test_RevealTwiceReverts() public {
        _open(AUCTION_ID, 0, 0);
        _commit(AUCTION_ID, bidder1, 100);
        (uint64 commitD, ) = _deadlines(AUCTION_ID);
        vm.warp(commitD + 1);
        _reveal(AUCTION_ID, bidder1, 100);
        vm.prank(bidder1);
        vm.expectRevert(CommitRevealAuction.AlreadyRevealed.selector);
        auction.reveal(AUCTION_ID, 100, _salt(bidder1), _agentHash(bidder1));
    }

    function test_RevealAfterDeadlineReverts() public {
        _open(AUCTION_ID, 0, 0);
        _commit(AUCTION_ID, bidder1, 100);
        (, uint64 revealD) = _deadlines(AUCTION_ID);
        vm.warp(revealD + 1);
        vm.prank(bidder1);
        vm.expectRevert(CommitRevealAuction.RevealWindowClosed.selector);
        auction.reveal(AUCTION_ID, 100, _salt(bidder1), _agentHash(bidder1));
    }

    function test_RevealAtExactRevealDeadlineSucceeds() public {
        _open(AUCTION_ID, 0, 0);
        _commit(AUCTION_ID, bidder1, 100);
        (, uint64 revealD) = _deadlines(AUCTION_ID);
        vm.warp(revealD); // guard is `timestamp > deadline`, so == is open
        _reveal(AUCTION_ID, bidder1, 100);
    }

    function test_RevealPriceTooLargeReverts() public {
        uint256 price = uint256(type(uint96).max) + 1;
        _open(AUCTION_ID, 0, 0);
        _commit(AUCTION_ID, bidder1, price);
        (uint64 commitD, ) = _deadlines(AUCTION_ID);
        vm.warp(commitD + 1);
        vm.prank(bidder1);
        vm.expectRevert(CommitRevealAuction.PriceTooLarge.selector);
        auction.reveal(AUCTION_ID, price, _salt(bidder1), _agentHash(bidder1));
    }

    function test_RevealPriceExactlyUint96MaxSucceeds() public {
        uint256 price = type(uint96).max;
        _open(AUCTION_ID, 0, 0);
        _commit(AUCTION_ID, bidder1, price);
        (uint64 commitD, ) = _deadlines(AUCTION_ID);
        vm.warp(commitD + 1);
        _reveal(AUCTION_ID, bidder1, price);
    }

    // --- Vickrey -----------------------------------------------------------

    function _twoBidderAuction(uint256 p1, uint256 p2) internal {
        _open(AUCTION_ID, 0, 0);
        _commit(AUCTION_ID, bidder1, p1);
        _commit(AUCTION_ID, bidder2, p2);
        (uint64 commitD, ) = _deadlines(AUCTION_ID);
        vm.warp(commitD + 1);
        _reveal(AUCTION_ID, bidder1, p1);
        _reveal(AUCTION_ID, bidder2, p2);
    }

    function test_VickreySingleBidderFirstPrice() public {
        _open(AUCTION_ID, 0, 0);
        _commit(AUCTION_ID, bidder1, 100);
        (uint64 commitD, ) = _deadlines(AUCTION_ID);
        vm.warp(commitD + 1);
        _reveal(AUCTION_ID, bidder1, 100);
        (address winner, uint256 price) = auction.vickreyResult(AUCTION_ID);
        assertEq(winner, bidder1);
        assertEq(price, 100); // sole bidder falls back to first-price
    }

    function test_VickreyTwoBiddersSecondPrice() public {
        _twoBidderAuction(100, 150);
        (address winner, uint256 price) = auction.vickreyResult(AUCTION_ID);
        assertEq(winner, bidder1);
        assertEq(price, 150);
    }

    function test_VickreyTieBreaksToEarliestCommitter() public {
        _twoBidderAuction(100, 100);
        (address winner, uint256 price) = auction.vickreyResult(AUCTION_ID);
        assertEq(winner, bidder1);
        assertEq(price, 100);
    }

    function test_VickreyUnrevealedCommitIgnored() public {
        _open(AUCTION_ID, 0, 0);
        _commit(AUCTION_ID, bidder1, 100);
        _commit(AUCTION_ID, bidder2, 50); // cheaper, but never reveals
        (uint64 commitD, ) = _deadlines(AUCTION_ID);
        vm.warp(commitD + 1);
        _reveal(AUCTION_ID, bidder1, 100);
        (address winner, uint256 price) = auction.vickreyResult(AUCTION_ID);
        assertEq(winner, bidder1);
        assertEq(price, 100);
    }

    function test_VickreyNoRevealedBidsReverts() public {
        _open(AUCTION_ID, 0, 0);
        _commit(AUCTION_ID, bidder1, 100);
        vm.expectRevert(CommitRevealAuction.NoRevealedBids.selector);
        auction.vickreyResult(AUCTION_ID);
    }

    // --- timeout / forfeit -------------------------------------------------

    function test_TimeoutTooEarlyReverts() public {
        _open(AUCTION_ID, 0, 0);
        (, uint64 revealD) = _deadlines(AUCTION_ID);
        vm.warp(revealD); // timeout requires timestamp > revealDeadline
        vm.expectRevert(CommitRevealAuction.TooEarlyToTimeout.selector);
        auction.timeout(AUCTION_ID);
    }

    function test_TimeoutPermissionlessAfterDeadline() public {
        _open(AUCTION_ID, 0, 0);
        _commit(AUCTION_ID, bidder1, 100); // never reveals: forfeits eligibility
        (, uint64 revealD) = _deadlines(AUCTION_ID);
        vm.warp(revealD + 1);
        vm.prank(stranger); // anyone may finalize
        auction.timeout(AUCTION_ID);
        (, bool finalized, , , ) = auction.auctions(AUCTION_ID);
        assertTrue(finalized);
        // Finalized is terminal for every other path.
        vm.expectRevert(CommitRevealAuction.AlreadyFinalized.selector);
        auction.timeout(AUCTION_ID);
        vm.prank(bidder1);
        vm.expectRevert(CommitRevealAuction.AlreadyFinalized.selector);
        auction.commit(AUCTION_ID, bytes32(uint256(1)));
    }

    function test_TimeoutUnopenedReverts() public {
        vm.expectRevert(CommitRevealAuction.AuctionNotOpen.selector);
        auction.timeout(AUCTION_ID);
    }

    // --- selection ---------------------------------------------------------

    function test_SelectWinnerAndFundHappyPath() public {
        auction.setEscrowManager(address(escrow));
        _twoBidderAuction(100, 150);
        (, uint64 revealD) = _deadlines(AUCTION_ID);
        vm.warp(revealD + 1);
        vm.prank(poster);
        auction.selectWinnerAndFund{value: 150}(AUCTION_ID, 0);
        assertEq(escrow.calls(), 1);
        assertEq(escrow.lastWorker(), bidder1);
        assertEq(escrow.lastBid(), 150); // funded at the Vickrey (second) price
        assertEq(escrow.lastValue(), 150);
        (, bool finalized, , , ) = auction.auctions(AUCTION_ID);
        assertTrue(finalized);
    }

    function test_SelectWinnerNotPosterReverts() public {
        auction.setEscrowManager(address(escrow));
        _twoBidderAuction(100, 150);
        (, uint64 revealD) = _deadlines(AUCTION_ID);
        vm.warp(revealD + 1);
        vm.prank(stranger);
        vm.expectRevert(CommitRevealAuction.NotPoster.selector);
        auction.selectWinnerAndFund{value: 150}(AUCTION_ID, 0);
    }

    function test_SelectWinnerTooEarlyReverts() public {
        auction.setEscrowManager(address(escrow));
        _twoBidderAuction(100, 150);
        (, uint64 revealD) = _deadlines(AUCTION_ID);
        vm.warp(revealD); // selection requires timestamp > revealDeadline
        vm.prank(poster);
        vm.expectRevert(CommitRevealAuction.RevealTooEarly.selector);
        auction.selectWinnerAndFund{value: 150}(AUCTION_ID, 0);
    }

    function test_SelectWinnerNoEscrowManagerReverts() public {
        _twoBidderAuction(100, 150);
        (, uint64 revealD) = _deadlines(AUCTION_ID);
        vm.warp(revealD + 1);
        vm.prank(poster);
        vm.expectRevert(CommitRevealAuction.NoEscrowManager.selector);
        auction.selectWinnerAndFund{value: 150}(AUCTION_ID, 0);
    }

    function test_SetEscrowManagerOnlyOwner() public {
        vm.prank(stranger);
        vm.expectRevert(CommitRevealAuction.NotOwner.selector);
        auction.setEscrowManager(address(escrow));
    }

    // --- pause scope (ScopedPausable discipline) ---------------------------

    function test_PauseBlocksCommitButNotRevealOrTimeout() public {
        _open(AUCTION_ID, 0, 0);
        _commit(AUCTION_ID, bidder1, 100);
        vm.prank(guardian);
        auction.setPause(true);
        // New state injection is frozen...
        vm.prank(bidder2);
        vm.expectRevert(); // EmergencyPaused
        auction.commit(AUCTION_ID, _commitment(AUCTION_ID, 90, _salt(bidder2), bidder2));
        // ...but in-flight settlement paths are NEVER pausable.
        (uint64 commitD, uint64 revealD) = _deadlines(AUCTION_ID);
        vm.warp(commitD + 1);
        _reveal(AUCTION_ID, bidder1, 100);
        vm.warp(revealD + 1);
        auction.timeout(AUCTION_ID);
    }

    // --- fuzz --------------------------------------------------------------

    function testFuzz_VickreySecondPrice(uint96 rawA, uint96 rawB) public {
        uint256 a = bound(uint256(rawA), 1, 1e24);
        uint256 b = bound(uint256(rawB), 1, 1e24);
        vm.assume(a != b);
        _twoBidderAuction(a, b);
        (address winner, uint256 price) = auction.vickreyResult(AUCTION_ID);
        assertEq(winner, a < b ? bidder1 : bidder2);
        assertEq(price, a < b ? b : a);
    }

    function testFuzz_RevealRejectsOversizedPrice(uint256 price) public {
        price = bound(price, uint256(type(uint96).max) + 1, type(uint256).max);
        _open(AUCTION_ID, 0, 0);
        _commit(AUCTION_ID, bidder1, price);
        (uint64 commitD, ) = _deadlines(AUCTION_ID);
        vm.warp(commitD + 1);
        vm.prank(bidder1);
        vm.expectRevert(CommitRevealAuction.PriceTooLarge.selector);
        auction.reveal(AUCTION_ID, price, _salt(bidder1), _agentHash(bidder1));
    }

    function testFuzz_WindowBoundaries(uint64 commitW, uint64 revealW) public {
        commitW = uint64(bound(uint256(commitW), 1, 7 days));
        revealW = uint64(bound(uint256(revealW), 1, 7 days));
        _open(AUCTION_ID, commitW, revealW);
        // Read the deadlines back from the contract (never recompute them
        // from a locally captured t0 — the EVM clock can drift by a second
        // between setUp and the fuzz run). Last valid commit instant, last
        // valid reveal instant, first valid timeout instant.
        (uint64 commitD, uint64 revealD) = _deadlines(AUCTION_ID);
        assertEq(commitD, uint64(block.timestamp) + commitW);
        assertEq(revealD, commitD + revealW);
        vm.warp(commitD);
        _commit(AUCTION_ID, bidder1, 100);
        vm.warp(revealD);
        _reveal(AUCTION_ID, bidder1, 100);
        vm.warp(revealD + 1);
        auction.timeout(AUCTION_ID);
    }
}
