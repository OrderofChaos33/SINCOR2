// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Script.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

contract FundCurveWithSINC is Script {
    function run() external {
        uint256 deployerKey = vm.envUint("DEPLOYER_PRIVATE_KEY");
        address sinc = vm.envAddress("SINC_TOKEN");
        address curve = vm.envAddress("CURVE");
        uint256 amount = 65_000_000 * 10**8;

        vm.startBroadcast(deployerKey);
        IERC20(sinc).transfer(curve, amount);
        vm.stopBroadcast();

        console.log("Funded curve with 65M SINC");
    }
}
