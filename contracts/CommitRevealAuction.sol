// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "./IExecutionEscrowManager.sol";

/// @title CommitRevealAuction
/// @notice Sealed-bid commit/reveal with onchain deadlines, plus the
///         selection bridge into the execution-escrow layer.
///         Publishes Hash(price ‖ salt ‖ keccak(agentId)) during the commit
///         window so task parameters and bids are not MEV-readable. Reveal
///         is a second transaction (or an off-chain message checked against
///         this commitment).
///
///         Timing (ratified 2026-09-25, docs/ops/AUCTION_SECURITY_DECISIONS.md):
///         - 5-minute commit window + 5-minute reveal window, per-auction
///           parameters (pass 0 for the defaults).
///         - `openAuction` is permissionless, first call wins. Auction ids
///           are unpredictable (offchain id = keccak(taskId ‖ random salt)),
///           so front-running an open is impractical. The opener is recorded
///           as the poster (the hiring party).
///         - `timeout` is permissionless: anyone may finalize the auction
///           after the reveal deadline *plus the selection window*, giving
///           the poster a grief-free grace period to select a winner.
///           Unrevealed commits are simply ignored — there are no
///           non-reveal bonds.
///         - A stalled coordinator can no longer leave commits hanging.
///
///         Selection (added 2026-09-26):
///         - Procurement Vickrey: the lowest revealed bidder wins and the
///           escrow is funded at the *second-lowest* revealed price. With a
///           single revealed bid the winner pays their own bid (first-price
///           fallback). Ties break to the earliest committer.
///         - `selectWinnerAndFund` is poster-only and atomically initializes
///           the downstream `ExecutionEscrowManager` escrow, forwarding the
///           poster's ETH. The escrow enforces exact two-sided funding, so a
///           mispriced funding transaction reverts and the auction stays
///           selectable.
///         - `vickreyResult` is a view so the poster's agent can read the
///           exact funding amount before submitting.
contract CommitRevealAuction {
    /// @dev Default windows when openAuction is called with 0.
    uint64 public constant DEFAULT_COMMIT_WINDOW = 5 minutes;
    uint64 public constant DEFAULT_REVEAL_WINDOW = 5 minutes;
    /// @dev Grace period after the reveal deadline during which only the
    ///      poster may select a winner. Permissionless `timeout` opens after.
    uint64 public constant SELECTION_WINDOW = 1 hours;

    struct Auction {
        bool opened;
        bool finalized;
        address poster;
        uint64 commitDeadline;
        uint64 revealDeadline;
    }

    struct Commit {
        bytes32 commit;
        bool revealed;
        uint256 price;
    }

    /// @dev Execution-layer parameters applied when an escrow is opened.
    ///      Owner-settable; chosen per deployment, not per auction (v1).
    ///      Mirrors the escrow's duration fields; minStakeBps is immutable
    ///      on the escrow itself (set at its construction).
    struct ExecutionParams {
        uint32 executionDuration;
        uint32 disputeWindowDuration;
        uint32 adjudicationWindowDuration;
        uint32 stakeDepositWindow;
    }

    address public owner;
    IExecutionEscrowManager public escrowManager;
    ExecutionParams public executionParams;

    mapping(bytes32 => Auction) public auctions; // auctionId => timing
    mapping(bytes32 => mapping(address => Commit)) public commits; // auctionId => bidder
    mapping(bytes32 => address[]) public bidders; // auctionId => committers (Vickrey enumeration)

    event AuctionOpened(bytes32 indexed auctionId, uint64 commitDeadline, uint64 revealDeadline);
    event Committed(bytes32 indexed auctionId, address indexed bidder, bytes32 commit);
    event Revealed(bytes32 indexed auctionId, address indexed bidder, uint256 price);
    event WinnerSelected(bytes32 indexed auctionId, address indexed worker, uint256 price);
    event TimedOut(bytes32 indexed auctionId);
    event EscrowManagerSet(address indexed escrowManager);
    event ExecutionParamsSet(
        uint32 executionDuration,
        uint32 disputeWindowDuration,
        uint32 adjudicationWindowDuration,
        uint32 stakeDepositWindow
    );

    error AlreadyOpened();
    error AuctionNotOpen();
    error AlreadyFinalized();
    error CommitWindowClosed();
    error RevealWindowClosed();
    error RevealTooEarly();
    error TooEarlyToTimeout();
    error AlreadyCommitted();
    error NoCommit();
    error AlreadyRevealed();
    error BadReveal();
    error NotPoster();
    error NoRevealedBids();
    error NoEscrowManager();
    error NotOwner();

    constructor() {
        owner = msg.sender;
        // Sane defaults; the owner tunes them per deployment.
        executionParams = ExecutionParams({
            executionDuration: 3 days,
            disputeWindowDuration: 1 days,
            adjudicationWindowDuration: 2 days,
            stakeDepositWindow: 1 days
        });
    }

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    /// @notice Point this auction core at its execution-escrow manager.
    /// @dev The escrow's constructor must name this contract as auctionCore.
    function setEscrowManager(address manager) external onlyOwner {
        escrowManager = IExecutionEscrowManager(manager);
        emit EscrowManagerSet(manager);
    }

    /// @notice Tune the execution-layer parameters applied at selection.
    function setExecutionParams(ExecutionParams calldata p) external onlyOwner {
        executionParams = p;
        emit ExecutionParamsSet(
            p.executionDuration, p.disputeWindowDuration, p.adjudicationWindowDuration, p.stakeDepositWindow
        );
    }

    /// @notice Open an auction's timing. Permissionless; first call wins.
    ///         The opener is recorded as the poster (hiring party).
    /// @param commitWindow Seconds for the commit phase (0 = 5-minute default).
    /// @param revealWindow Seconds for the reveal phase (0 = 5-minute default).
    function openAuction(bytes32 auctionId, uint64 commitWindow, uint64 revealWindow) external {
        Auction storage a = auctions[auctionId];
        if (a.opened) revert AlreadyOpened();
        uint64 now_ = uint64(block.timestamp);
        uint64 commitW = commitWindow == 0 ? DEFAULT_COMMIT_WINDOW : commitWindow;
        uint64 revealW = revealWindow == 0 ? DEFAULT_REVEAL_WINDOW : revealWindow;
        a.opened = true;
        a.finalized = false;
        a.poster = msg.sender;
        a.commitDeadline = now_ + commitW;
        a.revealDeadline = now_ + commitW + revealW;
        emit AuctionOpened(auctionId, a.commitDeadline, a.revealDeadline);
    }

    function _liveAuction(bytes32 auctionId) private view returns (Auction storage a) {
        a = auctions[auctionId];
        if (!a.opened) revert AuctionNotOpen();
        if (a.finalized) revert AlreadyFinalized();
    }

    function commit(bytes32 auctionId, bytes32 commitHash) external {
        Auction storage a = _liveAuction(auctionId);
        if (block.timestamp > a.commitDeadline) revert CommitWindowClosed();
        if (commits[auctionId][msg.sender].commit != bytes32(0)) revert AlreadyCommitted();
        commits[auctionId][msg.sender] = Commit({commit: commitHash, revealed: false, price: 0});
        bidders[auctionId].push(msg.sender);
        emit Committed(auctionId, msg.sender, commitHash);
    }

    function reveal(
        bytes32 auctionId,
        uint256 price,
        bytes32 salt,
        bytes32 agentIdHash
    ) external {
        Auction storage a = _liveAuction(auctionId);
        if (block.timestamp <= a.commitDeadline) revert RevealTooEarly();
        if (block.timestamp > a.revealDeadline) revert RevealWindowClosed();
        Commit storage entry = commits[auctionId][msg.sender];
        if (entry.commit == bytes32(0)) revert NoCommit();
        if (entry.revealed) revert AlreadyRevealed();
        bytes32 expected = keccak256(abi.encodePacked(bytes32(price), salt, agentIdHash));
        if (expected != entry.commit) revert BadReveal();
        entry.revealed = true;
        entry.price = price;
        emit Revealed(auctionId, msg.sender, price);
    }

    /// @notice Compute the Vickrey outcome: lowest revealed bidder wins at
    ///         the second-lowest revealed price. Single-bid auctions fall
    ///         back to first-price. Ties break to the earliest committer.
    /// @return winner The winning worker address.
    /// @return price  The escrow funding amount (second-lowest bid).
    function vickreyResult(bytes32 auctionId) public view returns (address winner, uint256 price) {
        Auction storage a = auctions[auctionId];
        if (!a.opened) revert AuctionNotOpen();
        address[] storage list = bidders[auctionId];
        uint256 lowest = type(uint256).max;
        uint256 second = type(uint256).max;
        for (uint256 i = 0; i < list.length; i++) {
            Commit storage c = commits[auctionId][list[i]];
            if (!c.revealed) continue;
            if (c.price < lowest) {
                second = lowest;
                lowest = c.price;
                winner = list[i];
            } else if (c.price < second) {
                second = c.price;
            }
        }
        if (winner == address(0)) revert NoRevealedBids();
        // Sole bidder: second stays at max; fall back to their own bid.
        price = second == type(uint256).max ? lowest : second;
    }

    /// @notice Select the Vickrey winner and atomically open the execution
    ///         escrow, forwarding the poster's ETH. Poster-only, after the
    ///         reveal deadline.
    /// @param creditToApply Amount of the poster's re-auction-fund ledger to
    ///        apply. `msg.value + creditToApply` must equal the Vickrey price
    ///        exactly or the escrow reverts.
    function selectWinnerAndFund(bytes32 auctionId, uint96 creditToApply) external payable {
        Auction storage a = _liveAuction(auctionId);
        if (msg.sender != a.poster) revert NotPoster();
        if (block.timestamp <= a.revealDeadline) revert RevealTooEarly();
        if (address(escrowManager) == address(0)) revert NoEscrowManager();
        (address winner, uint256 price) = vickreyResult(auctionId);
        a.finalized = true;
        emit WinnerSelected(auctionId, winner, price);
        ExecutionParams storage p = executionParams;
        // Escrow id reuses the auction id: exactly one escrow per auction.
        // The escrow enforces exact funding; if this call reverts (e.g. the
        // poster mispriced msg.value + creditToApply), the whole transaction
        // rolls back atomically, including the finalization above.
        escrowManager.initializeEscrow{value: msg.value}(
            auctionId,
            a.poster,
            winner,
            uint96(price),
            creditToApply,
            p.executionDuration,
            p.disputeWindowDuration,
            p.adjudicationWindowDuration,
            p.stakeDepositWindow
        );
    }

    /// @notice Finalize the auction after the reveal deadline plus the
    ///         selection window. Permissionless. Unrevealed commits are
    ///         ignored; there is nothing to refund because commits carry no
    ///         bonds. If the poster selected a winner first, this reverts.
    function timeout(bytes32 auctionId) external {
        Auction storage a = auctions[auctionId];
        if (!a.opened) revert AuctionNotOpen();
        if (a.finalized) revert AlreadyFinalized();
        if (block.timestamp <= a.revealDeadline + SELECTION_WINDOW) revert TooEarlyToTimeout();
        a.finalized = true;
        emit TimedOut(auctionId);
    }
}
