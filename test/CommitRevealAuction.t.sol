// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {CommitRevealAuction} from "../contracts/CommitRevealAuction.sol";
import {ExecutionEscrowManager} from "../contracts/ExecutionEscrowManager.sol";
import {IExecutionEscrowManager} from "../contracts/IExecutionEscrowManager.sol";

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
