// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {SincorSettlementHub} from "../contracts/modules/SincorSettlementHub.sol";
import {SincorTaskEscrow} from "../contracts/escrow/SincorTaskEscrow.sol";
import {MockUSDC} from "./mocks/MockUSDC.sol";

/// @notice FULL-LOOP INTEGRATION: SincorTaskEscrow -> off-chain SIGIL -> SincorSettlementHub.
///
/// Stage mapping (honest, driven by the escrow contract's real semantics):
/// `SincorTaskEscrow.completeTask` pays the task's ASSIGNED AGENT, so the
/// composed A2A chain exercised here is an agent-to-agent chain:
///
///   1. ESCROW FUNDED — a sponsor creates a task on SincorTaskEscrow with a
///      10_000 MockUSDC reward (bond 0, a mode the escrow suite itself covers);
///      createTask pulls the reward in. Asserted: sponsor -10_000, escrow
///      holds exactly 10_000, task status Unassigned.
///   2. CLAIM — the buyer (an agent) claims the task. Asserted: status Claimed,
///      assignedAgent == buyer.
///   3. WORK COMPLETED OFF-CHAIN — the SIGIL commitment for the downstream hub
///      task is computed in-test exactly per the Hub-path canon:
///      keccak256(taskId || canonical-JSON output bytes), where the JSON is
///      the compact, sorted-key serialization of the delivered output.
///   4. ESCROW RELEASED — the sponsor (task creator) calls the escrow's own
///      completion path, completeTask. Asserted: escrow balance 0, buyer
///      +10_000 (buyer started at 0 — the hub settlement funds ARE the escrow
///      release; nothing is minted mid-test), task status Verified (final).
///   5. BUYER APPROVES HUB — buyer approves SincorSettlementHub on MockUSDC
///      for exactly the seal amount. Asserted: allowance == 10_000.
///   6. RELAYER SETTLES — a third-party relayer (NOT the buyer, NOT the
///      sponsor) submits the buyer's signed SEAL to verifyAndSettleSeal.
///      Asserted: provider +9_500 (exactly 95%), treasury +500 (exactly 5%),
///      buyer -10_000 (back to 0), hub nonce 1, isTaskVerified(hubTaskId,
///      sigil) == true, and the escrow task remains in its final Verified
///      state with a zero escrow balance.
///
/// No stage is theatrical: every handoff is a real contract call and every
/// balance/state assertion ties the hub leg's funds to the escrow leg's
/// release (same token, same 10_000 amount, buyer balance 0 -> 10_000 -> 0).
contract SincorSettlementHubIntegrationTest is Test {
    MockUSDC public usdc;
    SincorTaskEscrow public escrow;
    SincorSettlementHub public hub;

    address public sponsor = makeAddr("sponsor");
    address public provider = makeAddr("provider");
    address public relayer = makeAddr("relayer");
    address public treasury = makeAddr("hub-treasury");
    address public escrowTreasury = makeAddr("escrow-treasury");

    uint256 internal buyerKey = 0xB17E2;
    address public buyer;

    uint256 internal constant REWARD = 10_000; // == hub seal amount, by construction

    function setUp() public {
        buyer = vm.addr(buyerKey);
        usdc = new MockUSDC();
        escrow = new SincorTaskEscrow(address(usdc), escrowTreasury);
        hub = new SincorSettlementHub(address(usdc), treasury);
        usdc.mint(sponsor, REWARD);
        // Deliberately NO mint to the buyer: its settlement funds must come
        // from the escrow release (stage 4), and stage assertions prove it.
    }

    function _status(uint256 id) internal view returns (SincorTaskEscrow.TaskStatus st) {
        (,,,,, st) = escrow.tasks(id);
    }

    function test_FullLoop_EscrowReleaseFundsHubSettlement() public {
        // ---- Stage 1: escrow funded -------------------------------------
        vm.prank(sponsor);
        usdc.approve(address(escrow), REWARD);
        vm.prank(sponsor);
        uint256 escrowTaskId = escrow.createTask(REWARD, 0, 1 days);

        assertEq(usdc.balanceOf(sponsor), 0);
        assertEq(usdc.balanceOf(address(escrow)), REWARD);
        assertEq(uint256(_status(escrowTaskId)), uint256(SincorTaskEscrow.TaskStatus.Unassigned));

        // ---- Stage 2: buyer claims --------------------------------------
        vm.prank(buyer);
        escrow.claimTask(escrowTaskId);
        (address _creator, address assigned, uint256 _reward, uint256 _bond, uint256 _expiry, ) =
            escrow.tasks(escrowTaskId);
        // (trailing slot is the task status, asserted via _status below)
        assertEq(assigned, buyer);
        assertEq(uint256(_status(escrowTaskId)), uint256(SincorTaskEscrow.TaskStatus.Claimed));
        assertEq(usdc.balanceOf(buyer), 0);

        // ---- Stage 3: work completed off-chain, SIGIL computed ----------
        // The hub task is the downstream settlement for this escrow task;
        // its bytes32 id is the escrow task id widened to bytes32.
        bytes32 hubTaskId = bytes32(escrowTaskId);
        // Canonical JSON of the delivered output: sorted keys, compact
        // separators (the sincor-x402 canonical_json form).
        bytes memory outputJson = bytes('{"result":"ok","task":0}');
        bytes32 sigil = keccak256(abi.encodePacked(hubTaskId, outputJson));
        bytes32 schemaHash = keccak256(bytes('{"type":"object","required":["result"]}'));
        assertFalse(hub.isTaskVerified(hubTaskId, sigil));

        // ---- Stage 4: escrow released via its own completion path -------
        vm.prank(sponsor);
        escrow.completeTask(escrowTaskId);

        assertEq(usdc.balanceOf(address(escrow)), 0);
        assertEq(usdc.balanceOf(buyer), REWARD); // release == settlement funds
        assertEq(uint256(_status(escrowTaskId)), uint256(SincorTaskEscrow.TaskStatus.Verified));

        // ---- Stage 5: buyer approves the Hub -----------------------------
        vm.prank(buyer);
        usdc.approve(address(hub), REWARD);
        assertEq(usdc.allowance(buyer, address(hub)), REWARD);

        // ---- Stage 6: relayer settles the buyer's SEAL on the Hub --------
        SincorSettlementHub.SealAuthorization memory seal = SincorSettlementHub.SealAuthorization({
            taskId: hubTaskId,
            buyer: buyer,
            provider: provider,
            amount: REWARD,
            expectedSchemaHash: schemaHash,
            deadline: block.timestamp + 1 hours,
            nonce: hub.nonces(buyer)
        });
        bytes32 structHash = keccak256(
            abi.encode(
                hub.SEAL_TYPEHASH(),
                seal.taskId,
                seal.buyer,
                seal.provider,
                seal.amount,
                seal.expectedSchemaHash,
                seal.deadline,
                seal.nonce
            )
        );
        bytes32 digest = keccak256(abi.encodePacked("\x19\x01", hub.DOMAIN_SEPARATOR(), structHash));
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(buyerKey, digest);

        vm.prank(relayer); // third party — not the buyer, not the sponsor
        bool ok = hub.verifyAndSettleSeal(seal, sigil, v, r, s);

        assertTrue(ok);
        assertEq(usdc.balanceOf(provider), 9_500); // exactly 95%
        assertEq(usdc.balanceOf(treasury), 500); // exactly 5%
        assertEq(usdc.balanceOf(buyer), 0); // release fully settled, nothing invented
        assertEq(hub.nonces(buyer), 1);
        assertTrue(hub.isTaskVerified(hubTaskId, sigil));

        // ---- Final state: escrow task final, escrow empty -----------------
        assertEq(uint256(_status(escrowTaskId)), uint256(SincorTaskEscrow.TaskStatus.Verified));
        assertEq(usdc.balanceOf(address(escrow)), 0);
        assertEq(usdc.balanceOf(escrowTreasury), 0); // no slash path was taken
    }
}
