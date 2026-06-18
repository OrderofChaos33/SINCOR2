// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/console.sol";
import {OrderIdLibrary} from "@openzeppelin/uniswap-hooks/general/LimitOrderHook.sol";
import {SincLimitOrderHook} from "../src/SincLimitOrderHook.sol";
import {SincLadderConfig} from "../src/SincLadderConfig.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";
import {SignerScript} from "./SignerScript.sol";

/// @notice Cancel unfilled hook sell orders placed by signer; return SINC to recipient.
/// @dev Use old Safe key if orders were placed from 0x2d61. Set RECIPIENT=0x09E289… in env.
contract RecoverHookSinc is SignerScript {
    address constant HOOK = 0x8e0eE51dCa5249c9e84dbec539fDD46b375110C0;
    address constant DEFAULT_RECIPIENT = 0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac;

    uint24 constant POOL_FEE = 3000;
    int24 constant TICK_SPACING = 60;

    function run() external {
        uint256 signerKey = _loadSignerKey();
        address signer = vm.addr(signerKey);
        address recipient = vm.envOr("RECIPIENT", DEFAULT_RECIPIENT);

        PoolKey memory key = _poolKey();
        SincLimitOrderHook hook = SincLimitOrderHook(payable(HOOK));

        uint256 cancelled;
        uint256 sincBefore = IERC20Minimal(_sinc()).balanceOf(recipient);

        bool skipDiscovery = vm.envOr("SKIP_DISCOVERY", false);
        uint256 maxFloor = vm.envOr("MAX_FLOOR_RUNGS", uint256(0));

        vm.startBroadcast(signerKey);
        if (!skipDiscovery) {
            cancelled += _cancelRungs(hook, key, SincLadderConfig.discoveryRamp(), recipient, signer, "discovery", 0);
        }
        cancelled += _cancelRungs(hook, key, SincLadderConfig.floorLadder(), recipient, signer, "floor", maxFloor);
        vm.stopBroadcast();

        uint256 sincAfter = IERC20Minimal(_sinc()).balanceOf(recipient);
        console.log("Signer:", signer);
        console.log("Recipient:", recipient);
        console.log("Rungs cancelled:", cancelled);
        console.log("SINC recovered:", sincAfter - sincBefore);
    }

    function _cancelRungs(
        SincLimitOrderHook hook,
        PoolKey memory key,
        SincLadderConfig.Rung[] memory rungs,
        address recipient,
        address signer,
        string memory label,
        uint256 maxRungs
    ) internal returns (uint256 count) {
        uint256 limit = rungs.length;
        if (maxRungs > 0 && maxRungs < limit) {
            limit = maxRungs;
        }
        for (uint256 i = 0; i < limit; i++) {
            (int24 tick,) = SincLadderConfig.liquidityForRung(rungs[i]);
            OrderIdLibrary.OrderId orderId = hook.getOrderId(key, tick, false);
            uint256 liq = hook.getOrderLiquidity(orderId, signer);
            if (liq == 0) continue;
            (bool filled,,,,,) = hook.getOrderInfo(orderId);
            if (filled) continue;
            console.log("Cancelling rung");
            console.log(i);
            console.logInt(tick);
            console.log("liq", liq);
            hook.cancelOrder(key, tick, false, recipient);
            count++;
        }
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

    function _sinc() internal pure returns (address) {
        return 0x9C8cd8d3961F445D653713dE65C6578bE11668e7;
    }
}

interface IERC20Minimal {
    function balanceOf(address) external view returns (uint256);
}