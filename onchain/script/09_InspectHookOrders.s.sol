// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Script.sol";
import {OrderIdLibrary} from "@openzeppelin/uniswap-hooks/general/LimitOrderHook.sol";
import {SincLimitOrderHook} from "../src/SincLimitOrderHook.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";

import {FloorPriceMath} from "../src/FloorPriceMath.sol";

/// @notice Read-only: inspect $1.50 and $0.99 sell rungs on the live hook.
contract InspectHookOrders is Script {
    address constant HOOK = 0x8e0eE51dCa5249c9e84dbec539fDD46b375110C0;
    address constant TREASURY = 0xAf9B539D8043C634b7E611818518BA7E850F289e;
    address constant SINC = 0x9C8cd8d3961F445D653713dE65C6578bE11668e7;
    address constant USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;

    uint24 constant POOL_FEE = 3000;
    int24 constant TICK_SPACING = 60;

    function run() external view {
        PoolKey memory key = _poolKey();
        SincLimitOrderHook hook = SincLimitOrderHook(payable(HOOK));

        _logRung(hook, key, 15e17, "USD 1.50");
        _logRung(hook, key, 99e16, "USD 0.99");
    }

    function _logRung(SincLimitOrderHook hook, PoolKey memory key, uint256 usdE18, string memory label) internal view {
        int24 tick = FloorPriceMath.tickFromUsd(usdE18);
        uint256 orderId = OrderIdLibrary.OrderId.unwrap(hook.getOrderId(key, tick, false));
        console.log("--- rung:", label);
        console.log("tick:", tick);
        console.log("orderId:", orderId);
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