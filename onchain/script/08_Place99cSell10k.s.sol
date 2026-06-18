// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Script.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";
import {PoolId, PoolIdLibrary} from "@uniswap/v4-core/src/types/PoolId.sol";
import {StateLibrary} from "@uniswap/v4-core/src/libraries/StateLibrary.sol";
import {SincLimitOrderHook} from "../src/SincLimitOrderHook.sol";
import {FloorPriceMath} from "../src/FloorPriceMath.sol";

/// @notice Treasury places 10,000 SINC sell limit at $0.99 via SincLimitOrderHook.
/// @dev Fills when a buyer swaps USDC→SINC through the hook pool crossing this tick.
///      After fill, call withdraw() on the hook to move USDC to treasury.
contract Place99cSell10k is Script {
    using PoolIdLibrary for PoolKey;
    using StateLibrary for IPoolManager;

    address constant TREASURY = 0xAf9B539D8043C634b7E611818518BA7E850F289e;
    address constant SINC = 0x9C8cd8d3961F445D653713dE65C6578bE11668e7;
    address constant USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    address constant POOL_MANAGER = 0x498581fF718922c3f8e6A244956aF099B2652b2b;
    address constant HOOK = 0x8e0eE51dCa5249c9e84dbec539fDD46b375110C0;

    uint24 constant POOL_FEE = 3000;
    int24 constant TICK_SPACING = 60;

    uint256 constant SELL_SINC = 10_000 * 1e8;
    uint256 constant USD_PER_SINC_E18 = 99e16; // $0.99

    function run() external {
        PoolKey memory key = _poolKey();
        PoolId id = key.toId();
        (uint160 sqrtPrice,,,) = IPoolManager(POOL_MANAGER).getSlot0(id);
        require(sqrtPrice != 0, "Pool not initialized");

        uint256 balance = IERC20(SINC).balanceOf(TREASURY);
        require(balance >= SELL_SINC, "Need 10k SINC in treasury");

        int24 tick = FloorPriceMath.tickFromUsd(USD_PER_SINC_E18);
        uint128 liquidity = FloorPriceMath.liquidityForSincAmount(tick, SELL_SINC);
        require(liquidity > 0, "zero liquidity");

        console.log("Placing 10k SINC sell wall at $0.99");
        console.log("tick:", tick);
        console.log("liquidity:", liquidity);

        SincLimitOrderHook hook = SincLimitOrderHook(payable(HOOK));

        vm.startBroadcast();
        IERC20(SINC).approve(HOOK, SELL_SINC);
        hook.placeOrder(key, tick, false, liquidity);
        vm.stopBroadcast();

        console.log("Sell limit live - buyer must lift via USDC swap to fill");
    }

    function _poolKey() internal pure returns (PoolKey memory key) {
        key = PoolKey({
            currency0: Currency.wrap(USDC),
            currency1: Currency.wrap(SINC),
            fee: POOL_FEE,
            tickSpacing: TICK_SPACING,
            hooks: IHooks(HOOK)
        });
    }
}