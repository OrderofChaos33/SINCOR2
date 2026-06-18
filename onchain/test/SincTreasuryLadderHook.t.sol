// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Test.sol";
import {SincTreasuryLadderHook} from "../src/SincTreasuryLadderHook.sol";
import {Hooks} from "@uniswap/v4-core/src/libraries/Hooks.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";

contract SincTreasuryLadderHookTest is Test {
    address constant POOL_MANAGER = 0x498581fF718922c3f8e6A244956aF099B2652b2b;
    address constant TREASURY = 0x7B4082f78CdAc2cB5fa8572b2CA54BeDaaa8f956;

    function test_V2PermissionsMatchV1() public {
        bytes memory creationCode =
            abi.encodePacked(type(SincTreasuryLadderHook).creationCode, abi.encode(POOL_MANAGER, TREASURY));
        (address addr, bytes32 salt) = _mine(creationCode);
        SincTreasuryLadderHook hook = SincTreasuryLadderHook(payable(_deploy(creationCode, salt)));
        assertEq(address(hook), addr);

        Hooks.Permissions memory perms = hook.getHookPermissions();
        assertTrue(perms.beforeSwap);
        assertTrue(perms.afterInitialize);
        assertTrue(perms.afterSwap);
        assertEq(hook.treasury(), TREASURY);
    }

    function _mine(bytes memory creationCode) internal view returns (address, bytes32) {
        uint160 required =
            uint160(Hooks.AFTER_INITIALIZE_FLAG | Hooks.BEFORE_SWAP_FLAG | Hooks.AFTER_SWAP_FLAG);
        for (uint256 i = 0; i < 500_000; i++) {
            bytes32 salt = bytes32(i);
            address predicted = address(
                uint160(uint256(keccak256(abi.encodePacked(bytes1(0xff), address(this), salt, keccak256(creationCode)))))
            );
            if ((uint160(predicted) & 0x3FFF) == required) return (predicted, salt);
        }
        revert("salt not found");
    }

    function _deploy(bytes memory creationCode, bytes32 salt) internal returns (address addr) {
        assembly {
            addr := create2(0, add(creationCode, 0x20), mload(creationCode), salt)
        }
        require(addr != address(0), "create2 failed");
    }
}