// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {OLTWAMMIHook} from "../src/hooks/OLTWAMMIHook.sol";
import {MockERC20} from "./mocks/MockERC20.sol";
import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {Hooks} from "@uniswap/v4-core/src/libraries/Hooks.sol";
import {HookMiner} from "@uniswap/v4-periphery/src/utils/HookMiner.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "@uniswap/v4-core/src/types/PoolId.sol";
import {SwapParams} from "@uniswap/v4-core/src/types/PoolOperation.sol";
import {Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {toBalanceDelta} from "@uniswap/v4-core/src/types/BalanceDelta.sol";

contract OLTWAMMIHookTest is Test {
    using PoolIdLibrary for PoolKey;

    OLTWAMMIHook hook;
    address constant TREASURY = address(0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac);
    address constant MOCK_PM = address(0xBEEF);
    address solver = address(0xA11CE);
    MockERC20 feeToken;
    PoolKey key;
    PoolId poolId;

    function setUp() public {
        IPoolManager poolManager = IPoolManager(MOCK_PM);
        uint160 flags = uint160(Hooks.BEFORE_SWAP_FLAG | Hooks.AFTER_SWAP_FLAG);
        bytes memory constructorArgs = abi.encode(poolManager, TREASURY, 5);
        (address hookAddress, bytes32 salt) =
            HookMiner.find(address(this), flags, type(OLTWAMMIHook).creationCode, constructorArgs);
        hook = new OLTWAMMIHook{salt: salt}(poolManager, TREASURY, 5);
        require(address(hook) == hookAddress, "hook address mismatch");

        feeToken = new MockERC20("Fee Token", "FEE", 6);
        key = PoolKey({
            currency0: Currency.wrap(address(0x1)),
            currency1: Currency.wrap(address(feeToken)),
            fee: 3000,
            tickSpacing: 60,
            hooks: IHooks(address(hook))
        });
        poolId = key.toId();
        hook.setPoolEnabled(poolId, true);
    }

    function _swapParams() internal pure returns (SwapParams memory) {
        return SwapParams({zeroForOne: true, amountSpecified: -1, sqrtPriceLimitX96: 0});
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

    function test_hookPermissions() public view {
        Hooks.Permissions memory permissions = hook.getHookPermissions();
        assertTrue(permissions.beforeSwap);
        assertTrue(permissions.afterSwap);
    }

    function test_invalidHookDataPassesThrough() public {
        vm.prank(solver);
        bytes32 intentId = hook.registerIntent(poolId, 1_000_000, 4, 100);

        bytes memory invalidHookData = bytes.concat(abi.encode(intentId), bytes1(0x01));

        vm.prank(MOCK_PM);
        (bytes4 selector,, uint24 feeOverride) = hook.beforeSwap(address(this), key, _swapParams(), invalidHookData);

        assertEq(selector, IHooks.beforeSwap.selector);
        assertEq(feeOverride, 0);

        (uint128 amountLeft, uint32 partsLeft, bool isActive) = hook.remaining(intentId);
        assertEq(amountLeft, 1_000_000);
        assertEq(partsLeft, 4);
        assertTrue(isActive);
    }

    function test_beforeAfterSwapAccruesAndSweepsFees() public {
        vm.prank(solver);
        bytes32 intentId = hook.registerIntent(poolId, 1_000_000, 4, 100);

        vm.prank(MOCK_PM);
        (bytes4 beforeSelector,, uint24 feeOverride) = hook.beforeSwap(address(this), key, _swapParams(), abi.encode(intentId));
        assertEq(beforeSelector, IHooks.beforeSwap.selector);
        assertEq(feeOverride, 0);

        vm.prank(MOCK_PM);
        (bytes4 afterSelector, int128 unspecifiedDelta) =
            hook.afterSwap(address(this), key, _swapParams(), toBalanceDelta(0, 0), "");
        assertEq(afterSelector, IHooks.afterSwap.selector);
        assertEq(unspecifiedDelta, 0);

        uint256 accruedFee = hook.accruedFees(key.currency1);
        assertEq(accruedFee, 125);

        (uint128 amountLeft, uint32 partsLeft, bool isActive) = hook.remaining(intentId);
        assertEq(amountLeft, 750_000);
        assertEq(partsLeft, 3);
        assertTrue(isActive);

        feeToken.mint(address(hook), accruedFee);
        uint256 treasuryBalanceBefore = feeToken.balanceOf(TREASURY);

        hook.sweepFees(key.currency1);

        assertEq(feeToken.balanceOf(TREASURY), treasuryBalanceBefore + accruedFee);
        assertEq(hook.accruedFees(key.currency1), 0);
    }
}
