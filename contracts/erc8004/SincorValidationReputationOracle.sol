// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title SincorValidationReputationOracle — ERC-8004-STYLE (draft), NOT final-spec conformance
/// @notice Validation + Reputation recording driven by SIGIL settlements.
/// Trust assumptions (precise, for adversarial review):
///  1. This contract does NOT verify SIGIL output itself. Verification happens
///     off-chain / in the settlement facilitator, exactly as in the reference
///     SincorSettlementHub draft: the Hub RECORDS a sigilCommitment; its
///     isTaskVerified(taskId, sigil) proves a commitment was recorded, not
///     that on-chain verification occurred.
///  2. recordVerifiedSettlement trusts (a) the configured settlementSource's
///     isTaskVerified answer and (b) that only owner/settlementSource call it.
///  3. recordValidation trusts the caller entirely: owner or settlementSource
///     asserting an outcome. No hub check is performed on that path.
///  4. Reputation is a deterministic bounded counter, not a quality judgement.

interface ISettlementVerifier {
    function isTaskVerified(bytes32 taskId, bytes32 expectedSigil) external view returns (bool);
}

/// @notice Minimal Identity registry STUB (ERC-8004-style Identity reference only).
/// Not a full Identity Registry: no metadata, no transfer, no agent wallet logic.
contract SincorIdentityRegistryStub {
    mapping(address => bool) public isRegistered;
    mapping(address => uint256) public agentIdOf;
    uint256 public nextAgentId = 1;
    event AgentRegistered(address indexed agent, uint256 indexed agentId);
    function registerAgent(address agent) external returns (uint256 id) {
        require(agent != address(0), "zero agent");
        require(!isRegistered[agent], "already registered");
        id = nextAgentId++;
        isRegistered[agent] = true;
        agentIdOf[agent] = id;
        emit AgentRegistered(agent, id);
    }
}

contract SincorValidationReputationOracle {
    enum Outcome { Success, Failure }

    struct Attestation {
        address agent;
        bytes32 taskId;
        bytes32 sigilCommitment;
        Outcome outcome;
        uint256 timestamp;
    }

    struct Reputation {
        int256 score;
        uint256 successes;
        uint256 failures;
    }

    int256 public constant SCORE_MAX = 1000;
    int256 public constant SCORE_MIN = -100;
    int256 public constant SUCCESS_DELTA = 10;
    int256 public constant FAILURE_DELTA = -10;

    address public owner;
    address public settlementSource; // configured hub address; may be address(0) until set

    Attestation[] public attestations;
    mapping(bytes32 => bool) public attestationSeen; // keccak256(taskId, sigilCommitment)
    mapping(address => Reputation) private _reputation;

    error NotAuthorized();
    error ReplayBlocked();
    error ZeroAgent();
    error ZeroTaskId();
    error ZeroSigil();
    error SettlementNotVerified();
    error NotOwner();

    event ValidationRecorded(address indexed agent, bytes32 indexed taskId, bytes32 sigilCommitment, Outcome outcome, int256 newScore);
    event SettlementSourceSet(address indexed source);
    event OwnershipTransferred(address indexed newOwner);

    modifier onlyOwner() { if (msg.sender != owner) revert NotOwner(); _; }
    modifier onlyWriter() { if (msg.sender != owner && msg.sender != settlementSource) revert NotAuthorized(); _; }

    constructor(address _settlementSource) {
        owner = msg.sender;
        settlementSource = _settlementSource;
        if (_settlementSource != address(0)) emit SettlementSourceSet(_settlementSource);
    }

    function setSettlementSource(address source) external onlyOwner {
        settlementSource = source;
        emit SettlementSourceSet(source);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "zero owner");
        owner = newOwner;
        emit OwnershipTransferred(newOwner);
    }

    function attestationKey(bytes32 taskId, bytes32 sigilCommitment) public pure returns (bytes32) {
        return keccak256(abi.encode(taskId, sigilCommitment));
    }

    /// @notice Writer-asserted path: owner/settlementSource only, no hub check.
    function recordValidation(address agent, bytes32 taskId, bytes32 sigilCommitment, Outcome outcome) external onlyWriter {
        _record(agent, taskId, sigilCommitment, outcome);
    }

    /// @notice Settlement-verified path: writer only AND hub must report the
    /// (taskId, sigilCommitment) pair as verified via isTaskVerified.
    function recordVerifiedSettlement(address agent, bytes32 taskId, bytes32 sigilCommitment, Outcome outcome) external onlyWriter {
        if (settlementSource == address(0)) revert SettlementNotVerified();
        if (!ISettlementVerifier(settlementSource).isTaskVerified(taskId, sigilCommitment)) revert SettlementNotVerified();
        _record(agent, taskId, sigilCommitment, outcome);
    }

    function _record(address agent, bytes32 taskId, bytes32 sigilCommitment, Outcome outcome) internal {
        if (agent == address(0)) revert ZeroAgent();
        if (taskId == bytes32(0)) revert ZeroTaskId();
        if (sigilCommitment == bytes32(0)) revert ZeroSigil();
        bytes32 key = attestationKey(taskId, sigilCommitment);
        if (attestationSeen[key]) revert ReplayBlocked();
        attestationSeen[key] = true;

        attestations.push(Attestation(agent, taskId, sigilCommitment, outcome, block.timestamp));
        Reputation storage rep = _reputation[agent];
        if (outcome == Outcome.Success) {
            rep.successes++;
            rep.score = rep.score + SUCCESS_DELTA > SCORE_MAX ? SCORE_MAX : rep.score + SUCCESS_DELTA;
        } else {
            rep.failures++;
            rep.score = rep.score + FAILURE_DELTA < SCORE_MIN ? SCORE_MIN : rep.score + FAILURE_DELTA;
        }
        emit ValidationRecorded(agent, taskId, sigilCommitment, outcome, rep.score);
    }

    function reputationOf(address agent) external view returns (int256 score, uint256 successes, uint256 failures, uint256 total) {
        Reputation storage r = _reputation[agent];
        return (r.score, r.successes, r.failures, r.successes + r.failures);
    }

    function attestationCount() external view returns (uint256) { return attestations.length; }
}
