// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Script.sol";
import {SincBondingCurve} from "../src/SincBondingCurve.sol";
import {SincGenesisNFT} from "../src/SincGenesisNFT.sol";

contract SmokeTest is Script {
    function run() external {
        uint256 deployerKey = vm.envUint("DEPLOYER_PRIVATE_KEY");
        address deployer = vm.addr(deployerKey);
        SincBondingCurve curve = SincBondingCurve(payable(vm.envAddress("CURVE")));
        SincGenesisNFT nft = SincGenesisNFT(vm.envAddress("GENESIS_NFT"));

        console.log("Pre-buy: NFT balance:", nft.balanceOf(deployer));
        console.log("Pre-buy: current price (wei per 1 SINC):", curve.currentPriceWei());

        vm.startBroadcast(deployerKey);
        curve.buy{value: 0.001 ether}(0.001 ether, address(0));
        vm.stopBroadcast();

        console.log("Post-buy: NFT balance:", nft.balanceOf(deployer));
        console.log("Post-buy: SINC sold:", curve.sincSold());
        console.log("Post-buy: ETH accumulated:", curve.ethAccumulated());
    }
}
