// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {OLTWAMMIHook} from "../src/hooks/OLTWAMMIHook.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {PoolId} from "@uniswap/v4-core/src/types/PoolId.sol";

contract OLTWAMMIHookTest is Test {
    OLTWAMMIHook hook;
    address constant TREASURY = address(0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac);
    address constant MOCK_PM = address(0xBEEF);
    address solver = address(0xA11CE);
    PoolId poolId;

    function setUp() public {
        hook = new OLTWAMMIHook(IPoolManager(MOCK_PM), TREASURY, 5);
        poolId = PoolId.wrap(keccak256("test-pool"));
        hook.setPoolEnabled(poolId, true);
    }

    function test_registerIntent() public {
        vm.prank(solver);
        bytes32 intentId = hook.registerIntent(poolId, 1_000_000e6, 10, 100);
        (uint128 amountLeft, uint32 partsLeft, bool isActive) = hook.remaining(intentId);
        assertEq(amountLeft, 1_000_000e6);
        assertEq(partsLeft, 10);
        assertTrue(isActive);
    }

    function test_cancelIntent_onlySolver() public {
        vm.prank(solver);
        bytes32 intentId = hook.registerIntent(poolId, 100e6, 4, 50);
        vm.expectRevert(OLTWAMMIHook.Unauthorized.selector);
        hook.cancelIntent(intentId);
        vm.prank(solver);
        hook.cancelIntent(intentId);
        (, , bool isActive) = hook.remaining(intentId);
        assertFalse(isActive);
    }

    function test_feeCap() public {
        vm.expectRevert(OLTWAMMIHook.FeeTooHigh.selector);
        hook.setProtocolFee(101);
        hook.setProtocolFee(50);
        assertEq(hook.protocolFeeBps(), 50);
    }

    function test_poolMustBeEnabled() public {
        PoolId other = PoolId.wrap(keccak256("other"));
        vm.prank(solver);
        vm.expectRevert(OLTWAMMIHook.PoolNotEnabled.selector);
        hook.registerIntent(other, 100e6, 2, 10);
    }

    function test_zeroAmountReverts() public {
        vm.prank(solver);
        vm.expectRevert(OLTWAMMIHook.ZeroAmount.selector);
        hook.registerIntent(poolId, 0, 2, 10);
    }

    function test_zeroPartsReverts() public {
        vm.prank(solver);
        vm.expectRevert(OLTWAMMIHook.ZeroParts.selector);
        hook.registerIntent(poolId, 100e6, 0, 10);
    }

    function test_treasuryIsImmutable() public {
        assertEq(hook.treasury(), TREASURY);
    }
}
