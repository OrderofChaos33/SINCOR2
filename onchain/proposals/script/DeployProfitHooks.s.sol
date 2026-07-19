// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import "forge-std/Script.sol";
import {PhantomCreditToken} from "../hooks/PhantomCreditToken.sol";
import {MoebiusMEVHook} from "../hooks/MoebiusMEVHook.sol";
import {LiquidityAmplifierHook} from "../../src/LiquidityAmplifierHook.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";

// NOTE: broken as committed (literal newline inside string literal; imports proposal hooks).
// Preserved for reference — Moebius v2 deploy path ships with PR #81.
contract DeployProfitHooks is Script {
    function run() external {
        uint256 deployerKey = vm.envUint("DEPLOYER_PRIVATE_KEY");
        address poolManager = vm.envAddress("POOL_MANAGER_ADDRESS");
        address guardian = vm.envAddress("GUARDIAN_ADDRESS");
        address treasury = vm.envAddress("TREASURY_ADDRESS");

        vm.startBroadcast(deployerKey);

        PhantomCreditToken pToken = new PhantomCreditToken(address(0));
        MoebiusMEVHook moebius = new MoebiusMEVHook(
            IPoolManager(poolManager),
            address(pToken),
            treasury
        );
        LiquidityAmplifierHook amplifier = new LiquidityAmplifierHook(
            IPoolManager(poolManager),
            guardian
        );

        vm.stopBroadcast();
    }
}
