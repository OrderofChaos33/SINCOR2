// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title KYARegistry
/// @notice Minimal on-chain pointer for off-chain KYA attestations.
///         Not deployed in this drop.
contract KYARegistry {
    struct Attestation {
        bytes32 agentIdHash;
        address principal;
        bytes32 recordHash;
        uint64 updatedAt;
        bool verified;
        bool revoked;
    }

    address public owner;
    mapping(bytes32 => Attestation) public byKya;
    mapping(bytes32 => bytes32) public agentToKya;

    event Listed(bytes32 indexed kyaId, bytes32 agentIdHash, address principal, bytes32 recordHash);
    event Verified(bytes32 indexed kyaId, bytes32 recordHash);
    event Revoked(bytes32 indexed kyaId, bytes32 recordHash);

    error NotOwner();
    error RevokedAlready();

    constructor() {
        owner = msg.sender;
    }

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    function list(
        bytes32 kyaId,
        bytes32 agentIdHash,
        address principal,
        bytes32 recordHash
    ) external onlyOwner {
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

    function revoke(bytes32 kyaId, bytes32 recordHash) external onlyOwner {
        Attestation storage a = byKya[kyaId];
        a.revoked = true;
        a.verified = false;
        a.recordHash = recordHash;
        a.updatedAt = uint64(block.timestamp);
        emit Revoked(kyaId, recordHash);
    }
}
