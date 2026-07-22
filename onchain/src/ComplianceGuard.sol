// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

/// @notice Minimal surface shared by sanctions-oracle providers
///         (Chainalysis-style on-chain screening exposes isSanctioned(address)).
interface ISanctionsOracle {
    function isSanctioned(address addr) external view returns (bool);
}

/// @notice Consumed by SincFluidAdapter / future vault v2 to gate user entry.
interface IComplianceGuard {
    function isAllowed(address account) external view returns (bool);
}

/// @title  ComplianceGuard — sanctions screening + manual blocklist (Base)
/// @notice Production compliance gate: optional on-chain sanctions oracle plus a
///         guardian-controlled blocklist. Oracle address is deliberately NOT
///         hardcoded — legal/compliance picks the provider, guardian sets it once.
///         ZK-attestation screening (prove non-membership without revealing address)
///         is the documented upgrade path; this contract is the enforcement point
///         either way, so the swap to ZK later requires no integrator changes.
/// @dev    Fail-closed on explicit block, fail-open only when no oracle is set
///         (screening effectively blocklist-only until oracleEnabled).
contract ComplianceGuard is IComplianceGuard {
    address public guardian;
    ISanctionsOracle public sanctionsOracle;
    bool public oracleEnabled;
    mapping(address account => bool) public blocked;

    event OracleUpdated(address oracle, bool enabled);
    event Blocked(address indexed account);
    event Unblocked(address indexed account);
    event GuardianUpdated(address indexed newGuardian);

    error OnlyGuardian();
    error ZeroAddress();

    modifier onlyGuardian() {
        if (msg.sender != guardian) revert OnlyGuardian();
        _;
    }

    constructor(address _guardian) {
        if (_guardian == address(0)) revert ZeroAddress();
        guardian = _guardian;
    }

    /// @notice True when the account may interact. Blocklist always applies;
    ///         oracle applies only when enabled AND set.
    function isAllowed(address account) external view returns (bool) {
        if (blocked[account]) return false;
        if (oracleEnabled && address(sanctionsOracle) != address(0)) {
            if (sanctionsOracle.isSanctioned(account)) return false;
        }
        return true;
    }

    function setOracle(address oracle, bool enabled) external onlyGuardian {
        sanctionsOracle = ISanctionsOracle(oracle);
        oracleEnabled = enabled;
        emit OracleUpdated(oracle, enabled);
    }

    function block(address account) external onlyGuardian {
        blocked[account] = true;
        emit Blocked(account);
    }

    function unblock(address account) external onlyGuardian {
        blocked[account] = false;
        emit Unblocked(account);
    }

    function blockBatch(address[] calldata accounts) external onlyGuardian {
        for (uint256 i = 0; i < accounts.length; i++) {
            blocked[accounts[i]] = true;
            emit Blocked(accounts[i]);
        }
    }

    function setGuardian(address newGuardian) external onlyGuardian {
        if (newGuardian == address(0)) revert ZeroAddress();
        guardian = newGuardian;
        emit GuardianUpdated(newGuardian);
    }
}
