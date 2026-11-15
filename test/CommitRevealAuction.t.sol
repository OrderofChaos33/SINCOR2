// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {CommitRevealAuction} from "../contracts/CommitRevealAuction.sol";

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
contract CommitRevealAuctionTest is Test {
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
