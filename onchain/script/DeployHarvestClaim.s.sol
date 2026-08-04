// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Script.sol";
import {HarvestClaim} from "../src/HarvestClaim.sol";

/**
 * @title  DeployHarvestClaim
 * @notice Deploys HarvestClaim to Base mainnet (or Sepolia for testing).
 *
 * Required environment variables:
 *   DEPLOYER_PRIVATE_KEY   — deployer wallet private key (never commit)
 *   SINC_TOKEN_ADDRESS     — Base SINC token: 0x9C8cd8d3961F445D653713dE65C6578bE11668e7
 *   TREASURY_ADDRESS       — treasury multi-sig: 0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac
 *   HARVEST_OWNER_ADDRESS  — initial owner (deployer or multi-sig); transfer immediately after
 *
 * Optional:
 *   CREATE2_SALT           — hex salt for CREATE2 (deterministic address); omit for regular deploy
 *
 * Usage (Base Sepolia — staging):
 *   forge script script/DeployHarvestClaim.s.sol \
 *     --rpc-url $BASE_SEPOLIA_RPC_URL --broadcast --verify
 *
 * Usage (Base mainnet — production):
 *   forge script script/DeployHarvestClaim.s.sol \
 *     --rpc-url $BASE_RPC_URL --broadcast --verify
 *
 * Post-deploy checklist:
 *   1. Transfer SINC allocation from treasury to HarvestClaim address
 *   2. Call setRoot(merkleRoot, 30, allocation) from owner
 *   3. Transfer ownership to treasury multi-sig (or renounce after root set)
 *   4. Verify on Basescan
 *   5. Record deployed address in onchain/deployments/base-8453.json
 */
contract DeployHarvestClaim is Script {
    function run() external returns (address contractAddress) {
        uint256 deployerKey = vm.envUint("DEPLOYER_PRIVATE_KEY");
        address deployer    = vm.addr(deployerKey);

        address sinc     = vm.envAddress("SINC_TOKEN_ADDRESS");
        address treasury = vm.envAddress("TREASURY_ADDRESS");
        address initialOwner = vm.envOr("HARVEST_OWNER_ADDRESS", deployer);

        // Optional CREATE2 salt for deterministic address
        bytes32 salt = vm.envOr("CREATE2_SALT", bytes32(0));

        console.log("=== DeployHarvestClaim ===");
        console.log("Deployer:     ", deployer);
        console.log("SINC token:   ", sinc);
        console.log("Treasury:     ", treasury);
        console.log("Initial owner:", initialOwner);
        if (salt != bytes32(0)) {
            console.log("CREATE2 salt: ", vm.toString(salt));
        }

        vm.startBroadcast(deployerKey);

        HarvestClaim harvest;
        if (salt != bytes32(0)) {
            harvest = new HarvestClaim{salt: salt}(sinc, treasury, initialOwner);
        } else {
            harvest = new HarvestClaim(sinc, treasury, initialOwner);
        }

        vm.stopBroadcast();

        contractAddress = address(harvest);
        console.log("HarvestClaim deployed at:", contractAddress);
        console.log("");
        console.log("Next steps:");
        console.log("  1. Transfer SINC allocation to:", contractAddress);
        console.log("  2. Call setRoot(root, 30, allocation) from:", initialOwner);
        console.log("  3. Transfer ownership to treasury multi-sig:", treasury);
        console.log("  4. Verify: forge verify-contract", contractAddress, "src/HarvestClaim.sol:HarvestClaim");
    }
}
