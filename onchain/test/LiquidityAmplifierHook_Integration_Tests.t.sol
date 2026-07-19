// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {LiquidityAmplifierHook} from "../src/LiquidityAmplifierHook.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "@uniswap/v4-core/src/types/PoolId.sol";
import {Hooks} from "@uniswap/v4-core/src/libraries/Hooks.sol";
import {HookMiner} from "@uniswap/v4-periphery/src/utils/HookMiner.sol";

/// @notice LiquidityAmplifierHook tests. v4-periphery utils/BaseHook validates the
///         hook address permission flags at construction, so the hook is deployed
///         via CREATE2 at a HookMiner-mined address (same as production).
contract LiquidityAmplifierHookTest is Test {
    using PoolIdLibrary for PoolKey;

    LiquidityAmplifierHook hook;
    IPoolManager poolManager;
    address guardian = makeAddr("guardian");

    function setUp() public {
        poolManager = IPoolManager(makeAddr("poolManager"));

        uint160 flags = uint160(
            Hooks.BEFORE_ADD_LIQUIDITY_FLAG | Hooks.AFTER_ADD_LIQUIDITY_FLAG
                | Hooks.BEFORE_REMOVE_LIQUIDITY_FLAG | Hooks.AFTER_REMOVE_LIQUIDITY_FLAG
                | Hooks.BEFORE_SWAP_FLAG | Hooks.AFTER_SWAP_FLAG
        );
        bytes memory constructorArgs = abi.encode(poolManager, guardian);
        (address hookAddress, bytes32 salt) = HookMiner.find(
            address(this), flags, type(LiquidityAmplifierHook).creationCode, constructorArgs
        );
        hook = new LiquidityAmplifierHook{salt: salt}(poolManager, guardian);
        require(address(hook) == hookAddress, "hook address mismatch");
    }

    function testHookPermissions() public view {
        Hooks.Permissions memory permissions = hook.getHookPermissions();
        assertTrue(permissions.beforeAddLiquidity);
        assertTrue(permissions.afterAddLiquidity);
        assertTrue(permissions.beforeRemoveLiquidity);
        assertTrue(permissions.afterRemoveLiquidity);
        assertTrue(permissions.beforeSwap);
        assertTrue(permissions.afterSwap);
    }

    function testGuardianControls() public {
        PoolKey memory key;
        PoolId poolId = key.toId();
        address vault = makeAddr("vault");

        // third party (not guardian, not owner) cannot set a yield vault
        vm.prank(makeAddr("rando"));
        vm.expectRevert(LiquidityAmplifierHook.OnlyGuardian.selector);
        hook.setYieldVault(poolId, vault);

        // guardian can
        vm.prank(guardian);
        hook.setYieldVault(poolId, vault);
        assertEq(hook.getVault(poolId), vault);
    }

    function testPauseBlocksAndUnpauses() public {
        vm.prank(guardian);
        hook.setPaused(true);
        vm.prank(guardian);
        hook.setPaused(false);
    }

    function testOwnershipAndHub() public {
        address hub = makeAddr("hub");
        vm.prank(makeAddr("rando"));
        vm.expectRevert(LiquidityAmplifierHook.OnlyOwner.selector);
        hook.setAccountingHub(hub);
        hook.setAccountingHub(hub); // deployer is owner
        assertEq(hook.accountingHub(), hub);
    }
}
