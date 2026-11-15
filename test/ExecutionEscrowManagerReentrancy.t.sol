// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {ExecutionEscrowManager} from "../contracts/ExecutionEscrowManager.sol";
import {IExecutionEscrowManager} from "../contracts/IExecutionEscrowManager.sol";
import {ReentrancyGuard} from "../contracts/security/ScopedPausable.sol";

/// @dev Worker contract whose receive() attempts to reenter a dispute
///      resolution path while its own resolution payout is in flight.
contract ReentrantWorker {
    ExecutionEscrowManager public mgr;
    bytes32 public auctionId;
    bool public reentryAttempted;
    bool public reentrySucceeded;
    bytes4 public reentryErrorSelector;

    function arm(ExecutionEscrowManager _mgr, bytes32 _auctionId) external {
        mgr = _mgr;
        auctionId = _auctionId;
    }

    function depositStake(bytes32 id, uint256 amount) external {
        mgr.depositStake{value: amount}(id);
    }

    function submitResult(bytes32 id, bytes32 resultHash) external {
        mgr.submitResult(id, resultHash);
    }

    receive() external payable {
        if (!reentryAttempted && address(mgr) != address(0)) {
            reentryAttempted = true;
            // Cross-function reentry: while resolveQualityDispute is paying
            // this worker out, try to run a second resolution path against
            // the same escrow.
            try mgr.rejectUnsubstantiatedDispute(auctionId) {
                reentrySucceeded = true;
            } catch (bytes memory err) {
                if (err.length >= 4) {
                    reentryErrorSelector = bytes4(err);
                }
            }
        }
    }
}

/**
 * @notice S-01 defense-in-depth (Perimeter Freeze 2026-11-07, Slither
 *         waiver S-01): ExecutionEscrowManager finalizes all dispute state
 *         BEFORE any payout, so reentry already fails on state checks.
 *         These tests pin the belt-and-braces property on top of that:
 *         a reentrant call into a dispute-resolution path during a
 *         resolution payout must revert with the ReentrancyGuard error,
 *         and the outer resolution must still complete and pay exactly
 *         once.
 */
contract ExecutionEscrowManagerReentrancyTest is Test {
    bytes32 internal constant AUCTION_ID = keccak256("reentrancy-auction");
    uint256 internal constant BID = 1 ether;
    uint256 internal constant STAKE = 0.2 ether; // 2000 bps of BID
    uint256 internal constant BOND = 0.02 ether;

    ExecutionEscrowManager internal mgr;
    ReentrantWorker internal worker;

    address internal auctionCore = makeAddr("auctionCore");
    address internal adjudicator = makeAddr("adjudicator");
    address internal guardian = makeAddr("guardian");
    address internal poster = makeAddr("poster");
    address internal challenger = makeAddr("challenger");

    function setUp() public {
        mgr = new ExecutionEscrowManager(auctionCore, adjudicator, 2000, BOND, guardian);
        worker = new ReentrantWorker();
        worker.arm(mgr, AUCTION_ID);

        vm.deal(auctionCore, 10 ether);
        vm.deal(address(worker), 1 ether);
        vm.deal(challenger, 1 ether);

        vm.prank(auctionCore);
        mgr.initializeEscrow{value: BID}(
            AUCTION_ID,
            poster,
            address(worker),
            uint96(BID),
            0,
            1 days,
            1 days,
            2 days,
            1 days
        );
        worker.depositStake(AUCTION_ID, STAKE);
        worker.submitResult(AUCTION_ID, keccak256("result"));

        // Evidence-free dispute from a third-party challenger.
        vm.prank(challenger);
        mgr.openQualityDispute{value: BOND}(AUCTION_ID, bytes32(0));

        // Past the 6h evidence window, so rejectUnsubstantiatedDispute is
        // in principle callable -- the reentry target is live, not stale.
        vm.warp(block.timestamp + 7 hours);
    }

    function test_reentryDuringRejectResolution_blockedByGuard() public {
        uint256 balBefore = address(worker).balance;

        vm.prank(adjudicator);
        mgr.resolveQualityDispute(AUCTION_ID, false); // reject: worker paid in full

        // The payout reached the worker and triggered the reentry attempt.
        assertTrue(worker.reentryAttempted(), "reentry was attempted");
        assertFalse(worker.reentrySucceeded(), "reentry must not succeed");
        // The block must come from the guard itself (S-01), not merely from
        // the already-finalized state checks.
        assertEq(
            worker.reentryErrorSelector(),
            ReentrancyGuard.ReentrancyGuardReentrantCall.selector,
            "reentry must revert with ReentrancyGuardReentrantCall"
        );
        // Outer resolution completed and paid exactly once (bid + stake).
        assertEq(address(worker).balance, balBefore + BID + STAKE, "paid exactly once");
        IExecutionEscrowManager.Escrow memory esc = mgr.getEscrow(AUCTION_ID);
        assertEq(
            uint8(esc.state),
            uint8(IExecutionEscrowManager.ExecutionState.Finalized),
            "escrow finalized"
        );
    }

    function test_reentryDuringSlashResolution_blockedByGuard() public {
        // Slash path: poster is an EOA here, challenger an EOA; the worker
        // still receives its unslashed stake half, which fires receive().
        uint256 balBefore = address(worker).balance;

        vm.prank(adjudicator);
        mgr.resolveQualityDispute(AUCTION_ID, true); // slash

        assertTrue(worker.reentryAttempted(), "reentry was attempted");
        assertFalse(worker.reentrySucceeded(), "reentry must not succeed");
        assertEq(
            worker.reentryErrorSelector(),
            ReentrancyGuard.ReentrancyGuardReentrantCall.selector,
            "reentry must revert with ReentrancyGuardReentrantCall"
        );
        // Worker keeps exactly the unslashed half of its stake.
        assertEq(address(worker).balance, balBefore + STAKE / 2, "stake half paid exactly once");
        IExecutionEscrowManager.Escrow memory esc = mgr.getEscrow(AUCTION_ID);
        assertEq(
            uint8(esc.state),
            uint8(IExecutionEscrowManager.ExecutionState.Finalized),
            "escrow finalized"
        );
    }
}
