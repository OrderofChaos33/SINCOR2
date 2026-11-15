// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {OptimisticBatchSettlement} from "../contracts/OptimisticBatchSettlement.sol";

/**
 * @notice Dedicated suite for OptimisticBatchSettlement (wave 2 blind-spot
 *         remediation). The contract moves NO value by design: these tests
 *         pin the state machine and the root/settlement binding instead --
 *         operator-only submission, sequential batch ids, Merkle challenge
 *         proofs, and the challenge/finalize window edges.
 *
 *         Boundary semantics (read from the contract, pinned exactly):
 *         - challenge() reverts WindowClosed only when
 *           block.number > submittedBlock + CHALLENGE_BLOCKS, so a challenge
 *           AT submittedBlock + 3000 is still valid.
 *         - finalize() reverts WindowOpen only when
 *           block.number < submittedBlock + CHALLENGE_BLOCKS, so finalize
 *           AT submittedBlock + 3000 also succeeds.
 *         Both transitions are therefore legal in the exact boundary block;
 *         whichever executes first wins (the loser sees NotPending).
 */
contract OptimisticBatchSettlementTest is Test {
    event RootSubmitted(uint256 indexed batchId, bytes32 root, uint256 eventCount);
    event Challenged(uint256 indexed batchId, address challenger, uint256 leafIndex);
    event Finalized(uint256 indexed batchId, bytes32 root);

    OptimisticBatchSettlement public obs;
    address public treasury = address(0x7EA5);
    address public operator = address(0x09E2);
    address public anyone = address(0xCA11);

    uint256 internal constant START_BLOCK = 1000;

    bytes32 internal leafA = keccak256("event-a");
    bytes32 internal leafB = keccak256("event-b");
    bytes32 internal fraudLeaf = keccak256("not-the-posted-leaf");

    function setUp() public {
        obs = new OptimisticBatchSettlement(treasury, operator);
        vm.roll(START_BLOCK);
    }

    // --- helpers ----------------------------------------------------------

    /// @dev Root of the 2-leaf tree (leafA, leafB).
    function _root2() internal view returns (bytes32) {
        return keccak256(abi.encodePacked(leafA, leafB));
    }

    /// @dev Submit the 2-leaf batch as the operator; returns (id, root).
    function _submit2() internal returns (uint256 id, bytes32 root) {
        root = _root2();
        vm.prank(operator);
        id = obs.submitRoot(root, 2);
    }

    function _batch(uint256 id)
        internal
        view
        returns (bytes32 root, uint256 count, uint256 submitted, uint8 status, address challenger)
    {
        return obs.batches(id);
    }

    function _proofA() internal view returns (bytes32[] memory proof, uint8[] memory sides) {
        proof = new bytes32[](1);
        proof[0] = leafB;
        sides = new uint8[](1);
        sides[0] = 0; // sibling on the right
    }

    // --- submission ---------------------------------------------------------

    function test_ConstructorStoresTreasuryAndOperator() public view {
        assertEq(obs.treasury(), treasury);
        assertEq(obs.operator(), operator);
        assertEq(obs.nextBatchId(), 0);
        assertEq(obs.CHALLENGE_BLOCKS(), 3000);
    }

    function test_SubmitRoot_BindsRootCountAndBlock() public {
        vm.expectEmit(true, false, false, true);
        emit RootSubmitted(0, _root2(), 2);
        (uint256 id, ) = _submit2();

        assertEq(id, 0, "first batch id is 0");
        (bytes32 root, uint256 count, uint256 submitted, uint8 status, address challenger) =
            _batch(id);
        assertEq(root, _root2(), "root bound");
        assertEq(count, 2, "eventCount bound");
        assertEq(submitted, START_BLOCK, "submittedBlock is current block");
        assertEq(status, 0, "pending");
        assertEq(challenger, address(0), "no challenger yet");
        assertEq(obs.nextBatchId(), 1);
    }

    function test_SubmitRoot_NonOperatorReverts() public {
        vm.prank(anyone);
        vm.expectRevert(OptimisticBatchSettlement.NotOperator.selector);
        obs.submitRoot(_root2(), 2);
        assertEq(obs.nextBatchId(), 0, "no batch consumed");
    }

    function test_SubmitRoot_SequentialIdsAreIndependent() public {
        (uint256 id0, ) = _submit2();
        bytes32 otherRoot = keccak256("another-batch");
        vm.prank(operator);
        uint256 id1 = obs.submitRoot(otherRoot, 7);

        assertEq(id0, 0);
        assertEq(id1, 1);
        (bytes32 r0, uint256 c0, , uint8 s0, ) = _batch(id0);
        (bytes32 r1, uint256 c1, , uint8 s1, ) = _batch(id1);
        assertEq(r0, _root2());
        assertEq(c0, 2);
        assertEq(r1, otherRoot);
        assertEq(c1, 7);
        assertEq(s0, 0);
        assertEq(s1, 0);
    }

    function testFuzz_SubmitRoot_BindsArbitraryRootAndCount(bytes32 root, uint256 count)
        public
    {
        vm.prank(operator);
        uint256 id = obs.submitRoot(root, count);
        (bytes32 gotRoot, uint256 gotCount, uint256 submitted, uint8 status, ) = _batch(id);
        assertEq(gotRoot, root);
        assertEq(gotCount, count);
        assertEq(submitted, block.number);
        assertEq(status, 0);
    }

    // --- challenge ----------------------------------------------------------

    function test_Challenge_ValidProofInsideWindow_FreezesBatch() public {
        (uint256 id, ) = _submit2();
        (bytes32[] memory proof, uint8[] memory sides) = _proofA();

        vm.expectEmit(true, false, false, true);
        emit Challenged(id, anyone, 0);
        vm.prank(anyone); // anyone may challenge
        obs.challenge(id, 0, leafA, fraudLeaf, proof, sides);

        (, , , uint8 status, address challenger) = _batch(id);
        assertEq(status, 1, "challenged");
        assertEq(challenger, anyone, "challenger recorded");
    }

    function test_Challenge_FourLeafTreeProof() public {
        bytes32 l0 = keccak256("l0");
        bytes32 l1 = keccak256("l1");
        bytes32 l2 = keccak256("l2");
        bytes32 l3 = keccak256("l3");
        bytes32 h01 = keccak256(abi.encodePacked(l0, l1));
        bytes32 h23 = keccak256(abi.encodePacked(l2, l3));
        bytes32 root = keccak256(abi.encodePacked(h01, h23));

        vm.prank(operator);
        uint256 id = obs.submitRoot(root, 4);

        bytes32[] memory proof = new bytes32[](2);
        proof[0] = l3; // sibling right of l2
        proof[1] = h01; // sibling left of h23
        uint8[] memory sides = new uint8[](2);
        sides[0] = 0;
        sides[1] = 1;

        vm.prank(anyone);
        obs.challenge(id, 2, l2, fraudLeaf, proof, sides);
        (, , , uint8 status, ) = _batch(id);
        assertEq(status, 1);
    }

    function test_Challenge_PostedEqualsClaimed_RevertsLeafMatches() public {
        (uint256 id, ) = _submit2();
        (bytes32[] memory proof, uint8[] memory sides) = _proofA();
        vm.expectRevert(OptimisticBatchSettlement.LeafMatches.selector);
        obs.challenge(id, 0, leafA, leafA, proof, sides);
    }

    function test_Challenge_BadProof_Reverts() public {
        (uint256 id, ) = _submit2();
        bytes32[] memory proof = new bytes32[](1);
        proof[0] = keccak256("wrong-sibling");
        uint8[] memory sides = new uint8[](1);
        sides[0] = 0;
        vm.expectRevert(OptimisticBatchSettlement.BadProof.selector);
        obs.challenge(id, 0, leafA, fraudLeaf, proof, sides);
    }

    function test_Challenge_WrongSide_RevertsBadProof() public {
        (uint256 id, ) = _submit2();
        bytes32[] memory proof = new bytes32[](1);
        proof[0] = leafB;
        uint8[] memory sides = new uint8[](1);
        sides[0] = 1; // sibling claimed on the left: different root
        vm.expectRevert(OptimisticBatchSettlement.BadProof.selector);
        obs.challenge(id, 0, leafA, fraudLeaf, proof, sides);
    }

    function test_Challenge_AtExactWindowEnd_Succeeds() public {
        (uint256 id, ) = _submit2();
        vm.roll(START_BLOCK + 3000);
        (bytes32[] memory proof, uint8[] memory sides) = _proofA();
        obs.challenge(id, 0, leafA, fraudLeaf, proof, sides);
        (, , , uint8 status, ) = _batch(id);
        assertEq(status, 1, "challenge at +3000 is inside the window");
    }

    function test_Challenge_AfterWindow_RevertsWindowClosed() public {
        (uint256 id, ) = _submit2();
        vm.roll(START_BLOCK + 3001);
        (bytes32[] memory proof, uint8[] memory sides) = _proofA();
        vm.expectRevert(OptimisticBatchSettlement.WindowClosed.selector);
        obs.challenge(id, 0, leafA, fraudLeaf, proof, sides);
    }

    function test_Challenge_UnknownBatch_RevertsWindowClosed() public {
        // A never-submitted batch reads as root 0 / submittedBlock 0, so
        // once block.number passes CHALLENGE_BLOCKS its window has closed.
        vm.roll(START_BLOCK + 3000);
        (bytes32[] memory proof, uint8[] memory sides) = _proofA();
        vm.expectRevert(OptimisticBatchSettlement.WindowClosed.selector);
        obs.challenge(99, 0, leafA, fraudLeaf, proof, sides);
    }

    function test_Challenge_Twice_RevertsNotPending() public {
        (uint256 id, ) = _submit2();
        (bytes32[] memory proof, uint8[] memory sides) = _proofA();
        obs.challenge(id, 0, leafA, fraudLeaf, proof, sides);
        vm.expectRevert(OptimisticBatchSettlement.NotPending.selector);
        obs.challenge(id, 0, leafA, fraudLeaf, proof, sides);
    }

    function testFuzz_ChallengeWindow_Boundary(uint256 delta) public {
        delta = bound(delta, 0, 6000);
        (uint256 id, ) = _submit2();
        vm.roll(START_BLOCK + delta);
        (bytes32[] memory proof, uint8[] memory sides) = _proofA();
        if (delta <= 3000) {
            obs.challenge(id, 0, leafA, fraudLeaf, proof, sides);
            (, , , uint8 status, ) = _batch(id);
            assertEq(status, 1);
        } else {
            vm.expectRevert(OptimisticBatchSettlement.WindowClosed.selector);
            obs.challenge(id, 0, leafA, fraudLeaf, proof, sides);
        }
    }

    // --- finalize -----------------------------------------------------------

    function test_Finalize_BeforeWindow_RevertsWindowOpen() public {
        (uint256 id, ) = _submit2();
        vm.expectRevert(OptimisticBatchSettlement.WindowOpen.selector);
        obs.finalize(id);

        vm.roll(START_BLOCK + 2999);
        vm.expectRevert(OptimisticBatchSettlement.WindowOpen.selector);
        obs.finalize(id);
    }

    function test_Finalize_AtExactWindowEnd_Succeeds() public {
        (uint256 id, bytes32 root) = _submit2();
        vm.roll(START_BLOCK + 3000);
        vm.expectEmit(true, false, false, true);
        emit Finalized(id, root);
        obs.finalize(id); // permissionless
        (, , , uint8 status, ) = _batch(id);
        assertEq(status, 2, "finalized at +3000");
    }

    function test_Finalize_AfterWindow_Succeeds() public {
        (uint256 id, ) = _submit2();
        vm.roll(START_BLOCK + 10_000);
        obs.finalize(id);
        (, , , uint8 status, ) = _batch(id);
        assertEq(status, 2);
    }

    function test_DoubleFinalize_RevertsNotPending() public {
        (uint256 id, ) = _submit2();
        vm.roll(START_BLOCK + 3001);
        obs.finalize(id);
        vm.expectRevert(OptimisticBatchSettlement.NotPending.selector);
        obs.finalize(id);
    }

    function test_ChallengeAfterFinalize_RevertsNotPending() public {
        (uint256 id, ) = _submit2();
        vm.roll(START_BLOCK + 3001);
        obs.finalize(id);
        (bytes32[] memory proof, uint8[] memory sides) = _proofA();
        vm.expectRevert(OptimisticBatchSettlement.NotPending.selector);
        obs.challenge(id, 0, leafA, fraudLeaf, proof, sides);
    }

    function test_FinalizeChallengedBatch_RevertsNotPending() public {
        (uint256 id, ) = _submit2();
        (bytes32[] memory proof, uint8[] memory sides) = _proofA();
        obs.challenge(id, 0, leafA, fraudLeaf, proof, sides);
        vm.roll(START_BLOCK + 10_000);
        vm.expectRevert(OptimisticBatchSettlement.NotPending.selector);
        obs.finalize(id); // a challenged batch can never finalize
    }

    function testFuzz_FinalizeWindow_Boundary(uint256 delta) public {
        delta = bound(delta, 0, 6000);
        (uint256 id, ) = _submit2();
        vm.roll(START_BLOCK + delta);
        if (delta < 3000) {
            vm.expectRevert(OptimisticBatchSettlement.WindowOpen.selector);
            obs.finalize(id);
        } else {
            obs.finalize(id);
            (, , , uint8 status, ) = _batch(id);
            assertEq(status, 2);
        }
    }

    function testFuzz_RaceAtBoundary_FirstTransitionWins(bool challengeFirst) public {
        (uint256 id, ) = _submit2();
        vm.roll(START_BLOCK + 3000); // both transitions legal in this block
        (bytes32[] memory proof, uint8[] memory sides) = _proofA();
        if (challengeFirst) {
            obs.challenge(id, 0, leafA, fraudLeaf, proof, sides);
            vm.expectRevert(OptimisticBatchSettlement.NotPending.selector);
            obs.finalize(id);
        } else {
            obs.finalize(id);
            vm.expectRevert(OptimisticBatchSettlement.NotPending.selector);
            obs.challenge(id, 0, leafA, fraudLeaf, proof, sides);
        }
    }
}
