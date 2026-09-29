// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title KYARegistry
/// @notice Minimal on-chain pointer for off-chain KYA attestations.
/// @dev W-10 hardening (2026-09-29):
///      1. No-revive guard: revoke() permanently stamps everRevoked[kyaId].
///         list() reverts for any ever-revoked kyaId. A revoked attestation
///         can only be revived through the timelocked re-verification path
///         (requestReverification -> finalizeReverification after
///         REVERIFY_DELAY), which resets the record but keeps the kyaId
///         marked ever-revoked, so every revival needs a fresh timelocked
///         cycle and the re-verified record must pass verify() again.
///      2. Owner rotation is two-step: nominateOwner() by the current owner,
///         then acceptOwnership() by the nominee. A compromised owner key
///         alone cannot silently hand off ownership in one transaction.
contract KYARegistry {
    struct Attestation {
        bytes32 agentIdHash;
        address principal;
        bytes32 recordHash;
        uint64 updatedAt;
        bool verified;
        bool revoked;
    }

    /// @notice Minimum delay between a re-verification request and its
    ///         finalization. Gives monitors time to react to a revival.
    uint256 public constant REVERIFY_DELAY = 48 hours;

    address public owner;
    /// @notice Nominee from nominateOwner(); address(0) when no nomination is live.
    address public pendingOwner;

    mapping(bytes32 => Attestation) public byKya;
    mapping(bytes32 => bytes32) public agentToKya;
    /// @notice Set permanently by revoke(); never cleared. list() refuses
    ///         ever-revoked kyaIds outside the timelocked re-verification path.
    mapping(bytes32 => bool) public everRevoked;
    /// @notice block.timestamp of a pending re-verification request; 0 = none.
    mapping(bytes32 => uint256) public reverifyRequestedAt;

    event Listed(bytes32 indexed kyaId, bytes32 agentIdHash, address principal, bytes32 recordHash);
    event Verified(bytes32 indexed kyaId, bytes32 recordHash);
    event Revoked(bytes32 indexed kyaId, bytes32 recordHash);
    event ReverificationRequested(bytes32 indexed kyaId, uint64 requestedAt, uint64 readyAt);
    event Reverified(bytes32 indexed kyaId, bytes32 recordHash);
    event OwnerNominated(address indexed currentOwner, address indexed nominee);
    event NominationCancelled(address indexed currentOwner, address indexed nominee);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    error NotOwner();
    error NotNominee();
    error NoPendingNomination();
    error ZeroAddress();
    error InvalidNominee();
    error NotListed();
    error NotRevoked();
    error RevokedAlready();
    /// @notice Thrown by list() when the kyaId was revoked at any point in
    ///         the past. Use the timelocked re-verification path instead.
    error RevokedPreviously();
    error ReverificationNotRequested();
    error ReverifyDelayNotElapsed(uint64 readyAt);

    constructor() {
        owner = msg.sender;
    }

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    /// @notice List a new attestation or update an existing active one.
    /// @dev Reverts for ever-revoked kyaIds: revoked attestations are not
    ///      revivable by direct re-listing (W-10 no-revive guard).
    function list(
        bytes32 kyaId,
        bytes32 agentIdHash,
        address principal,
        bytes32 recordHash
    ) external onlyOwner {
        if (everRevoked[kyaId]) revert RevokedPreviously();
        Attestation storage a = byKya[kyaId];
        a.agentIdHash = agentIdHash;
        a.principal = principal;
        a.recordHash = recordHash;
        a.updatedAt = uint64(block.timestamp);
        a.revoked = false;
        agentToKya[agentIdHash] = kyaId;
        emit Listed(kyaId, agentIdHash, principal, recordHash);
    }

    function verify(bytes32 kyaId, bytes32 recordHash) external onlyOwner {
        Attestation storage a = byKya[kyaId];
        if (a.revoked) revert RevokedAlready();
        a.verified = true;
        a.recordHash = recordHash;
        a.updatedAt = uint64(block.timestamp);
        emit Verified(kyaId, recordHash);
    }

    /// @notice Revoke an attestation and permanently mark the kyaId
    ///         ever-revoked. Reverts for kyaIds that were never listed, so a
    ///         stray revoke cannot permanently burn an unused kyaId.
    function revoke(bytes32 kyaId, bytes32 recordHash) external onlyOwner {
        Attestation storage a = byKya[kyaId];
        if (a.updatedAt == 0) revert NotListed();
        a.revoked = true;
        a.verified = false;
        a.recordHash = recordHash;
        a.updatedAt = uint64(block.timestamp);
        everRevoked[kyaId] = true;
        emit Revoked(kyaId, recordHash);
    }

    /// @notice Step 1 of timelocked re-verification: start the delay clock for
    ///         an ever-revoked kyaId. Only meaningful for revoked kyaIds.
    function requestReverification(bytes32 kyaId) external onlyOwner {
        if (!everRevoked[kyaId]) revert NotRevoked();
        uint256 requestedAt = block.timestamp;
        reverifyRequestedAt[kyaId] = requestedAt;
        emit ReverificationRequested(
            kyaId,
            uint64(requestedAt),
            uint64(requestedAt + REVERIFY_DELAY)
        );
    }

    /// @notice Step 2 of timelocked re-verification: after REVERIFY_DELAY has
    ///         elapsed since the request, replace the attestation record.
    ///         The kyaId stays marked ever-revoked (each revival needs a fresh
    ///         timelocked cycle) and the fresh record starts unverified.
    function finalizeReverification(
        bytes32 kyaId,
        bytes32 agentIdHash,
        address principal,
        bytes32 recordHash
    ) external onlyOwner {
        if (!everRevoked[kyaId]) revert NotRevoked();
        uint256 requestedAt = reverifyRequestedAt[kyaId];
        if (requestedAt == 0) revert ReverificationNotRequested();
        uint256 readyAt = requestedAt + REVERIFY_DELAY;
        if (block.timestamp < readyAt) revert ReverifyDelayNotElapsed(uint64(readyAt));
        reverifyRequestedAt[kyaId] = 0;
        Attestation storage a = byKya[kyaId];
        a.agentIdHash = agentIdHash;
        a.principal = principal;
        a.recordHash = recordHash;
        a.updatedAt = uint64(block.timestamp);
        a.revoked = false;
        a.verified = false;
        agentToKya[agentIdHash] = kyaId;
        emit Reverified(kyaId, recordHash);
    }

    /// @notice Owner-rotation step 1: nominate a successor. Ownership does NOT
    ///         change here; the nominee must call acceptOwnership().
    function nominateOwner(address nominee) external onlyOwner {
        if (nominee == address(0)) revert ZeroAddress();
        if (nominee == owner) revert InvalidNominee();
        pendingOwner = nominee;
        emit OwnerNominated(owner, nominee);
    }

    /// @notice Cancel a live nomination. Only the current owner can cancel.
    function cancelNomination() external onlyOwner {
        address nominee = pendingOwner;
        if (nominee == address(0)) revert NoPendingNomination();
        pendingOwner = address(0);
        emit NominationCancelled(owner, nominee);
    }

    /// @notice Owner-rotation step 2: the nominee accepts and becomes owner.
    ///         Callable only by the nominated address while a nomination is live.
    function acceptOwnership() external {
        address nominee = pendingOwner;
        if (nominee == address(0)) revert NoPendingNomination();
        if (msg.sender != nominee) revert NotNominee();
        address previousOwner = owner;
        owner = nominee;
        pendingOwner = address(0);
        emit OwnershipTransferred(previousOwner, nominee);
    }
}
