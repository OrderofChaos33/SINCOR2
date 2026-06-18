// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/console.sol";
import {OrderIdLibrary} from "@openzeppelin/uniswap-hooks/general/LimitOrderHook.sol";
import {SincLimitOrderHook} from "../src/SincLimitOrderHook.sol";
import {SignerScript} from "./SignerScript.sol";

/// @notice Withdraw USDC proceeds from filled limit orders on the live hook.
/// @dev Set FILL_ORDER_ID (uint232) in .env — get from Place logs / Fill events before mapping clears.
///      Repeat with each filled order id. Sends proceeds to msg.sender (treasury signer).
contract WithdrawFilledOrders is SignerScript {
    address constant HOOK = 0x8e0eE51dCa5249c9e84dbec539fDD46b375110C0;

    function run() external {
        uint256 orderIdRaw = vm.envUint("FILL_ORDER_ID");
        uint256 signerKey = _loadSignerKey();
        address treasury = vm.addr(signerKey);

        SincLimitOrderHook hook = SincLimitOrderHook(payable(HOOK));
        OrderIdLibrary.OrderId orderId = OrderIdLibrary.OrderId.wrap(uint232(orderIdRaw));

        (bool filled,,, uint256 usdcTotal, uint256 sincTotal, uint128 liqTotal) = hook.getOrderInfo(orderId);
        require(filled, "Order not filled yet");
        console.log("Withdrawing filled order", orderIdRaw);
        console.log("  USDC total:", usdcTotal);
        console.log("  SINC total:", sincTotal);
        console.log("  liquidity:", liqTotal);

        vm.startBroadcast(signerKey);
        (uint256 amount0, uint256 amount1) = hook.withdraw(orderId, treasury);
        vm.stopBroadcast();

        console.log("Withdrawn USDC:", amount0);
        console.log("Withdrawn SINC:", amount1);
    }
}