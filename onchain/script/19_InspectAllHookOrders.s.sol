// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/console.sol";
import "forge-std/Script.sol";
import {OrderIdLibrary} from "@openzeppelin/uniswap-hooks/general/LimitOrderHook.sol";
import {SincLimitOrderHook} from "../src/SincLimitOrderHook.sol";
import {SincLadderConfig} from "../src/SincLadderConfig.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";

/// @notice Read-only: list unfilled hook sell orders for known signers.
contract InspectAllHookOrders is Script {
    address constant HOOK = 0x8e0eE51dCa5249c9e84dbec539fDD46b375110C0;
    address constant OLD_SAFE = 0x2d61752adF5092052Ff7D366a9884823C07Cdaf8;
    address constant AF9B = 0xAf9B539D8043C634b7E611818518BA7E850F289e;
    address constant NEW_SAFE = 0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac;

    uint24 constant POOL_FEE = 3000;
    int24 constant TICK_SPACING = 60;

    function run() external view {
        PoolKey memory key = _poolKey();
        SincLimitOrderHook hook = SincLimitOrderHook(payable(HOOK));

        address[] memory signers = new address[](3);
        signers[0] = NEW_SAFE;
        signers[1] = OLD_SAFE;
        signers[2] = AF9B;

        for (uint256 s = 0; s < signers.length; s++) {
            console.log("=== signer ===");
            console.logAddress(signers[s]);
            _inspectLadder(hook, key, signers[s], "discovery", SincLadderConfig.discoveryRamp());
            _inspectLadder(hook, key, signers[s], "floor", SincLadderConfig.floorLadder());
        }
    }

    function _inspectLadder(
        SincLimitOrderHook hook,
        PoolKey memory key,
        address signer,
        string memory label,
        SincLadderConfig.Rung[] memory rungs
    ) internal view {
        uint256 totalLiq;
        uint256 openRungs;
        for (uint256 i = 0; i < rungs.length; i++) {
            (int24 tick,) = SincLadderConfig.liquidityForRung(rungs[i]);
            OrderIdLibrary.OrderId orderId = hook.getOrderId(key, tick, false);
            uint256 liq = hook.getOrderLiquidity(orderId, signer);
            if (liq == 0) continue;
            (bool filled,,,,,) = hook.getOrderInfo(orderId);
            if (filled) continue;
            openRungs++;
            totalLiq += liq;
            console.log("open rung");
            console.log(label);
            console.log(i);
            console.logInt(tick);
            console.log("sinc atoms", rungs[i].sincAmount);
            console.log("liq", liq);
        }
        console.log("ladder summary");
        console.log(label);
        console.log("open rungs", openRungs);
        console.log("total liq", totalLiq);
    }

    function _poolKey() internal pure returns (PoolKey memory key) {
        key = PoolKey({
            currency0: Currency.wrap(0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913),
            currency1: Currency.wrap(0x9C8cd8d3961F445D653713dE65C6578bE11668e7),
            fee: POOL_FEE,
            tickSpacing: TICK_SPACING,
            hooks: IHooks(HOOK)
        });
    }
}