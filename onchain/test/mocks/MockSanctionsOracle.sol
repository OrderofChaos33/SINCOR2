// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {ISanctionsOracle} from "../../src/ComplianceGuard.sol";

/// @notice Test-only sanctions oracle with guardian-toggled flags.
contract MockSanctionsOracle is ISanctionsOracle {
    mapping(address account => bool) public sanctioned;

    function setSanctioned(address account, bool flag) external {
        sanctioned[account] = flag;
    }

    function isSanctioned(address account) external view returns (bool) {
        return sanctioned[account];
    }
}
