// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title CommitRevealAuction
/// @notice Sealed-bid commit/reveal with onchain deadlines.
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
///           so front-running an open is impractical.
///         - `timeout` is permissionless: anyone may finalize the auction
///           after the reveal deadline. Unrevealed commits are simply
///           ignored — there are no non-reveal bonds.
///         - A stalled coordinator can no longer leave commits hanging.
contract CommitRevealAuction {
    /// @dev Default windows when openAuction is called with 0.
    uint64 public constant DEFAULT_COMMIT_WINDOW = 5 minutes;
    uint64 public constant DEFAULT_REVEAL_WINDOW = 5 minutes;

    struct Auction {
        bool opened;
        bool finalized;
        uint64 commitDeadline;
        uint64 revealDeadline;
    }

    struct Commit {
        bytes32 commit;
        bool revealed;
        uint256 price;
    }

    mapping(bytes32 => Auction) public auctions; // auctionId => timing
    mapping(bytes32 => mapping(address => Commit)) public commits; // auctionId => bidder

    event AuctionOpened(bytes32 indexed auctionId, uint64 commitDeadline, uint64 revealDeadline);
    event Committed(bytes32 indexed auctionId, address indexed bidder, bytes32 commit);
    event Revealed(bytes32 indexed auctionId, address indexed bidder, uint256 price);
    event TimedOut(bytes32 indexed auctionId);

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

    /// @notice Open an auction's timing. Permissionless; first call wins.
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

    /// @notice Finalize the auction after the reveal deadline. Permissionless.
    ///         Unrevealed commits are ignored; there is nothing to refund
    ///         because commits carry no bonds.
    function timeout(bytes32 auctionId) external {
        Auction storage a = auctions[auctionId];
        if (!a.opened) revert AuctionNotOpen();
        if (a.finalized) revert AlreadyFinalized();
        if (block.timestamp <= a.revealDeadline) revert TooEarlyToTimeout();
        a.finalized = true;
        emit TimedOut(auctionId);
    }
}
