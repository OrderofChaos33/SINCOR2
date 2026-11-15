// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {CommitRevealAuction} from "../contracts/CommitRevealAuction.sol";
import {ScopedPausable} from "../contracts/security/ScopedPausable.sol";
import {OptimisticBatchSettlement} from "../contracts/OptimisticBatchSettlement.sol";

/// @title Sunset / expiry boundary tests (A+ overhaul, 2026-11-14)
/// @notice Boundary fuzz + unit coverage for the two on-chain time gates
///         that exist in Solidity on this tree:
///           1. ScopedPausable's immutable sunset (inherited by
///              CommitRevealAuction and ExecutionEscrowManager):
///              SUNSET_DURATION_BLOCKS = 7_776_000 on THIS tree
///              (deploy block + duration; 180 days at 2s Base blocks).
///           2. OptimisticBatchSettlement's challenge window:
///              CHALLENGE_BLOCKS = 300 on THIS tree.
///         The stacked-PR (Denim) branches carry different constants
///         (77_760_000 / 3000); those branches are not this tree, so the
///         assertions below read the constants from the contracts and
///         pin the values this tree actually ships.
/// @dev    SEAL EIP-712 envelope expiry is enforced in the Python layer
///         (src/sincor2) — there is no Solidity SEAL envelope contract in
///         this repo — so its boundary tests live in tests/pytest, not here.
contract SunsetExpiryBoundaryTest is Test {
    address internal guardian = address(0x6A12D);
    address internal poster = address(0x9057);
    address internal bidder = address(0xB1D1);
    address internal stranger = address(0x57AA);

    uint256 internal constant DEPLOY_BLOCK = 1000;

    CommitRevealAuction internal auction;
    OptimisticBatchSettlement internal batch;
    address internal treasury = address(0x7EA5);
    address internal operator = address(0x09E1);

    function setUp() public {
        vm.warp(1_700_000_000);
        vm.roll(DEPLOY_BLOCK);
        auction = new CommitRevealAuction(guardian);
        batch = new OptimisticBatchSettlement(treasury, operator);
    }

    // --- ScopedPausable sunset ---------------------------------------------

    function test_SunsetConstantsOnThisTree() public view {
        assertEq(auction.SUNSET_DURATION_BLOCKS(), 7_776_000);
        assertEq(auction.SUNSET_BLOCK(), DEPLOY_BLOCK + 7_776_000);
        assertEq(auction.guardianMultisig(), guardian);
    }

    function test_SetPauseOnlyGuardian() public {
        vm.prank(stranger);
        vm.expectRevert(ScopedPausable.UnauthorizedGuardian.selector);
        auction.setPause(true);
    }

    function test_SetPauseAtSunsetMinusOneSucceeds() public {
        vm.roll(auction.SUNSET_BLOCK() - 1);
        vm.prank(guardian);
        auction.setPause(true);
        assertTrue(auction.paused());
    }

    function test_SetPauseAtExactSunsetReverts() public {
        vm.roll(auction.SUNSET_BLOCK());
        vm.prank(guardian);
        vm.expectRevert(ScopedPausable.PauseExpired.selector);
        auction.setPause(true);
    }

    function test_SetPauseAfterSunsetReverts() public {
        vm.roll(auction.SUNSET_BLOCK() + 1);
        vm.prank(guardian);
        vm.expectRevert(ScopedPausable.PauseExpired.selector);
        auction.setPause(false);
    }

    function test_PausedBlocksNewActivityBeforeSunset() public {
        bytes32 id = bytes32(uint256(1));
        vm.prank(guardian);
        auction.setPause(true);
        vm.prank(poster);
        vm.expectRevert(ScopedPausable.EmergencyPaused.selector);
        auction.openAuction(id, 0, 0);
    }

    function test_PauseStopsEnforcingAtSunset() public {
        bytes32 id = bytes32(uint256(2));
        vm.roll(auction.SUNSET_BLOCK() - 10);
        vm.prank(guardian);
        auction.setPause(true); // paused flag is now stale-true forever
        vm.roll(auction.SUNSET_BLOCK()); // sunset: protocol unpausable by construction
        vm.prank(poster);
        auction.openAuction(id, 0, 0); // must NOT revert despite paused == true
        assertTrue(auction.paused()); // flag unchanged; enforcement is what expires
    }

    function testFuzz_SetPauseSunsetBoundary(uint256 target) public {
        uint256 sunset = auction.SUNSET_BLOCK();
        target = bound(target, sunset - 1000, sunset + 1000);
        vm.roll(target);
        vm.prank(guardian);
        if (target < sunset) {
            auction.setPause(true);
            assertTrue(auction.paused());
        } else {
            vm.expectRevert(ScopedPausable.PauseExpired.selector);
            auction.setPause(true);
        }
    }

    function testFuzz_PauseEnforcementBoundary(uint256 target) public {
        bytes32 id = bytes32(uint256(3));
        uint256 sunset = auction.SUNSET_BLOCK();
        vm.prank(guardian);
        auction.setPause(true);
        target = bound(target, sunset - 1000, sunset + 1000);
        vm.roll(target);
        vm.prank(poster);
        if (target < sunset) {
            vm.expectRevert(ScopedPausable.EmergencyPaused.selector);
            auction.openAuction(id, 0, 0);
        } else {
            auction.openAuction(id, 0, 0);
        }
    }

    // --- OptimisticBatchSettlement challenge window --------------------------

    function _submit(bytes32 root) internal returns (uint256 batchId, uint256 submittedBlock) {
        vm.prank(operator);
        batchId = batch.submitRoot(root, 1);
        submittedBlock = block.number;
    }

    function test_ChallengeBlocksConstantOnThisTree() public view {
        assertEq(batch.CHALLENGE_BLOCKS(), 300);
    }

    function test_SubmitRootOnlyOperator() public {
        vm.prank(stranger);
        vm.expectRevert(OptimisticBatchSettlement.NotOperator.selector);
        batch.submitRoot(bytes32(uint256(9)), 1);
    }

    function test_ChallengeAtExactWindowEndSucceeds() public {
        bytes32 leaf = bytes32(uint256(0x1EAF));
        (uint256 id, uint256 submitted) = _submit(leaf); // single-leaf tree: root == leaf
        vm.roll(submitted + 300); // guard reverts only when block > submitted + 300
        batch.challenge(id, 0, leaf, bytes32(uint256(0xBAD)), new bytes32[](0), new uint8[](0));
        (, , , uint8 status, address challenger) = batch.batches(id);
        assertEq(status, 1);
        assertEq(challenger, address(this));
    }

    function test_ChallengeAfterWindowReverts() public {
        bytes32 leaf = bytes32(uint256(0x1EAF));
        (uint256 id, uint256 submitted) = _submit(leaf);
        vm.roll(submitted + 301);
        vm.expectRevert(OptimisticBatchSettlement.WindowClosed.selector);
        batch.challenge(id, 0, leaf, bytes32(uint256(0xBAD)), new bytes32[](0), new uint8[](0));
    }

    function test_FinalizeBeforeWindowEndReverts() public {
        (uint256 id, uint256 submitted) = _submit(bytes32(uint256(0x1EAF)));
        vm.roll(submitted + 299); // finalize requires block >= submitted + 300
        vm.expectRevert(OptimisticBatchSettlement.WindowOpen.selector);
        batch.finalize(id);
    }

    function test_FinalizeAtExactWindowEndSucceeds() public {
        (uint256 id, uint256 submitted) = _submit(bytes32(uint256(0x1EAF)));
        vm.roll(submitted + 300);
        batch.finalize(id);
        (, , , uint8 status, ) = batch.batches(id);
        assertEq(status, 2);
    }

    function test_ChallengeLeafMatchesReverts() public {
        bytes32 leaf = bytes32(uint256(0x1EAF));
        (uint256 id, ) = _submit(leaf);
        vm.expectRevert(OptimisticBatchSettlement.LeafMatches.selector);
        batch.challenge(id, 0, leaf, leaf, new bytes32[](0), new uint8[](0));
    }

    function test_ChallengeBadProofReverts() public {
        (uint256 id, ) = _submit(bytes32(uint256(0xB007)));
        vm.expectRevert(OptimisticBatchSettlement.BadProof.selector);
        batch.challenge(id, 0, bytes32(uint256(0x1EAF)), bytes32(uint256(0xBAD)), new bytes32[](0), new uint8[](0));
    }

    function test_ChallengeAfterFinalizeRevertsNotPending() public {
        bytes32 leaf = bytes32(uint256(0x1EAF));
        (uint256 id, uint256 submitted) = _submit(leaf);
        vm.roll(submitted + 300);
        batch.finalize(id);
        vm.expectRevert(OptimisticBatchSettlement.NotPending.selector);
        batch.challenge(id, 0, leaf, bytes32(uint256(0xBAD)), new bytes32[](0), new uint8[](0));
    }

    function testFuzz_ChallengeWindowBoundary(uint256 offset) public {
        offset = bound(offset, 0, 600);
        bytes32 leaf = bytes32(uint256(0x1EAF));
        (uint256 id, uint256 submitted) = _submit(leaf);
        vm.roll(submitted + offset);
        if (offset <= 300) {
            batch.challenge(id, 0, leaf, bytes32(uint256(0xBAD)), new bytes32[](0), new uint8[](0));
        } else {
            vm.expectRevert(OptimisticBatchSettlement.WindowClosed.selector);
            batch.challenge(id, 0, leaf, bytes32(uint256(0xBAD)), new bytes32[](0), new uint8[](0));
        }
    }

    function testFuzz_FinalizeWindowBoundary(uint256 offset) public {
        offset = bound(offset, 0, 600);
        (uint256 id, uint256 submitted) = _submit(bytes32(uint256(0x1EAF)));
        vm.roll(submitted + offset);
        if (offset < 300) {
            vm.expectRevert(OptimisticBatchSettlement.WindowOpen.selector);
            batch.finalize(id);
        } else {
            batch.finalize(id);
        }
    }
}
