// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Script.sol";
import {TWAMMIHook} from "../src/TWAMMIHook.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";

/**
 * @title DeployTWAMMIHook
 * @notice Deploy TWAMMIHook to Base Sepolia (or any chain) via CREATE2 for
 *         deterministic address mining compatible with Uniswap v4 hook bit flags.
 *
 * Required env vars:
 *   DEPLOYER_PRIVATE_KEY   — deployer EOA private key
 *   POOL_MANAGER           — Uniswap v4 PoolManager address
 *   TREASURY_ADDRESS       — fee treasury (default: 0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac)
 *   TWAMMI_FEE_BPS         — protocol fee in bps (default: 10)
 *   TWAMMI_SALT            — CREATE2 salt (bytes32); mine with 04_MineHookAddress.s.sol
 *
 * Base Sepolia PoolManager: https://docs.uniswap.org/contracts/v4/deployments
 *
 * Run:
 *   forge script script/07_DeployTWAMMIHook.s.sol \
 *     --rpc-url $BASE_SEPOLIA_RPC_URL \
 *     --broadcast \
 *     --verify \
 *     --etherscan-api-key $BASESCAN_API_KEY
 *
 * After deploy, verify fee routing:
 *   1. Call hook.executeTWAMM(key) with funded orders.
 *   2. Check TREASURY_ADDRESS balance increased by protocolFee.
 *   3. Check hook.cumulativeFees(token) > 0.
 */
contract DeployTWAMMIHook is Script {
    address public constant DEFAULT_TREASURY = 0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac;

    function run() external returns (address hookAddr) {
        uint256 deployerKey = vm.envUint("DEPLOYER_PRIVATE_KEY");

        address poolManager = vm.envAddress("POOL_MANAGER");
        address treasury = vm.envOr("TREASURY_ADDRESS", DEFAULT_TREASURY);
        uint256 feeBps = vm.envOr("TWAMMI_FEE_BPS", uint256(10));
        bytes32 salt = vm.envOr("TWAMMI_SALT", bytes32(0));

        require(treasury != address(0), "TREASURY_ADDRESS required");
        require(feeBps <= 100, "fee too high");

        vm.startBroadcast(deployerKey);

        bytes memory creationCode = abi.encodePacked(
            type(TWAMMIHook).creationCode,
            abi.encode(IPoolManager(poolManager), treasury, feeBps)
        );

        if (salt != bytes32(0)) {
            // Deterministic CREATE2 for hook address mining
            assembly {
                hookAddr := create2(0, add(creationCode, 0x20), mload(creationCode), salt)
            }
            require(hookAddr != address(0), "CREATE2 failed — try a different salt");
        } else {
            // Standard CREATE (no salt needed for non-hook-bit-flagged deploy)
            TWAMMIHook hook = new TWAMMIHook(IPoolManager(poolManager), treasury, feeBps);
            hookAddr = address(hook);
        }

        vm.stopBroadcast();

        console.log("=== TWAMMIHook deployed ===");
        console.log("Address   :", hookAddr);
        console.log("PoolManager:", poolManager);
        console.log("Treasury  :", treasury);
        console.log("Fee (bps) :", feeBps);
        console.log("Chain     :", block.chainid);

        // Verify fee routing in place
        console.log("\n--- Fee routing verification ---");
        console.log("Treasury at deploy:", treasury);
        console.log("Run hook.executeTWAMM(key) with funded orders to confirm fee flow.");
        console.log("Check hook.cumulativeFees(token) > 0 post-execution.");

        // Persist deployment record
        string memory chain = vm.toString(block.chainid);
        string memory path = string.concat("deployments/", chain, ".json");
        try vm.writeJson(vm.toString(hookAddr), path, ".twammi_hook") {} catch {}
        try vm.writeJson(vm.toString(treasury), path, ".twammi_treasury") {} catch {}
    }
}
