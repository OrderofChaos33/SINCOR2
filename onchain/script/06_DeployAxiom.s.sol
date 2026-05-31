// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Script.sol";
import {Axiom} from "../src/Axiom.sol";

/**
 * @notice Deploy the AXIOM (AXM) token.
 *
 * Usage (Base Sepolia):
 *   forge script script/06_DeployAxiom.s.sol \
 *     --rpc-url $BASE_SEPOLIA_RPC_URL --broadcast --verify
 *
 * Usage (Base mainnet):
 *   forge script script/06_DeployAxiom.s.sol \
 *     --rpc-url $BASE_RPC_URL --broadcast --verify
 *
 * The deployer's address receives the entire 1B AXM supply.
 * Transfer to the treasury wallet immediately after deployment.
 *
 * Already deployed on Base mainnet:
 *   0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822
 */
contract DeployAxiom is Script {
    function run() external returns (address) {
        uint256 deployerKey = vm.envUint("DEPLOYER_PRIVATE_KEY");
        address deployer    = vm.addr(deployerKey);
        // Override TREASURY_ADDRESS in .env if you want supply sent directly to treasury.
        address treasury    = vm.envOr("TREASURY_ADDRESS", deployer);

        vm.startBroadcast(deployerKey);
        Axiom axiom = new Axiom(treasury);
        vm.stopBroadcast();

        console.log("Axiom (AXM) deployed at:", address(axiom));
        console.log("Total supply held by:   ", treasury);
        return address(axiom);
    }
}
