// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title KYARegistry
/// @notice Minimal on-chain pointer for off-chain KYA attestations.
/// @dev W-10 hardening (2026-09-29, reworked after adversarial review):
///      1. Per-ACTOR no-revive tombstone (not just per-kyaId). revoke()
///         permanently stamps the actor's identities --
///         revokedAgent[agentIdHash] and revokedPrincipal[principal].
///         An "actor" is the (agentIdHash, principal) binding: either
///         component being tombstoned is enough to force the timelocked
///         path, because kyaIds are minted at will by the owner and a
///         kyaId-keyed guard alone lets a revoked actor walk back in
///         under a fresh kyaId. verify() reverts ActorPreviouslyRevoked()
///         while either identity is tombstoned, so a fresh kyaId for the
///         same agent hash and/or wallet can be LISTED (visible on-chain)
///         but can never become VERIFIED instantly.
///      2. Timelocked re-verification (requestReverification ->
///         finalizeReverification after REVERIFY_DELAY) is the ONLY way a
///         tombstoned actor re-enters. It works against the old kyaId or a
///         fresh one, rewrites the record unverified, and clears the
///         tombstone for exactly the identities it admits. Every revival
///         still needs a fresh timelocked cycle, and the kyaId stays
///         marked ever-revoked.
///      3. The re-verification path requires the attestation to be
///         CURRENTLY revoked (kyaId path) or to name a tombstoned actor
///         (actor path). It is NOT a silent live-record rewrite: a live
///         attestation must be revoked first (emitting Revoked) before it
///         can go through the cycle. Live updates use list().
///      4. agentToKya pointers are kept fresh: cleared on revoke() and
///         whenever an attestation's agent hash changes (list() update or
///         finalizeReverification), so no stale pointer survives.
///      5. Owner rotation is two-step: nominateOwner() by the current owner,
///         then acceptOwnership() by the nominee. This is FAT-FINGER
///         protection only -- it proves the nominee controls the new key
///         before ownership moves. It does NOT mitigate a compromised
///         owner key: the key holder can cancel any nomination and
///         nominate an attacker address instead. A rotation timelock is a
///         follow-up, out of scope for W-10.
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
    /// @notice Actor-level tombstone: agent-id hashes burned by revoke().
    ///         verify() reverts while set; cleared only by a completed
    ///         timelocked re-verification that re-admits the hash.
    mapping(bytes32 => bool) public revokedAgent;
    /// @notice Actor-level tombstone: principal wallets burned by revoke().
    ///         verify() reverts while set; cleared only by a completed
    ///         timelocked re-verification that re-admits the wallet.
    mapping(address => bool) public revokedPrincipal;
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
    /// @notice Thrown by verify() when the attestation names a tombstoned
    ///         actor identity (revoked agent hash and/or principal wallet).
    ///         Re-admission requires the timelocked re-verification path;
    ///         minting a fresh kyaId for the same actor does not help.
    error ActorPreviouslyRevoked();
    error ReverificationNotRequested();
    error ReverifyDelayNotElapsed(uint64 readyAt);

    constructor() {
        owner = msg.sender;
    }

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    /// @notice True when either identity component of the attestation's
    ///         actor is tombstoned.
    function _actorTombstoned(Attestation storage a) internal view returns (bool) {
        return revokedAgent[a.agentIdHash] || revokedPrincipal[a.principal];
    }

    /// @notice List a new attestation or update an existing active one.
    /// @dev Reverts for ever-revoked kyaIds: revoked attestations are not
    ///      revivable by direct re-listing (W-10 no-revive guard). Listing a
    ///      tombstoned actor is allowed -- the record is visible on-chain --
    ///      but verify() will refuse it until the timelocked path clears
    ///      the tombstone. Also clears a stale agentToKya pointer when the
    ///      update moves the attestation to a new agent hash.
    function list(
        bytes32 kyaId,
        bytes32 agentIdHash,
        address principal,
        bytes32 recordHash
    ) external onlyOwner {
        if (everRevoked[kyaId]) revert RevokedPreviously();
        Attestation storage a = byKya[kyaId];
        bytes32 oldHash = a.agentIdHash;
        a.agentIdHash = agentIdHash;
        a.principal = principal;
        a.recordHash = recordHash;
        a.updatedAt = uint64(block.timestamp);
        a.revoked = false;
        if (oldHash != bytes32(0) && oldHash != agentIdHash && agentToKya[oldHash] == kyaId) {
            delete agentToKya[oldHash];
        }
        agentToKya[agentIdHash] = kyaId;
        emit Listed(kyaId, agentIdHash, principal, recordHash);
    }

    /// @notice Mark the attestation verified.
    /// @dev Reverts for tombstoned actors: a revoked actor cannot become
    ///      verified under a fresh kyaId. Re-admission goes through the
    ///      timelocked re-verification path.
    function verify(bytes32 kyaId, bytes32 recordHash) external onlyOwner {
        Attestation storage a = byKya[kyaId];
        if (a.revoked) revert RevokedAlready();
        if (_actorTombstoned(a)) revert ActorPreviouslyRevoked();
        a.verified = true;
        a.recordHash = recordHash;
        a.updatedAt = uint64(block.timestamp);
        emit Verified(kyaId, recordHash);
    }

    /// @notice Revoke an attestation, permanently mark the kyaId
    ///         ever-revoked, and tombstone the actor's identities (agent
    ///         hash and principal wallet). Reverts for kyaIds that were
    ///         never listed, so a stray revoke cannot permanently burn an
    ///         unused kyaId.
    /// @dev Also clears the agentToKya pointer when it still resolves to
    ///      this kyaId, so no stale pointer survives the revocation.
    function revoke(bytes32 kyaId, bytes32 recordHash) external onlyOwner {
        Attestation storage a = byKya[kyaId];
        if (a.updatedAt == 0) revert NotListed();
        a.revoked = true;
        a.verified = false;
        a.recordHash = recordHash;
        a.updatedAt = uint64(block.timestamp);
        everRevoked[kyaId] = true;
        revokedAgent[a.agentIdHash] = true;
        revokedPrincipal[a.principal] = true;
        if (agentToKya[a.agentIdHash] == kyaId) {
            delete agentToKya[a.agentIdHash];
        }
        emit Revoked(kyaId, recordHash);
    }

    /// @notice Step 1 of timelocked re-verification: start the delay clock.
    /// @dev Admits two cases: (a) a currently-revoked, ever-revoked kyaId;
    ///      (b) a live (unrevoked) attestation naming a tombstoned actor --
    ///      i.e. re-admission of a revoked actor under a fresh kyaId. A
    ///      live attestation of a never-tombstoned actor is NOT admissible:
    ///      live updates go through list(), not this path.
    function requestReverification(bytes32 kyaId) external onlyOwner {
        Attestation storage a = byKya[kyaId];
        if (a.updatedAt == 0) revert NotListed();
        bool kyaPath = everRevoked[kyaId] && a.revoked;
        if (!kyaPath && !_actorTombstoned(a)) revert NotRevoked();
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
    /// @dev Clears the actor tombstone for exactly the identities named on
    ///      the new record -- that is the re-admission. Any other tombstoned
    ///      identities stay burned. Also clears a stale agentToKya pointer
    ///      when the agent hash changes.
    function finalizeReverification(
        bytes32 kyaId,
        bytes32 agentIdHash,
        address principal,
        bytes32 recordHash
    ) external onlyOwner {
        Attestation storage a = byKya[kyaId];
        if (a.updatedAt == 0) revert NotListed();
        bool kyaPath = everRevoked[kyaId] && a.revoked;
        if (!kyaPath && !_actorTombstoned(a)) revert NotRevoked();
        uint256 requestedAt = reverifyRequestedAt[kyaId];
        if (requestedAt == 0) revert ReverificationNotRequested();
        uint256 readyAt = requestedAt + REVERIFY_DELAY;
        if (block.timestamp < readyAt) revert ReverifyDelayNotElapsed(uint64(readyAt));
        reverifyRequestedAt[kyaId] = 0;
        bytes32 oldHash = a.agentIdHash;
        a.agentIdHash = agentIdHash;
        a.principal = principal;
        a.recordHash = recordHash;
        a.updatedAt = uint64(block.timestamp);
        a.revoked = false;
        a.verified = false;
        revokedAgent[agentIdHash] = false;
        revokedPrincipal[principal] = false;
        if (oldHash != agentIdHash && agentToKya[oldHash] == kyaId) {
            delete agentToKya[oldHash];
        }
        agentToKya[agentIdHash] = kyaId;
        emit Reverified(kyaId, recordHash);
    }

    /// @notice Owner-rotation step 1: nominate a successor. Ownership does NOT
    ///         change here; the nominee must call acceptOwnership().
    /// @dev Fat-finger protection only: the nominee proves control of the
    ///      new key by accepting. A compromised owner key can cancel any
    ///      nomination and nominate an attacker address instead -- two-step
    ///      rotation does not mitigate key compromise.
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
