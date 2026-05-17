// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Test.sol";
import {SincLimitOrderHook} from "../src/SincLimitOrderHook.sol";
import {Hooks} from "@uniswap/v4-core/src/libraries/Hooks.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";

contract SincLimitOrderHookTest is Test {
    address constant POOL_MANAGER = 0x498581fF718922c3f8e6A244956aF099B2652b2b;

    function setUp() public {
        vm.createSelectFork(vm.rpcUrl("base"));
    }

    function test_HookHasCorrectPermissions() public {
        // Mine a CREATE2 salt for the right permission flags
        bytes memory creationCode = abi.encodePacked(type(SincLimitOrderHook).creationCode, abi.encode(POOL_MANAGER));
        (address minedAddr, bytes32 salt) = _mineHookAddr(creationCode, address(this));
        SincLimitOrderHook hook = SincLimitOrderHook(payable(_deployWithCreate2(creationCode, salt)));
        assertEq(address(hook), minedAddr, "deployed address must match mined");

        Hooks.Permissions memory perms = hook.getHookPermissions();
        assertTrue(perms.beforeSwap, "beforeSwap must be enabled for anti-sandwich");
        assertTrue(perms.afterInitialize, "afterInitialize must be enabled for OZ LimitOrderHook");
        assertTrue(perms.afterSwap, "afterSwap must be enabled for OZ LimitOrderHook fill");
    }

    function _mineHookAddr(bytes memory creationCode, address deployer) internal pure returns (address, bytes32) {
        // Required permission bits — placeholder; real script in Task 17 uses Hooks.*_FLAG constants
        uint160 requiredBits = 0x2400;  // beforeSwap | afterSwap (rough mask; refine in Task 17)
        for (uint256 i = 0; i < 100000; i++) {
            bytes32 salt = bytes32(i);
            address predicted = address(uint160(uint256(keccak256(abi.encodePacked(
                bytes1(0xff), deployer, salt, keccak256(creationCode)
            )))));
            if ((uint160(predicted) & 0xFFFF) == requiredBits) return (predicted, salt);
        }
        revert("Salt not found in 100k tries");
    }

    function _deployWithCreate2(bytes memory creationCode, bytes32 salt) internal returns (address addr) {
        assembly {
            addr := create2(0, add(creationCode, 0x20), mload(creationCode), salt)
        }
        require(addr != address(0), "create2 failed");
    }
}
