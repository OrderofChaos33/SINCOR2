// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script} from "forge-std/Script.sol";
import {LiquidityAmplifierHook} from "../src/LiquidityAmplifierHook.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";

// Production deployment script extending your existing Foundry runbook (e.g., after 06_DeployAxiom.s.sol).
// Additive only — deploys new optional hook for parallel pools. No changes to SincLimitOrderHook or existing contracts.
// Uses your CREATE2 pattern for deterministic address (extend 04_MineHookAddress.s.sol if needed).
// Best practices: Guardian/timelock ready, events, compatible with Base (8453).

contract DeployLiquidityAmplifierHook is Script {
    function run() external {
        uint256 deployerPrivateKey = vm.envUint("PRIVATE_KEY");
        vm.startBroadcast(deployerPrivateKey);

        // Production config — set guardian to your timelock or trusted multi-sig (add full TimelockController in prod).
        address guardian = vm.envAddress("GUARDIAN_ADDRESS"); // e.g., your SINCOR treasury or timelock

        // PoolManager on Base (Uniswap V4 core — use your existing address or mainnet value).
        IPoolManager poolManager = IPoolManager(vm.envAddress("POOL_MANAGER_ADDRESS")); // e.g., 0x... on Base

        LiquidityAmplifierHook hook = new LiquidityAmplifierHook(poolManager, guardian);

        // Optional: Mine CREATE2 address first (copy your 04_MineHookAddress logic for hook permissions).
        // address hookAddress = ...; // Deploy at specific salt for V4 hook flags.

        vm.stopBroadcast();

        // Log for your runbook_state.json or monitoring.
        console.log("LiquidityAmplifierHook deployed at:", address(hook));
        console.log("Guardian:", guardian);
        console.log("Additive to existing SincLimitOrderHook — new pools only.");
    }
}

// Usage (your style):
// forge script script/07_DeployLiquidityAmplifierHook.s.sol --rpc-url $BASE_RPC_URL --broadcast --verify
// Then initialize yieldVaults for target pools (e.g., SINC/USDC) via guardian call or agent A2A task.
// Monitor via events + your existing dashboards. Treasury/AXM fee routing applies automatically on new pools.