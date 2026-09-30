// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Test.sol";
import {CreatorTokenFactory} from "../src/p24/CreatorTokenFactory.sol";

/// @notice Access-control tests for the P24 CreatorTokenFactory.
/// Backlog item 12 (P2 wiring tests): admin-only issue, screened gate,
/// symbol uniqueness, zero-address creator rejection.
/// NOTE: forge-std is not vendored in this repo yet, so `forge test` only
/// runs in CI once libs are present. The identical assertions are mirrored
/// in tests/pytest/test_defi_p24_wiring_full.py, which runs green locally
/// against the same contract compiled with solc 0.8.24 on eth-tester.
contract P24AccessControlTest is Test {
    CreatorTokenFactory factory;
    address admin = makeAddr("admin");
    address attacker = makeAddr("attacker");
    address creator = makeAddr("creator");
    address curveInventory = makeAddr("curveInventory");

    event TokenIssued(address indexed token, string symbol, address indexed creator, string policyVersion);

    function setUp() public {
        factory = new CreatorTokenFactory(admin);
    }

    function _issueArgs() internal view returns (
        string memory, string memory, address, address, string memory, bool
    ) {
        return ("Test Token", "TST", creator, curveInventory, "1.0.0", true);
    }

    function test_OnlyAdminCanIssue() public {
        (string memory n, string memory s, address c, address ci, string memory pv, bool sc) = _issueArgs();
        vm.prank(attacker);
        vm.expectRevert(CreatorTokenFactory.NotAdmin.selector);
        factory.issue(n, s, c, ci, pv, sc);
    }

    function test_UnscreenedReverts() public {
        (string memory n, string memory s, address c, address ci, string memory pv,) = _issueArgs();
        vm.prank(admin);
        vm.expectRevert(CreatorTokenFactory.NotScreened.selector);
        factory.issue(n, s, c, ci, pv, false);
    }

    function test_DuplicateSymbolReverts() public {
        (string memory n, string memory s, address c, address ci, string memory pv, bool sc) = _issueArgs();
        vm.prank(admin);
        factory.issue(n, s, c, ci, pv, sc);
        vm.prank(admin);
        vm.expectRevert(CreatorTokenFactory.SymbolTaken.selector);
        factory.issue("Other Name", s, makeAddr("other"), ci, pv, sc);
    }

    function test_ZeroCreatorReverts() public {
        (string memory n, string memory s,, address ci, string memory pv, bool sc) = _issueArgs();
        vm.prank(admin);
        vm.expectRevert(CreatorTokenFactory.ZeroCreator.selector);
        factory.issue(n, s, address(0), ci, pv, sc);
    }

    function test_HappyPathIssuesAndEmits() public {
        (string memory n, string memory s, address c, address ci, string memory pv, bool sc) = _issueArgs();
        vm.prank(admin);
        vm.expectEmit(false, true, false, true);
        emit TokenIssued(address(0), s, c, pv); // token topic not checked; asserted below
        address token = factory.issue(n, s, c, ci, pv, sc);
        assertEq(factory.tokenBySymbol(s), token);
        assertTrue(token != address(0));
    }
}
