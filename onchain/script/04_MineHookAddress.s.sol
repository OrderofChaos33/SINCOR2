// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Script.sol";
import {SincLimitOrderHook} from "../src/SincLimitOrderHook.sol";
import {Hooks} from "@uniswap/v4-core/src/libraries/Hooks.sol";

/// @notice Mines a CREATE2 salt so that the deployed SincLimitOrderHook address
///         has the correct permission flag bits set in its low 14 bits.
///
///         Permission analysis:
///           OZ LimitOrderHook.getHookPermissions() enables:
///             - afterInitialize  (1 << 12 = 0x1000)
///             - afterSwap        (1 << 6  = 0x0040)
///           SincLimitOrderHook.getHookPermissions() additionally enables:
///             - beforeSwap       (1 << 7  = 0x0080)
///
///           required mask = 0x1000 | 0x0080 | 0x0040 = 0x10C0
///
///         All other 14 hook-flag bits must be 0 to keep validateHookPermissions happy.
///         We therefore check:  (uint160(predicted) & 0x3FFF) == required
contract MineHookAddress is Script {
    function run() external view returns (address, bytes32) {
        address deployer = vm.envAddress("DEPLOYER_ADDRESS");
        address poolManager = vm.envAddress("POOL_MANAGER");

        // Required permission bits (verified against LimitOrderHook source):
        //   afterInitialize (1 << 12) | beforeSwap (1 << 7) | afterSwap (1 << 6)
        uint160 required = uint160(
            Hooks.AFTER_INITIALIZE_FLAG
                | Hooks.BEFORE_SWAP_FLAG
                | Hooks.AFTER_SWAP_FLAG
        );
        // required == 0x10C0

        bytes memory creationCode = abi.encodePacked(
            type(SincLimitOrderHook).creationCode,
            abi.encode(poolManager)
        );
        bytes32 codeHash = keccak256(creationCode);

        for (uint256 i = 0; i < 10_000_000; i++) {
            bytes32 salt = bytes32(i);
            address predicted = address(
                uint160(
                    uint256(
                        keccak256(
                            abi.encodePacked(
                                bytes1(0xff),
                                deployer,
                                salt,
                                codeHash
                            )
                        )
                    )
                )
            );
            // All 14 hook bits must match exactly — no extra stray bits.
            if (uint160(predicted) & 0x3FFF == required) {
                console.log("Found salt at iteration", i);
                console.log("Predicted address", predicted);
                console.logBytes32(salt);
                return (predicted, salt);
            }
        }
        revert("No salt found in 10M iterations");
    }
}
