// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title OptimisticBatchSettlement
/// @notice Posts Merkle roots of off-chain AXM / merit / assignment events
///         to Base. A 3000-block challenge window lets anyone freeze a root
///         by proving a posted leaf. Micro-tasks never each pay L2 gas.
/// @dev Companion Python simulator: marketplace/optimistic.
///      Base Denim: 200ms per block -> 3000 blocks ~= 10 minutes of wall
///      time (was 300 blocks at 2s/block before Denim).
///
///      W-190 hardening: a challenge must be substantive -- it has to name
///      a leaf position inside the batch (leafIndex < eventCount) and the
///      Merkle proof must actually sit at that position (the index implied
///      by the proof's sides must equal leafIndex). A challenged batch is
///      never frozen forever: the treasury adjudicates via
///      resolveChallenge() (fraud upheld -> batch rejected; fraud not
///      shown -> challenge dismissed, batch pending again), and if no
///      adjudication lands within RESOLUTION_BLOCKS after the challenge
///      window closes, anyone may expireChallenge() to restore the batch
///      to pending so it can finalize.
contract OptimisticBatchSettlement {
    uint256 public constant CHALLENGE_BLOCKS = 3000;
    uint256 public constant RESOLUTION_BLOCKS = 3000;
    address public immutable treasury;
    address public operator;

    struct Batch {
        bytes32 root;
        uint256 eventCount;
        uint256 submittedBlock;
        uint8 status; // 0 pending, 1 challenged, 2 finalized, 3 rejected
        address challenger;
    }

    mapping(uint256 => Batch) public batches;
    uint256 public nextBatchId;

    event RootSubmitted(uint256 indexed batchId, bytes32 root, uint256 eventCount);
    event Challenged(uint256 indexed batchId, address challenger, uint256 leafIndex);
    event Finalized(uint256 indexed batchId, bytes32 root);
    event ChallengeResolved(uint256 indexed batchId, address challenger, bool fraudProven);
    event ChallengeExpired(uint256 indexed batchId, address challenger);

    error WindowOpen();
    error WindowClosed();
    error NotPending();
    error LeafMatches();
    error BadProof();
    error NotOperator();
    error LeafIndexOutOfRange();
    error ProofIndexMismatch();
    error NotTreasury();
    error NotChallenged();
    error ResolutionOpen();

    constructor(address treasury_, address operator_) {
        treasury = treasury_;
        operator = operator_;
    }

    function submitRoot(bytes32 root, uint256 eventCount) external returns (uint256 batchId) {
        if (msg.sender != operator) revert NotOperator();
        batchId = nextBatchId++;
        batches[batchId] = Batch({
            root: root,
            eventCount: eventCount,
            submittedBlock: block.number,
            status: 0,
            challenger: address(0)
        });
        emit RootSubmitted(batchId, root, eventCount);
    }

    /// @dev `proof` is sibling hashes; `sides` 0 = sibling on the right, 1 = left.
    ///      W-190: the challenge must identify a real disputed item -- the
    ///      leaf index must be inside the batch and the proof must verify
    ///      at exactly that index (the position implied by `sides`).
    function challenge(
        uint256 batchId,
        uint256 leafIndex,
        bytes32 postedLeaf,
        bytes32 claimedLeaf,
        bytes32[] calldata proof,
        uint8[] calldata sides
    ) external {
        Batch storage batch = batches[batchId];
        if (batch.status != 0) revert NotPending();
        if (block.number > batch.submittedBlock + CHALLENGE_BLOCKS) revert WindowClosed();
        if (postedLeaf == claimedLeaf) revert LeafMatches();
        if (leafIndex >= batch.eventCount) revert LeafIndexOutOfRange();
        if (proof.length != sides.length) revert BadProof();
        if (!_verify(postedLeaf, proof, sides, batch.root)) revert BadProof();
        if (_impliedIndex(sides) != leafIndex) revert ProofIndexMismatch();
        batch.status = 1;
        batch.challenger = msg.sender;
        emit Challenged(batchId, msg.sender, leafIndex);
    }

    /// @notice Adjudicate a live challenge. Treasury only: the treasury is
    ///         the party independent of the operator who posted the batch.
    /// @param fraudProven true -> the batch is rejected (terminal);
    ///        false -> the challenge is dismissed and the batch returns to
    ///        pending (it finalizes once the challenge window has closed).
    function resolveChallenge(uint256 batchId, bool fraudProven) external {
        if (msg.sender != treasury) revert NotTreasury();
        Batch storage batch = batches[batchId];
        if (batch.status != 1) revert NotChallenged();
        address challenger = batch.challenger;
        if (fraudProven) {
            batch.status = 3;
        } else {
            batch.status = 0;
            batch.challenger = address(0);
        }
        emit ChallengeResolved(batchId, challenger, fraudProven);
    }

    /// @notice Permissionless liveness backstop: a challenge the treasury
    ///         never adjudicated expires once the challenge window plus
    ///         RESOLUTION_BLOCKS have passed, returning the batch to
    ///         pending so it can finalize. A challenged batch can never
    ///         be frozen permanently.
    function expireChallenge(uint256 batchId) external {
        Batch storage batch = batches[batchId];
        if (batch.status != 1) revert NotChallenged();
        if (block.number <= batch.submittedBlock + CHALLENGE_BLOCKS + RESOLUTION_BLOCKS) {
            revert ResolutionOpen();
        }
        address challenger = batch.challenger;
        batch.status = 0;
        batch.challenger = address(0);
        emit ChallengeExpired(batchId, challenger);
    }

    function finalize(uint256 batchId) external {
        Batch storage batch = batches[batchId];
        if (batch.status != 0) revert NotPending();
        if (block.number < batch.submittedBlock + CHALLENGE_BLOCKS) revert WindowOpen();
        batch.status = 2;
        emit Finalized(batchId, batch.root);
    }

    /// @dev Leaf position implied by a Merkle proof path: at level i the
    ///      leaf's index bit is 1 iff the sibling sits on the left
    ///      (sides[i] == 1). Binding this to the claimed leafIndex forces a
    ///      challenge to prove the leaf AT the position it disputes.
    function _impliedIndex(uint8[] calldata sides) internal pure returns (uint256 index) {
        for (uint256 i = 0; i < sides.length; i++) {
            if (sides[i] == 1) {
                index |= uint256(1) << i;
            } else if (sides[i] != 0) {
                revert BadProof();
            }
        }
    }

    function _verify(
        bytes32 leaf,
        bytes32[] calldata proof,
        uint8[] calldata sides,
        bytes32 root
    ) internal pure returns (bool) {
        bytes32 node = leaf;
        for (uint256 i = 0; i < proof.length; i++) {
            if (sides[i] == 0) {
                node = keccak256(abi.encodePacked(node, proof[i]));
            } else {
                node = keccak256(abi.encodePacked(proof[i], node));
            }
        }
        return node == root;
    }
}
