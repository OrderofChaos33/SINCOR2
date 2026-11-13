// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Script.sol";
import {ContentPolicyGuard} from "../src/p24/ContentPolicyGuard.sol";
import {CreatorTokenFactory} from "../src/p24/CreatorTokenFactory.sol";
import {FeeSplitDistributor} from "../src/p24/FeeSplitDistributor.sol";
import {RevenueAccrual} from "../src/p24/RevenueAccrual.sol";

/// @title Simulate P24 creator-issuance stack (local only)
/// @notice Simulates construction of the four P24 issuance contracts in dependency order:
///         ContentPolicyGuard -> CreatorTokenFactory -> FeeSplitDistributor -> RevenueAccrual.
///         BondingCurve is not constructed here: one curve is constructed per issued
///         token at `issue()` time (constructor takes the token address).
///
///         Issuance authority flows through existing constructor args + roles:
///         - CreatorTokenFactory.issue() is admin-only, requires `screened=true`
///           (the offchain content-policy gate), and enforces unique symbols.
///           The ContentPolicyGuard links OFFCHAIN: the Python bridge /
///           onboarding agent runs the policy screen (RULESET_VERSION recorded
///           on the token) and passes screened=true. No .sol changes needed.
///         - ContentPolicyGuard.screener must be a DIFFERENT key from admin.
///         - FeeSplitDistributor.TREASURY() is a compiled constant; the script
///           aborts if it drifts from the expected address (forces review).
///
/// @dev Read-only simulation inputs (NO private keys or broadcast hooks):
///         P24_ADMIN             - nonzero address used as factory/guard admin input in local simulation
///         P24_SCREENER          - nonzero address used as guard screener input; MUST differ from P24_ADMIN
///         P24_TREASURY          - optional override, defaults to 0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac
///
///         Usage (local simulation only):
///           P24_ADMIN=0x... P24_SCREENER=0x... forge script script/07_DeployP24.s.sol
///         This script intentionally contains no signer or network-write hook.
contract DeployP24 is Script {
    address internal constant DEFAULT_TREASURY = 0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac;

    function run()
        external
        returns (
            address guardAddr,
            address factoryAddr,
            address splitterAddr,
            address accrualAddr
        )
    {
        address admin = vm.envAddress("P24_ADMIN");
        address screener = vm.envAddress("P24_SCREENER");
        require(admin != address(0), "P24_ADMIN unset");
        require(screener != address(0), "P24_SCREENER unset");
        require(admin != screener, "P24_ADMIN and P24_SCREENER must be distinct keys");

        // FeeSplitDistributor.TREASURY is a compiled constant, not a constructor
        // arg. Read it back off the simulated instance and abort on drift so a
        // constant change forces an explicit review (never a silent drift).
        address expectedTreasury = vm.envOr("P24_TREASURY", DEFAULT_TREASURY);

        // Owner decision D4 keeps onchain anchoring OFF for launch, and WP0
        // defines no canonical deployment authorization contract. This path is
        // local simulation only and deliberately emits no durable artifact.

        ContentPolicyGuard guard = new ContentPolicyGuard(admin, screener);
        CreatorTokenFactory factory = new CreatorTokenFactory(admin);
        FeeSplitDistributor splitter = new FeeSplitDistributor();
        RevenueAccrual accrual = new RevenueAccrual();

        require(splitter.TREASURY() == expectedTreasury, "TREASURY constant drifted from expected");

        guardAddr = address(guard);
        factoryAddr = address(factory);
        splitterAddr = address(splitter);
        accrualAddr = address(accrual);

        console.log("SIMULATION ONLY: ContentPolicyGuard ephemeral address; NOT ON-CHAIN:", guardAddr);
        console.log("SIMULATION ONLY: CreatorTokenFactory ephemeral address; NOT ON-CHAIN:", factoryAddr);
        console.log("SIMULATION ONLY: FeeSplitDistributor ephemeral address; NOT ON-CHAIN:", splitterAddr);
        console.log("SIMULATION ONLY: RevenueAccrual ephemeral address; NOT ON-CHAIN:", accrualAddr);
    }
}
