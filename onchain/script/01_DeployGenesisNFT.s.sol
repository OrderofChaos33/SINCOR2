// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Script.sol";
import {SincGenesisNFT} from "../src/SincGenesisNFT.sol";

contract DeployGenesisNFT is Script {
    function run() external returns (address nft) {
        uint256 deployerKey = vm.envUint("DEPLOYER_PRIVATE_KEY");
        address deployer = vm.addr(deployerKey);

        address predictedCurve = vm.computeCreateAddress(deployer, vm.getNonce(deployer) + 1);
        console.log("Predicted curve address (will be deployed next):", predictedCurve);

        vm.startBroadcast(deployerKey);
        nft = address(new SincGenesisNFT(predictedCurve));
        vm.stopBroadcast();

        console.log("SincGenesisNFT deployed at:", nft);

        string memory chain = vm.toString(block.chainid);
        string memory path = string.concat("deployments/", chain, ".json");
        vm.writeJson(vm.toString(nft), path, ".nft");
    }
}
