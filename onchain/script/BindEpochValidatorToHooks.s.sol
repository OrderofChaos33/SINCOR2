// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";

interface IHookSettlementBinding {
    function setSettlementGate(address gate) external;
}

/// @notice Binds deployed settlement gate to existing Uniswap v4 routing/liquidity hooks.
contract BindEpochValidatorToHooks is Script {
    function run() external {
        uint256 pk = vm.envUint("PRIVATE_KEY");
        uint256 expectedChain = vm.envOr("TARGET_CHAIN_ID", uint256(8453));
        address settlementGate = vm.envAddress("SETTLEMENT_GATE");
        address hook0 = vm.envOr("HOOK_0", address(0));
        address[] memory hooks = hook0 == address(0)
            ? new address[](0)
            : new address[](1);

        if (hooks.length == 1) {
            hooks[0] = hook0;
        }

        require(block.chainid == expectedChain, "wrong-chain");

        vm.startBroadcast(pk);
        for (uint256 i; i < hooks.length; ++i) {
            IHookSettlementBinding(hooks[i]).setSettlementGate(settlementGate);
            console.log("bound hook", hooks[i]);
        }
        vm.stopBroadcast();
    }
}
