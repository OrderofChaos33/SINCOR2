// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script} from "forge-std/Script.sol";
import {LiquidityAmplifierHook} from "../../src/LiquidityAmplifierHook.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";

// NOTE: broken as committed (console not imported; env var names differ from Deploy.s.sol;
// BaseHook now requires CREATE2-mined address). Preserved for reference — use Deploy.s.sol.
contract DeployLiquidityAmplifierHook is Script {
    function run() external {
        uint256 deployerPrivateKey = vm.envUint("PRIVATE_KEY");
        vm.startBroadcast(deployerPrivateKey);

        address guardian = vm.envAddress("GUARDIAN_ADDRESS");
        IPoolManager poolManager = IPoolManager(vm.envAddress("POOL_MANAGER_ADDRESS"));

        LiquidityAmplifierHook hook = new LiquidityAmplifierHook(poolManager, guardian);

        vm.stopBroadcast();
    }
}
