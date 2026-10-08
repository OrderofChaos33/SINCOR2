// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {ComplianceGuard} from "../src/ComplianceGuard.sol";
import {MockSanctionsOracle} from "./mocks/MockSanctionsOracle.sol";

/// @notice Fail-closed tests for ComplianceGuard (C2 remediation, 2026-10-08).
///         The fail-closed flip was ratified 2026-09-29: with no configured
///         oracle the contract DENIES every account — it is never a rubber
///         stamp. The guardian must call setOracle before anyone is allowed.
contract ComplianceGuardTest is Test {
    ComplianceGuard guard;
    MockSanctionsOracle oracle;

    address guardian = makeAddr("guardian");
    address user = makeAddr("user");

    function setUp() public {
        guard = new ComplianceGuard(guardian);
        oracle = new MockSanctionsOracle();
    }

    function _enableOracle() internal {
        vm.prank(guardian);
        guard.setOracle(address(oracle), true);
    }

    // -- fail-closed: no oracle -------------------------------------------

    function test_noOracle_deniesEveryone() public view {
        assertFalse(guard.isAllowed(user), "no oracle configured: must deny");
        assertFalse(
            guard.isAllowed(guardian),
            "no oracle configured: must deny even the guardian"
        );
    }

    function test_oracleDisabled_denies() public {
        vm.prank(guardian);
        guard.setOracle(address(oracle), false);
        assertFalse(guard.isAllowed(user), "disabled oracle: must deny");
    }

    function test_oracleZeroAddress_denies() public {
        vm.prank(guardian);
        guard.setOracle(address(0), true);
        assertFalse(guard.isAllowed(user), "zero oracle address: must deny");
    }

    // -- blocklist still enforced ------------------------------------------

    function test_blocked_deniesWithOracle() public {
        _enableOracle();
        vm.prank(guardian);
        guard.block(user);
        assertFalse(guard.isAllowed(user), "blocked account: must deny");
    }

    function test_blocked_deniesWithoutOracle() public {
        vm.prank(guardian);
        guard.block(user);
        assertFalse(guard.isAllowed(user), "blocked account: must deny");
    }

    function test_unblock_restoresAllowWithOracle() public {
        _enableOracle();
        vm.startPrank(guardian);
        guard.block(user);
        guard.unblock(user);
        vm.stopPrank();
        assertTrue(guard.isAllowed(user), "unblocked clean account: must allow");
    }

    // -- oracle screening ---------------------------------------------------

    function test_sanctioned_denies() public {
        _enableOracle();
        oracle.setSanctioned(user, true);
        assertFalse(guard.isAllowed(user), "sanctioned account: must deny");
    }

    function test_cleanAllows() public {
        _enableOracle();
        assertTrue(guard.isAllowed(user), "clean account: must allow");
    }

    // -- access control ------------------------------------------------------

    function test_onlyGuardianCanSetOracle() public {
        vm.prank(user);
        vm.expectRevert(ComplianceGuard.OnlyGuardian.selector);
        guard.setOracle(address(oracle), true);
    }

    function test_onlyGuardianCanBlock() public {
        vm.prank(user);
        vm.expectRevert(ComplianceGuard.OnlyGuardian.selector);
        guard.block(user);
    }
}
