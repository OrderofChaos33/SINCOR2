// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Script.sol";
import {SincTreasuryLadderHook} from "../src/SincTreasuryLadderHook.sol";
import {Hooks} from "@uniswap/v4-core/src/libraries/Hooks.sol";

/// @notice Mine CREATE2 salt for SincTreasuryLadderHook V2 (mask 0x10C0).
contract MineTreasuryHookV2 is Script {
    function run() external view returns (address predicted, bytes32 salt) {
        address deployer = vm.envAddress("DEPLOYER_ADDRESS");
        address poolManager = vm.envAddress("POOL_MANAGER");
        address treasury = vm.envAddress("TREASURY");

        uint160 required = uint160(
            Hooks.AFTER_INITIALIZE_FLAG | Hooks.BEFORE_SWAP_FLAG | Hooks.AFTER_SWAP_FLAG
        );

        bytes memory creationCode =
            abi.encodePacked(type(SincTreasuryLadderHook).creationCode, abi.encode(poolManager, treasury));
        bytes32 codeHash = keccak256(creationCode);

        for (uint256 i = 0; i < 10_000_000; i++) {
            salt = bytes32(i);
            predicted = address(
                uint160(
                    uint256(keccak256(abi.encodePacked(bytes1(0xff), deployer, salt, codeHash)))
                )
            );
            if (uint160(predicted) & 0x3FFF == required) {
                console.log("Found salt at iteration", i);
                console.log("Predicted V2 hook:", predicted);
                console.logBytes32(salt);
                return (predicted, salt);
            }
        }
        revert("No salt found in 10M iterations");
    }
}