// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Script.sol";
import {SincBondingCurve} from "../src/SincBondingCurve.sol";

contract DeployBondingCurve is Script {
    function run() external returns (address curve) {
        uint256 deployerKey = vm.envUint("DEPLOYER_PRIVATE_KEY");
        address sinc = vm.envAddress("SINC_TOKEN");
        address treasury = vm.envAddress("TREASURY");
        address nft = vm.envAddress("GENESIS_NFT");
        address poolManager = vm.envAddress("POOL_MANAGER");
        address positionManager = vm.envAddress("POSITION_MANAGER");

        vm.startBroadcast(deployerKey);
        curve = address(new SincBondingCurve(sinc, treasury, nft, poolManager, positionManager));
        vm.stopBroadcast();

        console.log("SincBondingCurve deployed at:", curve);

        string memory chain = vm.toString(block.chainid);
        string memory path = string.concat("deployments/", chain, ".json");
        vm.writeJson(vm.toString(curve), path, ".curve");
    }
}
