// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";

import {SincSovereignRevenueRouter} from "../src/sincor/SincSovereignRevenueRouter.sol";

/// @notice Deploy sovereign revenue router for node ops, POL, and treasury reserve routing.
contract DeploySovereignRevenueRouter is Script {
    function run() external {
        uint256 pk = vm.envUint("PRIVATE_KEY");
        uint256 expectedChain = vm.envOr("TARGET_CHAIN_ID", uint256(8453));
        address admin = vm.envOr("REVENUE_ROUTER_ADMIN", vm.addr(pk));
        address nodeOpsReceiver = vm.envAddress("NODE_OPS_RECEIVER");
        address polReceiver = vm.envAddress("POL_RECEIVER");

        uint16 nodeOpsBps = uint16(vm.envOr("NODE_OPS_BPS", uint256(4000)));
        uint16 polBps = uint16(vm.envOr("POL_BPS", uint256(3000)));
        uint16 treasuryBps = uint16(vm.envOr("TREASURY_BPS", uint256(3000)));

        require(block.chainid == expectedChain, "wrong-chain");

        vm.startBroadcast(pk);
        SincSovereignRevenueRouter router = new SincSovereignRevenueRouter(
            admin,
            nodeOpsReceiver,
            polReceiver,
            nodeOpsBps,
            polBps,
            treasuryBps
        );
        vm.stopBroadcast();

        console.log("SincSovereignRevenueRouter:", address(router));
    }
}
