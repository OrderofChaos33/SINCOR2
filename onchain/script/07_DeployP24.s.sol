// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Script.sol";
import {ContentPolicyGuard} from "../src/p24/ContentPolicyGuard.sol";
import {CreatorTokenFactory} from "../src/p24/CreatorTokenFactory.sol";
import {FeeSplitDistributor} from "../src/p24/FeeSplitDistributor.sol";
import {RevenueAccrual} from "../src/p24/RevenueAccrual.sol";

/// @title Deploy P24 creator-issuance stack (Base Sepolia ceremony)
/// @notice Deploys the four P24 issuance contracts in dependency order:
///         ContentPolicyGuard -> CreatorTokenFactory -> FeeSplitDistributor -> RevenueAccrual.
///         BondingCurve is NOT deployed here: one curve is deployed per issued
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
/// @dev Env vars (NO private keys in the repo; all ceremony inputs via env):
///         P24_ADMIN             - factory admin + guard admin (founder-designated; Secure Vault/multisig per audit)
///         P24_SCREENER          - guard screener key, MUST differ from P24_ADMIN
///         P24_TREASURY          - optional override, defaults to 0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac
///         DEPLOYER_PRIVATE_KEY  - optional for inspection/dry-run; REQUIRED at ceremony with --broadcast
///
///         Usage (inspection only, no key, no broadcast):
///           P24_ADMIN=0x... P24_SCREENER=0x... forge script script/07_DeployP24.s.sol --dry-run
///         Ceremony (founder):
///           export P24_ADMIN=<...> P24_SCREENER=<...> DEPLOYER_PRIVATE_KEY=<one-shot>
///           forge script script/07_DeployP24.s.sol --rpc-url https://sepolia.base.org --broadcast --verify
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
        // arg. Read it back off the deployed instance and abort on drift so a
        // constant change forces an explicit review (never a silent drift).
        address expectedTreasury = vm.envOr("P24_TREASURY", DEFAULT_TREASURY);

        // No key => dry-run/inspection mode. Broadcasts are simulated only;
        // nothing leaves the machine. Ceremony sets DEPLOYER_PRIVATE_KEY + --broadcast.
        uint256 deployerKey = vm.envOr("DEPLOYER_PRIVATE_KEY", uint256(0));
        if (deployerKey != uint256(0)) {
            vm.startBroadcast(deployerKey);
        } else {
            console.log("NOTE: DEPLOYER_PRIVATE_KEY unset -> dry-run/inspection mode (no key required)");
            vm.startBroadcast();
        }

        ContentPolicyGuard guard = new ContentPolicyGuard(admin, screener);
        CreatorTokenFactory factory = new CreatorTokenFactory(admin);
        FeeSplitDistributor splitter = new FeeSplitDistributor();
        RevenueAccrual accrual = new RevenueAccrual();

        vm.stopBroadcast();

        require(splitter.TREASURY() == expectedTreasury, "TREASURY constant drifted from expected");

        guardAddr = address(guard);
        factoryAddr = address(factory);
        splitterAddr = address(splitter);
        accrualAddr = address(accrual);

        console.log("ContentPolicyGuard deployed at:", guardAddr);
        console.log("CreatorTokenFactory deployed at:", factoryAddr);
        console.log("FeeSplitDistributor deployed at:", splitterAddr);
        console.log("RevenueAccrual deployed at:", accrualAddr);

        _writeManifest(admin, screener, splitter.TREASURY(), guardAddr, factoryAddr, splitterAddr, accrualAddr);
    }

    function _writeManifest(
        address admin,
        address screener,
        address treasury,
        address guardAddr,
        address factoryAddr,
        address splitterAddr,
        address accrualAddr
    ) internal {
        string memory key = "p24manifest";
        string memory json = vm.serializeString(key, "network", "base-sepolia");
        json = vm.serializeUint(key, "chainId", block.chainid);
        json = vm.serializeUint(key, "deployedAt", block.timestamp);
        json = vm.serializeAddress(key, "deployer", msg.sender);
        json = vm.serializeString(key, "script", "script/07_DeployP24.s.sol");

        // constructor inputs (addresses filled at ceremony)
        json = vm.serializeAddress(key, "constructorInputs.P24_ADMIN", admin);
        json = vm.serializeAddress(key, "constructorInputs.P24_SCREENER", screener);
        json = vm.serializeAddress(key, "constructorInputs.P24_TREASURY", treasury);

        json = vm.serializeAddress(key, "contracts.ContentPolicyGuard.address", guardAddr);
        json = vm.serializeString(key, "contracts.ContentPolicyGuard.constructorArgs", "(P24_ADMIN, P24_SCREENER)");

        json = vm.serializeAddress(key, "contracts.CreatorTokenFactory.address", factoryAddr);
        json = vm.serializeString(key, "contracts.CreatorTokenFactory.constructorArgs", "(P24_ADMIN)");

        json = vm.serializeAddress(key, "contracts.FeeSplitDistributor.address", splitterAddr);
        json = vm.serializeString(key, "contracts.FeeSplitDistributor.constructorArgs", "()  // TREASURY is a compiled constant");

        json = vm.serializeAddress(key, "contracts.RevenueAccrual.address", accrualAddr);
        json = vm.serializeString(key, "contracts.RevenueAccrual.constructorArgs", "()");

        json = vm.serializeString(key, "verification.Sourcify", "pending");
        json = vm.serializeString(key, "verification.Etherscan", "pending");

        json = vm.serializeString(
            key,
            "notes",
            "BondingCurve is per-token (constructor takes token address), deployed at issue() time. Guard links offchain via screened=true."
        );

        vm.writeJson(json, "deployments/base-sepolia-p24.json");
        console.log("Manifest written to: deployments/base-sepolia-p24.json");
    }
}
