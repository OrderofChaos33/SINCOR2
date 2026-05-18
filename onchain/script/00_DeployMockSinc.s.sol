// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Script.sol";
import {MockSinc} from "../test/mocks/MockSinc.sol";

contract DeployMockSinc is Script {
    function run() external returns (address) {
        uint256 deployerKey = vm.envUint("DEPLOYER_PRIVATE_KEY");
        address deployer = vm.addr(deployerKey);

        vm.startBroadcast(deployerKey);
        MockSinc sinc = new MockSinc(deployer);
        vm.stopBroadcast();

        console.log("MockSinc deployed at:", address(sinc));
        return address(sinc);
    }
}
