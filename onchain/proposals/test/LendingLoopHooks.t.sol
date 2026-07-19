// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {LendingLoopHookFactory} from "../src/hooks/LendingLoopHookFactory.sol";
import {LoopAmplifyHook} from "../src/hooks/LoopAmplifyHook.sol";
import {AutoCapitalizeMonetizeHook} from "../src/hooks/AutoCapitalizeMonetizeHook.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "@uniswap/v4-core/src/types/PoolId.sol";

contract LendingLoopHooksTest is Test {
    using PoolIdLibrary for PoolKey;

    LendingLoopHookFactory factory;
    address pm = makeAddr("poolManager");

    function setUp() public {
        factory = new LendingLoopHookFactory(IPoolManager(pm));
    }

    function test_deployLoopAmplify() public {
        address h = factory.deployLoopAmplify();
        assertTrue(h != address(0));
    }

    function test_deployAutoCapitalize() public {
        address h = factory.deployAutoCapitalize();
        assertTrue(h != address(0));
    }
}
