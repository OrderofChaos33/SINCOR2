// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";

/// @title EpochSessionKeyValidator
/// @notice ERC-7579 compatible epoch-root validator for session-key-gated execution proofs.
contract EpochSessionKeyValidator is AccessControl {
    bytes32 public constant EPOCH_ADMIN_ROLE = keccak256("EPOCH_ADMIN_ROLE");
    bytes32 public constant SESSION_VALIDATOR_ROLE = keccak256("SESSION_VALIDATOR_ROLE");

    struct EpochRoot {
        bytes32 merkleRoot;
        uint64 activatedAt;
        bool active;
    }

    mapping(bytes32 epochId => EpochRoot) public epochRoots;

    event EpochRootPublished(bytes32 indexed epochId, bytes32 merkleRoot, uint64 activatedAt);
    event EpochRootRevoked(bytes32 indexed epochId);

    error InvalidConfig();
    error EpochInactive();
    error RootMismatch();

    constructor(address admin) {
        if (admin == address(0)) revert InvalidConfig();
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(EPOCH_ADMIN_ROLE, admin);
        _grantRole(SESSION_VALIDATOR_ROLE, admin);
    }

    function publishEpochRoot(bytes32 epochId, bytes32 merkleRoot) external onlyRole(EPOCH_ADMIN_ROLE) {
        if (epochId == bytes32(0) || merkleRoot == bytes32(0)) revert InvalidConfig();
        epochRoots[epochId] = EpochRoot({merkleRoot: merkleRoot, activatedAt: uint64(block.timestamp), active: true});
        emit EpochRootPublished(epochId, merkleRoot, uint64(block.timestamp));
    }

    function revokeEpochRoot(bytes32 epochId) external onlyRole(EPOCH_ADMIN_ROLE) {
        EpochRoot storage root = epochRoots[epochId];
        root.active = false;
        emit EpochRootRevoked(epochId);
    }

    function validateExecutionProof(bytes32 epochId, bytes32 merkleRoot) public view returns (bool) {
        EpochRoot memory root = epochRoots[epochId];
        if (!root.active) revert EpochInactive();
        if (root.merkleRoot != merkleRoot) revert RootMismatch();
        return true;
    }
}
