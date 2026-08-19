// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {Hooks} from "@uniswap/v4-core/src/libraries/Hooks.sol";
import {HookMiner} from "@uniswap/v4-periphery/src/utils/HookMiner.sol";

import {LiquidityAmplifierHook} from "../src/LiquidityAmplifierHook.sol";

/// @notice Deploys LiquidityAmplifierHook using CREATE2 at a HookMiner-mined address.
contract DeployLiquidityAmplifierHook is Script {
    function run() external returns (address hookAddress, bytes32 salt) {
        uint256 deployerPrivateKey = vm.envUint("PRIVATE_KEY");
        address guardian = vm.envOr("GUARDIAN", vm.addr(deployerPrivateKey));
        address create2Factory = vm.envOr("CREATE2_FACTORY", vm.addr(deployerPrivateKey));
        IPoolManager poolManager = IPoolManager(vm.envAddress("POOL_MANAGER"));

        uint160 flags = uint160(
            Hooks.BEFORE_ADD_LIQUIDITY_FLAG | Hooks.AFTER_ADD_LIQUIDITY_FLAG | Hooks.BEFORE_REMOVE_LIQUIDITY_FLAG
                | Hooks.AFTER_REMOVE_LIQUIDITY_FLAG | Hooks.BEFORE_SWAP_FLAG | Hooks.AFTER_SWAP_FLAG
        );
        bytes memory constructorArgs = abi.encode(poolManager, guardian);
        (hookAddress, salt) = HookMiner.find(create2Factory, flags, type(LiquidityAmplifierHook).creationCode, constructorArgs);

        vm.startBroadcast(deployerPrivateKey);
        LiquidityAmplifierHook hook = new LiquidityAmplifierHook{salt: salt}(poolManager, guardian);
        vm.stopBroadcast();
        require(address(hook) == hookAddress, "hook address mismatch");

        console.log("LiquidityAmplifierHook deployed:", hookAddress);
        console.logBytes32(salt);
    }
}
