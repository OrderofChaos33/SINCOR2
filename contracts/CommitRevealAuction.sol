// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title CommitRevealAuction
/// @notice Publishes Hash(price ‖ salt ‖ keccak(agentId)) during the commit
///         window so task parameters and bids are not MEV-readable. Reveal
///         is a second transaction (or an off-chain message checked against
///         this commitment).
contract CommitRevealAuction {
    struct Commit {
        bytes32 commit;
        bool revealed;
        uint256 price;
    }

    mapping(bytes32 => mapping(address => Commit)) public commits; // auctionId => bidder

    event Committed(bytes32 indexed auctionId, address indexed bidder, bytes32 commit);
    event Revealed(bytes32 indexed auctionId, address indexed bidder, uint256 price);

    error AlreadyCommitted();
    error NoCommit();
    error AlreadyRevealed();
    error BadReveal();

    function commit(bytes32 auctionId, bytes32 commitHash) external {
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
        Commit storage entry = commits[auctionId][msg.sender];
        if (entry.commit == bytes32(0)) revert NoCommit();
        if (entry.revealed) revert AlreadyRevealed();
        bytes32 expected = keccak256(abi.encodePacked(bytes32(price), salt, agentIdHash));
        if (expected != entry.commit) revert BadReveal();
        entry.revealed = true;
        entry.price = price;
        emit Revealed(auctionId, msg.sender, price);
    }
}
