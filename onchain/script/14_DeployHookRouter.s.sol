// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/console.sol";
import {SincHookRouter} from "../src/SincHookRouter.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {SignerScript} from "./SignerScript.sol";

/// @notice Deploy SincHookRouter for direct USDC→SINC buys through the hook pool.
contract DeployHookRouter is SignerScript {
    address constant USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    address constant SINC = 0x9C8cd8d3961F445D653713dE65C6578bE11668e7;
    address constant HOOK = 0x8e0eE51dCa5249c9e84dbec539fDD46b375110C0;

    function run() external returns (address router) {
        uint256 deployerKey = _loadSignerKey();
        address poolManager = vm.envAddress("POOL_MANAGER");

        vm.startBroadcast(deployerKey);
        router = address(new SincHookRouter(IPoolManager(poolManager), USDC, SINC, HOOK));
        vm.stopBroadcast();

        console.log("SincHookRouter deployed at:", router);

        string memory chain = vm.toString(block.chainid);
        string memory path = string.concat("deployments/", chain, ".json");
        vm.writeJson(vm.toString(router), path, ".hookRouter");
    }
}