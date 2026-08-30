// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {BribeLoopHook, PoolKey, BalanceDelta, IPoolManager, Hooks} from "../contracts/hooks/BribeLoopHook.sol";

contract MockToken is ERC20 {
    constructor() ERC20("OUT", "OUT") {
        _mint(msg.sender, 1_000_000 * 1e18);
    }
}

contract BribeLoopHookTest is Test {
    BribeLoopHook public hook;
    MockToken public token;

    address public poolManager = address(0xB0B);
    address public escrow = address(0xE5C);

    function setUp() public {
        token = new MockToken();
        hook = new BribeLoopHook(poolManager, escrow);
    }

    function test_ConstructorRevertsOnZero() public {
        vm.expectRevert("Invalid address");
        new BribeLoopHook(address(0), escrow);

        vm.expectRevert("Invalid address");
        new BribeLoopHook(poolManager, address(0));
    }

    function test_GetHookPermissionsAfterSwapOnly() public view {
        Hooks.Permissions memory perms = hook.getHookPermissions();
        assertFalse(perms.beforeInitialize);
        assertFalse(perms.afterInitialize);
        assertFalse(perms.beforeAddLiquidity);
        assertFalse(perms.afterAddLiquidity);
        assertFalse(perms.beforeRemoveLiquidity);
        assertFalse(perms.afterRemoveLiquidity);
        assertFalse(perms.beforeSwap);
        assertTrue(perms.afterSwap);
        assertFalse(perms.beforeDonate);
        assertFalse(perms.afterDonate);
        assertFalse(perms.beforeSwapReturnDelta);
        assertFalse(perms.afterSwapReturnDelta);
        assertFalse(perms.afterAddLiquidityReturnDelta);
        assertFalse(perms.afterRemoveLiquidityReturnDelta);
    }

    function test_SetBribeBps() public {
        hook.setBribeBps(100);
        assertEq(hook.bribeBps(), 100);
    }

    function test_SetBribeBpsCappedAt5Percent() public {
        vm.expectRevert("Bribe fee capped at 5%");
        hook.setBribeBps(501);
    }

    function test_SetBribeBpsOnlyOwner() public {
        vm.prank(address(0xBEEF));
        vm.expectRevert("Caller not Owner");
        hook.setBribeBps(10);
    }

    function test_SetTaskEscrow() public {
        address next = address(0xABC);
        hook.setTaskEscrow(next);
        assertEq(hook.taskEscrow(), next);
    }

    function test_SetTaskEscrowRejectsZero() public {
        vm.expectRevert("Invalid escrow address");
        hook.setTaskEscrow(address(0));
    }

    function test_AfterSwapOnlyPoolManager() public {
        PoolKey memory key = PoolKey({
            currency0: address(0),
            currency1: address(token),
            fee: 3000,
            tickSpacing: 60,
            hooks: address(hook)
        });
        IPoolManager.SwapParams memory params =
            IPoolManager.SwapParams({zeroForOne: true, amountSpecified: -1e18, sqrtPriceLimitX96: 0});
        BalanceDelta memory delta = BalanceDelta({amount0: 0, amount1: 1e18});

        vm.expectRevert("Caller not PoolManager");
        hook.afterSwap(address(this), key, params, delta, "");
    }

    function test_AfterSwapRoutesBribeToEscrow() public {
        uint256 outputAmount = 1_000e18;
        token.transfer(address(hook), outputAmount);

        PoolKey memory key = PoolKey({
            currency0: address(0x1),
            currency1: address(token),
            fee: 3000,
            tickSpacing: 60,
            hooks: address(hook)
        });
        IPoolManager.SwapParams memory params = IPoolManager.SwapParams({
            zeroForOne: true,
            amountSpecified: -int256(outputAmount),
            sqrtPriceLimitX96: 0
        });
        BalanceDelta memory delta = BalanceDelta({amount0: 0, amount1: int128(int256(outputAmount))});

        vm.prank(poolManager);
        hook.afterSwap(address(this), key, params, delta, "");

        uint256 expected = (outputAmount * 50) / 10_000; // default 50 bps
        assertEq(token.balanceOf(escrow), expected);
        assertEq(token.balanceOf(address(hook)), outputAmount - expected);
    }
}
