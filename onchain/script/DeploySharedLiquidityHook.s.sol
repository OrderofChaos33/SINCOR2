// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {Hooks} from "@uniswap/v4-core/src/libraries/Hooks.sol";

import {SharedLiquidityHook} from "../src/SharedLiquidityHook.sol";
import {ISharedLiquidityVault} from "../src/interfaces/ISharedLiquidityVault.sol";

/// @notice Deploys SharedLiquidityHook on a CREATE2-mined address with V4 hook flags:
///         BEFORE_SWAP + AFTER_SWAP (0xC0).
contract DeploySharedLiquidityHook is Script {
    uint160 internal constant HOOK_MASK = 0x3FFF;

    function run() external returns (address hookAddress, bytes32 salt) {
        uint256 deployerPrivateKey = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(deployerPrivateKey);

        IPoolManager poolManager = IPoolManager(vm.envAddress("POOL_MANAGER"));
        ISharedLiquidityVault vault = ISharedLiquidityVault(vm.envAddress("VAULT"));
        address treasury = vm.envAddress("TREASURY");
        uint256 feeBps = vm.envOr("HOOK_PROTOCOL_FEE_BPS", uint256(10));
        uint256 maxSaltIterations = vm.envOr("HOOK_SALT_MAX_ITERATIONS", uint256(2_500_000));

        uint160 required = uint160(Hooks.BEFORE_SWAP_FLAG | Hooks.AFTER_SWAP_FLAG);
        bytes memory creationCode = abi.encodePacked(
            type(SharedLiquidityHook).creationCode, abi.encode(poolManager, vault, treasury, feeBps)
        );
        bytes32 codeHash = keccak256(creationCode);

        bool found;
        for (uint256 i = 0; i < maxSaltIterations; i++) {
            salt = bytes32(i);
            address predicted = address(
                uint160(uint256(keccak256(abi.encodePacked(bytes1(0xff), deployer, salt, codeHash))))
            );
            if ((uint160(predicted) & HOOK_MASK) == required) {
                hookAddress = predicted;
                found = true;
                break;
            }
        }
        require(found, "salt not found");

        vm.startBroadcast(deployerPrivateKey);
        SharedLiquidityHook deployed = new SharedLiquidityHook{salt: salt}(poolManager, vault, treasury, feeBps);
        vm.stopBroadcast();

        require(address(deployed) == hookAddress, "hook address mismatch");

        console.log("SharedLiquidityHook deployed:", hookAddress);
        console.logBytes32(salt);
    }
}
