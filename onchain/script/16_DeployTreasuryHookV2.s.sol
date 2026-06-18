// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Script.sol";
import {SincTreasuryLadderHook} from "../src/SincTreasuryLadderHook.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";

/// @notice CREATE2 deploy SincTreasuryLadderHook V2. Requires HOOK_SALT from MineTreasuryHookV2.
contract DeployTreasuryHookV2 is Script {
    function run() external returns (address hook) {
        uint256 deployerKey = vm.envUint("DEPLOYER_PRIVATE_KEY");
        bytes32 salt = vm.envBytes32("HOOK_SALT");
        address poolManager = vm.envAddress("POOL_MANAGER");
        address treasury = vm.envAddress("TREASURY");

        vm.startBroadcast(deployerKey);

        bytes memory creationCode =
            abi.encodePacked(type(SincTreasuryLadderHook).creationCode, abi.encode(poolManager, treasury));
        assembly {
            hook := create2(0, add(creationCode, 0x20), mload(creationCode), salt)
        }
        require(hook != address(0), "CREATE2 failed");

        vm.stopBroadcast();

        console.log("SincTreasuryLadderHook V2 at:", hook);

        string memory chain = vm.toString(block.chainid);
        string memory path = string.concat("deployments/", chain, ".json");
        vm.writeJson(vm.toString(hook), path, ".hookV2");
    }
}