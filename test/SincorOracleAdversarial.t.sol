// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {SincorValidationReputationOracle, SincorIdentityRegistryStub} from "../contracts/erc8004/SincorValidationReputationOracle.sol";

// ---------- adversarial helper contracts ----------

/// @dev Verifier that reports EVERYTHING verified.
contract AlwaysTrueVerifier {
    function isTaskVerified(bytes32, bytes32) external pure returns (bool) { return true; }
}

/// @dev Verifier whose check always reverts.
contract RevertingVerifier {
    function isTaskVerified(bytes32, bytes32) external pure returns (bool) { revert("verifier down"); }
}

/// @dev Verifier that tries to reenter the oracle as the authorized writer
/// (it IS settlementSource) while the oracle is mid-verification.
/// Returns false if the reentrant write landed, true if it was blocked,
/// so a successful outer call + count==1 proves reentrancy failed.
contract ReentrantVerifier {
    SincorValidationReputationOracle public oracle;
    address public victim;
    function setTargets(SincorValidationReputationOracle _o, address _v) external { oracle = _o; victim = _v; }
    function isTaskVerified(bytes32 taskId, bytes32 sigil) external returns (bool) {
        try oracle.recordValidation(victim, taskId, sigil, SincorValidationReputationOracle.Outcome.Success) {
            return false; // reentry landed
        } catch {
            return true; // reentry blocked
        }
    }
}

/// @dev Dumb forwarder: tries to launder a stranger's call through a contract.
contract ForwardingAttacker {
    function write(SincorValidationReputationOracle o, address a, bytes32 t, bytes32 s) external {
        o.recordValidation(a, t, s, SincorValidationReputationOracle.Outcome.Success);
    }
    function writeVerified(SincorValidationReputationOracle o, address a, bytes32 t, bytes32 s) external {
        o.recordVerifiedSettlement(a, t, s, SincorValidationReputationOracle.Outcome.Success);
    }
    function rotate(SincorValidationReputationOracle o, address src) external { o.setSettlementSource(src); }
    function stealOwnership(SincorValidationReputationOracle o, address n) external { o.transferOwnership(n); }
}

contract SincorOracleAdversarialTest is Test {
    SincorValidationReputationOracle oracle; // settlementSource = honest-ish mock below
    AlwaysTrueVerifier hub; // doubles as source in setUp: verifies nothing in setUp use
    SincorIdentityRegistryStub identity;
    address agent = address(0xA6E7);
    address agent2 = address(0xB0B);
    address stranger = address(0xBAD);
    bytes32 task1 = keccak256("adv-task-1");
    bytes32 sigil1 = keccak256("adv-sigil-1");

    function setUp() public {
        hub = new AlwaysTrueVerifier();
        // NOTE: source is AlwaysTrue on purpose — even with a verify-everything
        // hub configured, unauthorized CALLERS must still be blocked.
        oracle = new SincorValidationReputationOracle(address(hub));
        identity = new SincorIdentityRegistryStub();
    }

    // ---------- unauthorized writes ----------

    function test_StrangerBlockedEvenWhenHubVerifiesEverything() public {
        vm.prank(stranger);
        vm.expectRevert(SincorValidationReputationOracle.NotAuthorized.selector);
        oracle.recordVerifiedSettlement(agent, task1, sigil1, SincorValidationReputationOracle.Outcome.Success);
        vm.prank(stranger);
        vm.expectRevert(SincorValidationReputationOracle.NotAuthorized.selector);
        oracle.recordValidation(agent, task1, sigil1, SincorValidationReputationOracle.Outcome.Success);
        assertEq(oracle.attestationCount(), 0);
    }

    function test_IntermediaryContractDoesNotLaunderAuthorization() public {
        ForwardingAttacker fwd = new ForwardingAttacker();
        vm.expectRevert(SincorValidationReputationOracle.NotAuthorized.selector);
        fwd.write(oracle, agent, task1, sigil1);
        vm.expectRevert(SincorValidationReputationOracle.NotAuthorized.selector);
        fwd.writeVerified(oracle, agent, task1, sigil1);
        assertEq(oracle.attestationCount(), 0);
    }

    function test_StrangerCannotBecomeWriterByRotationOrOwnershipTheft() public {
        ForwardingAttacker fwd = new ForwardingAttacker();
        vm.prank(stranger);
        vm.expectRevert(SincorValidationReputationOracle.NotOwner.selector);
        oracle.setSettlementSource(stranger);
        vm.prank(stranger);
        vm.expectRevert(SincorValidationReputationOracle.NotOwner.selector);
        oracle.transferOwnership(stranger);
        vm.expectRevert(SincorValidationReputationOracle.NotOwner.selector);
        fwd.rotate(oracle, stranger);
        vm.expectRevert(SincorValidationReputationOracle.NotOwner.selector);
        fwd.stealOwnership(oracle, stranger);
        assertEq(oracle.settlementSource(), address(hub));
        assertEq(oracle.owner(), address(this));
    }

    // ---------- replay matrix ----------

    function test_ReplayCrossPath_ReverseDirection_Blocked() public {
        vm.prank(address(hub));
        oracle.recordVerifiedSettlement(agent, task1, sigil1, SincorValidationReputationOracle.Outcome.Success);
        vm.expectRevert(SincorValidationReputationOracle.ReplayBlocked.selector);
        oracle.recordValidation(agent, task1, sigil1, SincorValidationReputationOracle.Outcome.Success);
        assertEq(oracle.attestationCount(), 1);
    }

    function test_ReplayDifferentAgentSamePair_Blocked_GlobalKey() public {
        oracle.recordValidation(agent, task1, sigil1, SincorValidationReputationOracle.Outcome.Success);
        vm.expectRevert(SincorValidationReputationOracle.ReplayBlocked.selector);
        oracle.recordValidation(agent2, task1, sigil1, SincorValidationReputationOracle.Outcome.Success);
        (int256 s2, uint256 succ2,,) = oracle.reputationOf(agent2);
        assertEq(s2, 0); assertEq(succ2, 0);
        assertEq(oracle.attestationCount(), 1);
    }

    function test_ReplayCannotFlipOutcome() public {
        oracle.recordValidation(agent, task1, sigil1, SincorValidationReputationOracle.Outcome.Success);
        vm.expectRevert(SincorValidationReputationOracle.ReplayBlocked.selector);
        oracle.recordValidation(agent, task1, sigil1, SincorValidationReputationOracle.Outcome.Failure);
        (int256 score,, uint256 fail,) = oracle.reputationOf(agent);
        assertEq(score, 10); assertEq(fail, 0);
    }

    function test_ReplayKeyBindsPairNotSigilAlone_SameSigilManyTasks() public {
        for (uint256 i = 0; i < 5; i++) {
            oracle.recordValidation(agent, keccak256(abi.encode("reuse-task", i)), sigil1, SincorValidationReputationOracle.Outcome.Success);
        }
        assertEq(oracle.attestationCount(), 5);
        (int256 sc, uint256 su,,) = oracle.reputationOf(agent);
        assertEq(sc, 50); assertEq(su, 5);
    }

    // ---------- reputation inflation / griefing (writer-trust) ----------

    function test_WriterPathFabricatedSigilsInflateToCap_ByDesign() public {
        for (uint256 i = 0; i < 100; i++) {
            oracle.recordValidation(agent, keccak256(abi.encode("fake-t", i)), keccak256(abi.encode("fake-s", i)), SincorValidationReputationOracle.Outcome.Success);
        }
        (int256 score, uint256 succ,,) = oracle.reputationOf(agent);
        assertEq(score, 1000); assertEq(succ, 100);
    }

    function test_SourceKeyCanTankAnyVictimToFloor_ByDesign() public {
        vm.startPrank(address(hub));
        for (uint256 i = 0; i < 10; i++) {
            oracle.recordValidation(agent, keccak256(abi.encode("grief-t", i)), keccak256(abi.encode("grief-s", i)), SincorValidationReputationOracle.Outcome.Failure);
        }
        vm.stopPrank();
        (int256 score,, uint256 fail,) = oracle.reputationOf(agent);
        assertEq(score, -100); assertEq(fail, 10);
    }

    function test_ScoreBoundedButCountsUnbounded() public {
        for (uint256 i = 0; i < 150; i++) {
            oracle.recordValidation(agent, keccak256(abi.encode("mass-t", i)), keccak256(abi.encode("mass-s", i)), SincorValidationReputationOracle.Outcome.Success);
        }
        (int256 score, uint256 succ,, uint256 total) = oracle.reputationOf(agent);
        assertEq(score, 1000); assertEq(succ, 150); assertEq(total, 150);
        for (uint256 i = 0; i < 30; i++) {
            oracle.recordValidation(agent2, keccak256(abi.encode("massf-t", i)), keccak256(abi.encode("massf-s", i)), SincorValidationReputationOracle.Outcome.Failure);
        }
        (int256 score2,, uint256 fail2,) = oracle.reputationOf(agent2);
        assertEq(score2, -100); assertEq(fail2, 30);
    }

    function test_ScoreArithmeticAtBounds() public {
        for (uint256 i = 0; i < 100; i++) {
            oracle.recordValidation(agent, keccak256(abi.encode("cap-t", i)), keccak256(abi.encode("cap-s", i)), SincorValidationReputationOracle.Outcome.Success);
        }
        oracle.recordValidation(agent, keccak256("cap-extra-t"), keccak256("cap-extra-s"), SincorValidationReputationOracle.Outcome.Failure);
        (int256 score,,,) = oracle.reputationOf(agent);
        assertEq(score, 990); // one failure steps off the cap normally
    }

    function test_SourceBypassesOwnVerificationViaWriterPath() public {
        // hub (AlwaysTrue here) as caller uses the UNVERIFIED path for a pair
        // no settlement ever produced — writer path performs no hub check.
        vm.prank(address(hub));
        oracle.recordValidation(agent, keccak256("never-settled"), keccak256("never-signed"), SincorValidationReputationOracle.Outcome.Success);
        assertEq(oracle.attestationCount(), 1);
    }

    // ---------- verified path / settlementSource trust ----------

    function test_OwnerSelfDeal_RotateToAlwaysTrue_ThenVerifiedInflation() public {
        AlwaysTrueVerifier evil = new AlwaysTrueVerifier();
        oracle.setSettlementSource(address(evil)); // owner, single step, no timelock
        oracle.recordVerifiedSettlement(agent, keccak256("phantom-task"), keccak256("phantom-sigil"), SincorValidationReputationOracle.Outcome.Success);
        (int256 score, uint256 succ,,) = oracle.reputationOf(agent);
        assertEq(score, 10); assertEq(succ, 1); // "verified" attestation for a settlement that never existed
    }

    function test_VerifiedPathBlockedWhenSourceIsZero() public {
        SincorValidationReputationOracle o2 = new SincorValidationReputationOracle(address(0));
        vm.expectRevert(SincorValidationReputationOracle.SettlementNotVerified.selector);
        o2.recordVerifiedSettlement(agent, task1, sigil1, SincorValidationReputationOracle.Outcome.Success);
        o2.recordValidation(agent, task1, sigil1, SincorValidationReputationOracle.Outcome.Success); // writer path still open to owner
        assertEq(o2.attestationCount(), 1);
    }

    function test_OwnerCanZeroSource_KillsVerifiedPathAndSourceWriter() public {
        oracle.setSettlementSource(address(0));
        vm.expectRevert(SincorValidationReputationOracle.SettlementNotVerified.selector);
        oracle.recordVerifiedSettlement(agent, task1, sigil1, SincorValidationReputationOracle.Outcome.Success);
        vm.prank(address(hub)); // former source no longer a writer
        vm.expectRevert(SincorValidationReputationOracle.NotAuthorized.selector);
        oracle.recordValidation(agent, task1, sigil1, SincorValidationReputationOracle.Outcome.Success);
    }

    function test_VerifiedPathWithEOASource_RevertsButEOAKeepsFullWriterAccess() public {
        address eoa = address(0xE0A);
        oracle.setSettlementSource(eoa);
        // EOA cannot answer isTaskVerified: ABI decode of empty returndata reverts.
        vm.expectRevert();
        oracle.recordVerifiedSettlement(agent, task1, sigil1, SincorValidationReputationOracle.Outcome.Success);
        // ...yet the same EOA key has UNVERIFIED write access via recordValidation.
        vm.prank(eoa);
        oracle.recordValidation(agent, task1, sigil1, SincorValidationReputationOracle.Outcome.Success);
        assertEq(oracle.attestationCount(), 1);
    }

    function test_VerifiedPathWithNonVerifierContractSource_Reverts() public {
        oracle.setSettlementSource(address(identity)); // a contract with no isTaskVerified
        vm.expectRevert();
        oracle.recordVerifiedSettlement(agent, task1, sigil1, SincorValidationReputationOracle.Outcome.Success);
        assertEq(oracle.attestationCount(), 0);
    }

    function test_VerifiedPathWithRevertingSource_Reverts_WriterPathUnaffected() public {
        oracle.setSettlementSource(address(new RevertingVerifier()));
        vm.expectRevert();
        oracle.recordVerifiedSettlement(agent, task1, sigil1, SincorValidationReputationOracle.Outcome.Success);
        oracle.recordValidation(agent, task1, sigil1, SincorValidationReputationOracle.Outcome.Success);
        assertEq(oracle.attestationCount(), 1);
    }

    function test_ReentrantVerifierCannotWriteDuringVerification() public {
        ReentrantVerifier rv = new ReentrantVerifier();
        rv.setTargets(oracle, agent2);
        oracle.setSettlementSource(address(rv));
        vm.prank(address(rv));
        oracle.recordVerifiedSettlement(agent, task1, sigil1, SincorValidationReputationOracle.Outcome.Success);
        // Exactly ONE attestation: the outer one. The reentrant write (for
        // agent2, same key — authorized caller) did not land: isTaskVerified
        // is invoked via staticcall, so state-changing reentry reverts.
        assertEq(oracle.attestationCount(), 1);
        (address a,,,,) = oracle.attestations(0);
        assertEq(a, agent);
        (int256 s2,,,) = oracle.reputationOf(agent2);
        assertEq(s2, 0);
    }

    // ---------- owner powers / history / funds ----------

    function test_OwnerCannotRewriteHistory() public {
        oracle.recordValidation(agent, task1, sigil1, SincorValidationReputationOracle.Outcome.Failure);
        oracle.setSettlementSource(address(0x9999));
        oracle.transferOwnership(stranger);
        (address a, bytes32 t, bytes32 s, SincorValidationReputationOracle.Outcome o,) = oracle.attestations(0);
        assertEq(a, agent); assertEq(t, task1); assertEq(s, sigil1); assertEq(uint256(o), 1);
        (int256 score,, uint256 fail,) = oracle.reputationOf(agent);
        assertEq(score, -10); assertEq(fail, 1);
        assertEq(oracle.attestationCount(), 1); // append-only: rotation/transfer changed nothing
    }

    function test_OwnershipTransferMovesAllPower_OldOwnerPowerless() public {
        oracle.transferOwnership(stranger);
        vm.expectRevert(SincorValidationReputationOracle.NotAuthorized.selector);
        oracle.recordValidation(agent, task1, sigil1, SincorValidationReputationOracle.Outcome.Success);
        vm.expectRevert(SincorValidationReputationOracle.NotOwner.selector);
        oracle.setSettlementSource(address(this));
        vm.startPrank(stranger); // new owner can write and rotate
        oracle.recordValidation(agent, task1, sigil1, SincorValidationReputationOracle.Outcome.Success);
        oracle.setSettlementSource(address(0x4242));
        vm.stopPrank();
        assertEq(oracle.attestationCount(), 1);
    }

    function test_ContractHoldsNoFunds_EtherRejected() public {
        vm.deal(address(this), 1 ether);
        (bool ok,) = address(oracle).call{value: 0.1 ether}("");
        assertFalse(ok); // no receive/fallback
        (bool ok2,) = address(oracle).call{value: 0.1 ether}(
            abi.encodeWithSelector(SincorValidationReputationOracle.recordValidation.selector, agent, task1, sigil1, uint256(0)));
        assertFalse(ok2); // nonpayable
        assertEq(address(oracle).balance, 0);
        assertEq(oracle.attestationCount(), 0);
    }

    // ---------- zero / boundary inputs ----------

    function test_ZeroTaskIdRejected() public {
        // Fixed: _record now guards zero taskId (ZeroTaskId) alongside
        // zero agent and zero sigil — on both recording paths.
        vm.expectRevert(SincorValidationReputationOracle.ZeroTaskId.selector);
        oracle.recordValidation(agent, bytes32(0), sigil1, SincorValidationReputationOracle.Outcome.Success);
        // Verified path too: hub is AlwaysTrue, so _record's guard must fire.
        vm.expectRevert(SincorValidationReputationOracle.ZeroTaskId.selector);
        oracle.recordVerifiedSettlement(agent, bytes32(0), sigil1, SincorValidationReputationOracle.Outcome.Success);
        assertEq(oracle.attestationCount(), 0);
        (int256 score, uint256 succ,,) = oracle.reputationOf(agent);
        assertEq(score, 0); assertEq(succ, 0);
    }

    function test_ZeroAgentAndSigilRejectedOnVerifiedPathToo() public {
        // hub is AlwaysTrue, so _record's guards are what must fire
        vm.expectRevert(SincorValidationReputationOracle.ZeroAgent.selector);
        oracle.recordVerifiedSettlement(address(0), task1, sigil1, SincorValidationReputationOracle.Outcome.Success);
        vm.expectRevert(SincorValidationReputationOracle.ZeroSigil.selector);
        oracle.recordVerifiedSettlement(agent, task1, bytes32(0), SincorValidationReputationOracle.Outcome.Success);
        assertEq(oracle.attestationCount(), 0);
    }

    function test_InvalidOutcomeEnum_Reverts() public {
        (bool ok,) = address(oracle).call(
            abi.encodeWithSelector(SincorValidationReputationOracle.recordValidation.selector, agent, task1, sigil1, uint256(2)));
        assertFalse(ok); // ABI decoder rejects out-of-range enum
        assertEq(oracle.attestationCount(), 0);
    }

    // ---------- identity / registration decoupling ----------

    function test_UnregisteredAndSelfAgentsGetReputation() public {
        assertFalse(identity.isRegistered(agent));
        oracle.recordValidation(agent, task1, sigil1, SincorValidationReputationOracle.Outcome.Success);
        oracle.recordValidation(address(this), keccak256("self-t"), keccak256("self-s"), SincorValidationReputationOracle.Outcome.Success); // owner as its own agent
        (int256 s1,,,) = oracle.reputationOf(agent);
        (int256 s2,,,) = oracle.reputationOf(address(this));
        assertEq(s1, 10); assertEq(s2, 10); // oracle never consults the identity stub
    }

    function test_IdentityStubPermissionlessSquatting() public {
        vm.prank(stranger);
        identity.registerAgent(agent); // stranger registers the VICTIM's address
        assertTrue(identity.isRegistered(agent));
        assertEq(identity.agentIdOf(agent), 1);
        vm.expectRevert("already registered");
        identity.registerAgent(agent); // victim can never register themselves
    }
}
