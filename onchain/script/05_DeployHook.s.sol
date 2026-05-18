// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Script.sol";
import {SincLimitOrderHook} from "../src/SincLimitOrderHook.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";

contract DeployHook is Script {
    function run() external returns (address hook) {
        uint256 deployerKey = vm.envUint("DEPLOYER_PRIVATE_KEY");
        bytes32 salt = vm.envBytes32("HOOK_SALT");
        address poolManager = vm.envAddress("POOL_MANAGER");
        address curve = vm.envAddress("CURVE");

        vm.startBroadcast(deployerKey);

        bytes memory creationCode = abi.encodePacked(
            type(SincLimitOrderHook).creationCode,
            abi.encode(poolManager)
        );
        assembly {
            hook := create2(0, add(creationCode, 0x20), mload(creationCode), salt)
        }
        require(hook != address(0), "CREATE2 failed");

        (bool ok,) = curve.call(abi.encodeWithSignature("setHook(address)", hook));
        require(ok, "setHook failed");

        vm.stopBroadcast();

        console.log("SincLimitOrderHook deployed at:", hook);
        console.log("Hook wired into curve");

        string memory chain = vm.toString(block.chainid);
        string memory path = string.concat("deployments/", chain, ".json");
        vm.writeJson(vm.toString(hook), path, ".hook");
    }
}
