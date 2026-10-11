// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Test.sol";
import {ContentPolicyGuard} from "../../src/p24/ContentPolicyGuard.sol";
import {CreatorTokenFactory} from "../../src/p24/CreatorTokenFactory.sol";
import {FeeSplitDistributor} from "../../src/p24/FeeSplitDistributor.sol";
import {RevenueAccrual} from "../../src/p24/RevenueAccrual.sol";
import {DeployP24} from "../../script/07_DeployP24.s.sol";

/// @notice Validates P24 constructor wiring and runs the deploy script only in
///         Foundry's in-memory test EVM. This is not deployment/broadcast evidence.
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

    function test_SimulationConstructorValuesAreNonzero() public {
        // These nonzero constructor arguments exist only in this local Foundry
        // simulation. This test creates no live deployment or manifest record.
        ContentPolicyGuard guard = new ContentPolicyGuard(address(1), address(2));
        assertTrue(guard.admin() != address(0) && guard.screener() != address(0));
    }

    function test_DeployScriptRunCreatesOnlyLocalTestContracts() public {
        address admin = makeAddr("p24-script-admin");
        address screener = makeAddr("p24-script-screener");
        vm.setEnv("P24_ADMIN", vm.toString(admin));
        vm.setEnv("P24_SCREENER", vm.toString(screener));
        vm.setEnv("P24_TREASURY", vm.toString(EXPECTED_TREASURY));

        DeployP24 script = new DeployP24();
        (address guardAddr, address factoryAddr, address splitterAddr, address accrualAddr) = script.run();

        assertGt(guardAddr.code.length, 0, "guard exists only in the local test EVM");
        assertGt(factoryAddr.code.length, 0, "factory exists only in the local test EVM");
        assertGt(splitterAddr.code.length, 0, "splitter exists only in the local test EVM");
        assertGt(accrualAddr.code.length, 0, "accrual exists only in the local test EVM");
        assertEq(ContentPolicyGuard(guardAddr).admin(), admin, "script guard admin");
        assertEq(ContentPolicyGuard(guardAddr).screener(), screener, "script screener");
        assertEq(CreatorTokenFactory(factoryAddr).admin(), admin, "script factory admin");
        assertEq(FeeSplitDistributor(splitterAddr).TREASURY(), EXPECTED_TREASURY, "script treasury");
    }
}
