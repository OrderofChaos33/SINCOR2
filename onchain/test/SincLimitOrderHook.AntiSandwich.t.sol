// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Test.sol";
import {SincLimitOrderHook} from "../src/SincLimitOrderHook.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {SwapParams} from "@uniswap/v4-core/src/types/PoolOperation.sol";
import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";
import {Hooks} from "@uniswap/v4-core/src/libraries/Hooks.sol";

contract SincLimitOrderHookAntiSandwichTest is Test {
    address constant POOL_MANAGER = 0x498581fF718922c3f8e6A244956aF099B2652b2b;
    SincLimitOrderHook hook;

    function setUp() public {
        vm.createSelectFork(vm.rpcUrl("base"));
        bytes memory creationCode = abi.encodePacked(type(SincLimitOrderHook).creationCode, abi.encode(POOL_MANAGER));
        bytes32 salt = _findSalt(creationCode);
        hook = SincLimitOrderHook(payable(_deployWithCreate2(creationCode, salt)));
    }

    function test_FirstSwapInBlock_GetsBaseFee() public {
        PoolKey memory key = _dummyKey();
        SwapParams memory params;
        vm.prank(POOL_MANAGER);
        (, , uint24 fee) = hook.beforeSwap(address(this), key, params, "");
        assertEq(fee, hook.BASE_FEE(), "first swap should pay base fee");
    }

    function test_SecondSwapInBlock_GetsSandwichFee() public {
        PoolKey memory key = _dummyKey();
        SwapParams memory params;

        vm.prank(POOL_MANAGER);
        hook.beforeSwap(address(this), key, params, "");

        vm.prank(POOL_MANAGER);
        (, , uint24 fee) = hook.beforeSwap(address(this), key, params, "");
        assertEq(fee, hook.SANDWICH_FEE(), "second swap same block should pay sandwich fee");
    }

    function test_DifferentBlock_ResetsFee() public {
        PoolKey memory key = _dummyKey();
        SwapParams memory params;

        vm.prank(POOL_MANAGER);
        hook.beforeSwap(address(this), key, params, "");

        vm.roll(block.number + 1);

        vm.prank(POOL_MANAGER);
        (, , uint24 fee) = hook.beforeSwap(address(this), key, params, "");
        assertEq(fee, hook.BASE_FEE(), "new block should reset to base fee");
    }

    function _dummyKey() internal pure returns (PoolKey memory) {
        return PoolKey({
            currency0: Currency.wrap(address(0x1)),
            currency1: Currency.wrap(address(0x2)),
            fee: 3000,
            tickSpacing: 60,
            hooks: IHooks(address(0))
        });
    }

    function _findSalt(bytes memory creationCode) internal view returns (bytes32) {
        // AFTER_INITIALIZE | BEFORE_SWAP | AFTER_SWAP = 0x10C0; 14-bit mask.
        uint160 requiredBits = uint160(Hooks.AFTER_INITIALIZE_FLAG | Hooks.BEFORE_SWAP_FLAG | Hooks.AFTER_SWAP_FLAG);
        for (uint256 i = 0; i < 1000000; i++) {
            bytes32 salt = bytes32(i);
            address predicted = address(uint160(uint256(keccak256(abi.encodePacked(
                bytes1(0xff), address(this), salt, keccak256(creationCode)
            )))));
            if ((uint160(predicted) & 0x3FFF) == requiredBits) return salt;
        }
        revert("salt not found");
    }

    function _deployWithCreate2(bytes memory creationCode, bytes32 salt) internal returns (address addr) {
        assembly {
            addr := create2(0, add(creationCode, 0x20), mload(creationCode), salt)
        }
        require(addr != address(0), "create2 failed");
    }
}
