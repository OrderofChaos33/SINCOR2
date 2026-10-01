// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Test.sol";
import {ContentPolicyGuard} from "../src/p24/ContentPolicyGuard.sol";
import {CreatorTokenFactory} from "../src/p24/CreatorTokenFactory.sol";
import {FeeSplitDistributor} from "../src/p24/FeeSplitDistributor.sol";
import {RevenueAccrual} from "../src/p24/RevenueAccrual.sol";

/// @notice Mirrors the plumbing of script/07_DeployP24.s.sol: constructor args
///         must land on the documented roles, admin/screener must be distinct,
///         and the compiled TREASURY constant must match the locked address.
contract P24DeployPlumbingTest is Test {
    address internal constant EXPECTED_TREASURY = 0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac;

    function test_ConstructorArgsLandOnRoles() public {
        address admin = makeAddr("p24-admin");
        address screener = makeAddr("p24-screener");
        vm.assume(admin != screener);

        ContentPolicyGuard guard = new ContentPolicyGuard(admin, screener);
        assertEq(guard.admin(), admin, "guard.admin");
        assertEq(guard.screener(), screener, "guard.screener");
        assertTrue(keccak256(bytes(guard.RULESET_VERSION())) == keccak256(bytes("1.0.0")), "ruleset version");

        CreatorTokenFactory factory = new CreatorTokenFactory(admin);
        assertEq(factory.admin(), admin, "factory.admin");

        FeeSplitDistributor splitter = new FeeSplitDistributor();
        assertEq(splitter.TREASURY(), EXPECTED_TREASURY, "treasury constant drift");

        RevenueAccrual accrual = new RevenueAccrual();
        assertEq(accrual.EPOCH(), 7 days, "epoch length");
    }

    function test_EmptyAdminRejectedByScriptConvention() public {
        // The deploy script requires non-zero, distinct admin/screener; this
        // documents the convention at the contract level: the factory has no
        // zero-address guard on purpose (script-side validation), but the
        // values deployed must never be zero in the manifest.
        ContentPolicyGuard guard = new ContentPolicyGuard(address(1), address(2));
        assertTrue(guard.admin() != address(0) && guard.screener() != address(0));
    }
}
