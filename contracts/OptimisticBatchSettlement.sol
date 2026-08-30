// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title OptimisticBatchSettlement
/// @notice Posts Merkle roots of off-chain AXM / merit / assignment events
///         to Base. A 300-block challenge window lets anyone freeze a root
///         by proving a posted leaf. Micro-tasks never each pay L2 gas.
/// @dev Companion Python simulator: marketplace/optimistic.
contract OptimisticBatchSettlement {
    uint256 public constant CHALLENGE_BLOCKS = 300;
    address public immutable treasury;
    address public operator;

    struct Batch {
        bytes32 root;
        uint256 eventCount;
        uint256 submittedBlock;
        uint8 status; // 0 pending, 1 challenged, 2 finalized
        address challenger;
    }

    mapping(uint256 => Batch) public batches;
    uint256 public nextBatchId;

    event RootSubmitted(uint256 indexed batchId, bytes32 root, uint256 eventCount);
    event Challenged(uint256 indexed batchId, address challenger, uint256 leafIndex);
    event Finalized(uint256 indexed batchId, bytes32 root);

    error WindowOpen();
    error WindowClosed();
    error NotPending();
    error LeafMatches();
    error BadProof();
    error NotOperator();

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
        if (!_verify(postedLeaf, proof, sides, batch.root)) revert BadProof();
        batch.status = 1;
        batch.challenger = msg.sender;
        emit Challenged(batchId, msg.sender, leafIndex);
    }

    function finalize(uint256 batchId) external {
        Batch storage batch = batches[batchId];
        if (batch.status != 0) revert NotPending();
        if (block.number < batch.submittedBlock + CHALLENGE_BLOCKS) revert WindowOpen();
        batch.status = 2;
        emit Finalized(batchId, batch.root);
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
