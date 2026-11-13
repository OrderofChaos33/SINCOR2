// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console} from "forge-std/Script.sol";
import {SincorSettlementHub} from "../contracts/modules/SincorSettlementHub.sol";

/// @notice Deploys SincorSettlementHub(usdc, feeTreasury). NO keys in this
///         script: it runs keyless in simulation (plain `forge script`), and
///         broadcasts only when the caller passes --broadcast with their own
///         key (e.g. --private-key $DEPLOYER_PRIVATE_KEY).
///
/// Env:
///   HUB_USDC            USDC token address. Defaults: Base mainnet
///                       0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913 (8453),
///                       Base Sepolia 0x036CbD53842c5426634e7929541eC2318f3dCF7e
///                       (84532). On 8453 any other value REVERTS.
///   HUB_TREASURY        Fee treasury. Default on both chains:
///                       0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac.
///   HUB_WRITE_MANIFEST  "true" only on a real broadcast run: writes the
///                       deployment manifest to
///                       onchain/deployments/base-sepolia-settlement-hub.json
///                       (Base Sepolia runs; the committed file is a template).
contract DeploySettlementHub is Script {
    address internal constant BASE_USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    address internal constant BASE_SEPOLIA_USDC = 0x036CbD53842c5426634e7929541eC2318f3dCF7e;
    address internal constant TREASURY = 0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac;

    function run() external returns (SincorSettlementHub hub) {
        address usdc = vm.envOr("HUB_USDC", address(0));
        address treasury = vm.envOr("HUB_TREASURY", TREASURY);

        if (block.chainid == 8453) {
            // Canon guard: on Base mainnet the Hub MUST point at real USDC.
            require(usdc == address(0) || usdc == BASE_USDC, "HUB_USDC must be Base mainnet USDC");
            if (usdc == address(0)) usdc = BASE_USDC;
        } else if (block.chainid == 84532) {
            if (usdc == address(0)) usdc = BASE_SEPOLIA_USDC;
        }
        require(usdc != address(0), "HUB_USDC unset and no default for this chain");
        require(treasury != address(0), "HUB_TREASURY zero");

        vm.startBroadcast();
        hub = new SincorSettlementHub(usdc, treasury);
        vm.stopBroadcast();

        console.log("SincorSettlementHub deployed (or simulated) at:", address(hub));
        console.log("chainId:", block.chainid);
        console.log("usdc:", usdc);
        console.log("feeTreasury:", treasury);
        console.log("protocolFeeBps:", hub.protocolFeeBps());

        if (vm.envOr("HUB_WRITE_MANIFEST", false)) {
            _writeManifest(hub, usdc, treasury);
        }
    }

    function _writeManifest(SincorSettlementHub hub, address usdc, address treasury) internal {
        // Same key shape as onchain/deployments/base-8453.json.
        string memory json = string.concat(
            "{\n",
            '  "network": "',
            block.chainid == 84532 ? "base-sepolia" : "base",
            '",\n',
            '  "chainId": ',
            vm.toString(block.chainid),
            ",\n",
            '  "deployedAt": "',
            vm.toString(block.timestamp),
            '",\n',
            '  "deployer": "',
            vm.toString(msg.sender),
            '",\n',
            '  "guardian": null,\n',
            '  "treasury": "',
            vm.toString(treasury),
            '",\n',
            '  "contracts": {\n    "SincorSettlementHub": {\n      "address": "',
            vm.toString(address(hub)),
            '",\n      "constructorArgs": "',
            vm.toString(abi.encode(usdc, treasury)),
            '",\n      "usdc": "',
            vm.toString(usdc),
            '",\n      "protocolFeeBps": 500\n    }\n  },\n',
            '  "deployRun": "script/DeploySettlementHub.s.sol",\n',
            '  "onchainVerification": "pending"\n',
            "}\n"
        );
        vm.writeFile("onchain/deployments/base-sepolia-settlement-hub.json", json);
        console.log("Manifest written to onchain/deployments/base-sepolia-settlement-hub.json");
    }
}
