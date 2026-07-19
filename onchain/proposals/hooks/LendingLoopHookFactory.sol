// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {BaseLendingLoopHookContainer} from "./BaseLendingLoopHookContainer.sol";
import {LoopAmplifyHook} from "./LoopAmplifyHook.sol";
import {AutoCapitalizeMonetizeHook} from "./AutoCapitalizeMonetizeHook.sol";

/// @title LendingLoopHookFactory — deploys lending-loop hook variants
contract LendingLoopHookFactory {
    IPoolManager public immutable poolManager;

    event HookDeployed(address indexed hook, string variant);

    constructor(IPoolManager _manager) {
        poolManager = _manager;
    }

    function deployLoopAmplify() external returns (address) {
        LoopAmplifyHook h = new LoopAmplifyHook(poolManager);
        emit HookDeployed(address(h), "LoopAmplify");
        return address(h);
    }

    function deployAutoCapitalize() external returns (address) {
        AutoCapitalizeMonetizeHook h = new AutoCapitalizeMonetizeHook(poolManager);
        emit HookDeployed(address(h), "AutoCapitalizeMonetize");
        return address(h);
    }
}
