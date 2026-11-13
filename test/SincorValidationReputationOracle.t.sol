// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {SincorValidationReputationOracle, SincorIdentityRegistryStub} from "../contracts/erc8004/SincorValidationReputationOracle.sol";

contract MockSettlementHub {
    mapping(bytes32 => bytes32) public verifiedTaskSigils;
    function setVerified(bytes32 taskId, bytes32 sigil) external { verifiedTaskSigils[taskId] = sigil; }
    function isTaskVerified(bytes32 taskId, bytes32 expectedSigil) external view returns (bool) {
        bytes32 recorded = verifiedTaskSigils[taskId];
        return recorded != bytes32(0) && recorded == expectedSigil;
    }
}

contract SincorValidationReputationOracleTest is Test {
    SincorValidationReputationOracle oracle;
    MockSettlementHub hub;
    SincorIdentityRegistryStub identity;
    address agent = address(0xA6E7);
    address stranger = address(0xBAD);
    bytes32 task1 = keccak256("task-1");
    bytes32 sigil1 = keccak256("sigil-1");

    function setUp() public {
        hub = new MockSettlementHub();
        oracle = new SincorValidationReputationOracle(address(hub));
        identity = new SincorIdentityRegistryStub();
    }

    // Full loop: settlement verified -> attestation recorded -> reputation updated
    function test_FullLoop_VerifiedSettlementRecordsAttestationAndReputation() public {
        hub.setVerified(task1, sigil1);
        // settlementSource (hub) is the writer, mirroring hub-driven recording
        vm.prank(address(hub));
        oracle.recordVerifiedSettlement(agent, task1, sigil1, SincorValidationReputationOracle.Outcome.Success);
        assertEq(oracle.attestationCount(), 1);
        (address a, bytes32 t, bytes32 s, SincorValidationReputationOracle.Outcome o, uint256 ts) = oracle.attestations(0);
        assertEq(a, agent); assertEq(t, task1); assertEq(s, sigil1);
        assertEq(uint256(o), 0); assertGt(ts, 0);
        (int256 score, uint256 succ, uint256 fail, uint256 total) = oracle.reputationOf(agent);
        assertEq(score, 10); assertEq(succ, 1); assertEq(fail, 0); assertEq(total, 1);
    }

    function test_VerifiedPathBlockedWhenHubHasNotVerified() public {
        vm.prank(address(hub));
        vm.expectRevert(SincorValidationReputationOracle.SettlementNotVerified.selector);
        oracle.recordVerifiedSettlement(agent, task1, sigil1, SincorValidationReputationOracle.Outcome.Success);
    }

    function test_ReplayBlockedAndDoesNotDoubleCount() public {
        hub.setVerified(task1, sigil1);
        vm.startPrank(address(hub));
        oracle.recordVerifiedSettlement(agent, task1, sigil1, SincorValidationReputationOracle.Outcome.Success);
        vm.expectRevert(SincorValidationReputationOracle.ReplayBlocked.selector);
        oracle.recordVerifiedSettlement(agent, task1, sigil1, SincorValidationReputationOracle.Outcome.Success);
        vm.stopPrank();
        (int256 score,,,) = oracle.reputationOf(agent);
        assertEq(score, 10);
        assertEq(oracle.attestationCount(), 1);
    }

    function test_ReplayBlockedAcrossPaths() public {
        oracle.recordValidation(agent, task1, sigil1, SincorValidationReputationOracle.Outcome.Success);
        hub.setVerified(task1, sigil1);
        vm.prank(address(hub));
        vm.expectRevert(SincorValidationReputationOracle.ReplayBlocked.selector);
        oracle.recordVerifiedSettlement(agent, task1, sigil1, SincorValidationReputationOracle.Outcome.Success);
    }

    function test_UnauthorizedWriterBlocked() public {
        vm.prank(stranger);
        vm.expectRevert(SincorValidationReputationOracle.NotAuthorized.selector);
        oracle.recordValidation(agent, task1, sigil1, SincorValidationReputationOracle.Outcome.Success);
        vm.prank(stranger);
        vm.expectRevert(SincorValidationReputationOracle.NotAuthorized.selector);
        oracle.recordVerifiedSettlement(agent, task1, sigil1, SincorValidationReputationOracle.Outcome.Success);
    }

    function test_OwnerCanWriteDirectPath() public {
        oracle.recordValidation(agent, task1, sigil1, SincorValidationReputationOracle.Outcome.Success);
        assertEq(oracle.attestationCount(), 1);
    }

    function test_FailureDecrementsReputation() public {
        oracle.recordValidation(agent, task1, sigil1, SincorValidationReputationOracle.Outcome.Failure);
        (int256 score,, uint256 fail,) = oracle.reputationOf(agent);
        assertEq(score, -10); assertEq(fail, 1);
    }

    function test_ScoreBoundedAboveAndBelow() public {
        for (uint256 i = 0; i < 105; i++) {
            oracle.recordValidation(agent, keccak256(abi.encode("t", i)), keccak256(abi.encode("s", i)), SincorValidationReputationOracle.Outcome.Success);
        }
        (int256 score,,,) = oracle.reputationOf(agent);
        assertEq(score, 1000);
        address agent2 = address(0xBEEF);
        for (uint256 i = 0; i < 15; i++) {
            oracle.recordValidation(agent2, keccak256(abi.encode("t2", i)), keccak256(abi.encode("s2", i)), SincorValidationReputationOracle.Outcome.Failure);
        }
        (int256 score2,,,) = oracle.reputationOf(agent2);
        assertEq(score2, -100);
    }

    function test_ZeroAgentAndZeroSigilRevert() public {
        vm.expectRevert(SincorValidationReputationOracle.ZeroAgent.selector);
        oracle.recordValidation(address(0), task1, sigil1, SincorValidationReputationOracle.Outcome.Success);
        vm.expectRevert(SincorValidationReputationOracle.ZeroSigil.selector);
        oracle.recordValidation(agent, task1, bytes32(0), SincorValidationReputationOracle.Outcome.Success);
    }

    function test_DifferentSigilSameTaskIsNewAttestation() public {
        oracle.recordValidation(agent, task1, sigil1, SincorValidationReputationOracle.Outcome.Success);
        oracle.recordValidation(agent, task1, keccak256("sigil-2"), SincorValidationReputationOracle.Outcome.Success);
        assertEq(oracle.attestationCount(), 2);
    }

    function test_OnlyOwnerSetsSettlementSource() public {
        vm.prank(stranger);
        vm.expectRevert(SincorValidationReputationOracle.NotOwner.selector);
        oracle.setSettlementSource(stranger);
        oracle.setSettlementSource(address(0x1234));
        assertEq(oracle.settlementSource(), address(0x1234));
    }

    function test_IdentityStubRegisterAndDuplicateBlocked() public {
        uint256 id = identity.registerAgent(agent);
        assertEq(id, 1); assertTrue(identity.isRegistered(agent));
        vm.expectRevert("already registered");
        identity.registerAgent(agent);
    }
}
