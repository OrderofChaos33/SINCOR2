// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script} from "forge-std/Script.sol";
import {console} from "forge-std/console.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SincFluidAdapter} from "../src/fluid/SincFluidAdapter.sol";

/// @notice Deploys SincFluidAdapter on Base (8453).
///         forge script script/DeploySincFluidAdapter.s.sol \
///           --rpc-url $BASE_RPC_URL --broadcast --verify \
///           --etherscan-api-key $BASESCAN_API_KEY -vvvv
///         Env overrides: SINC_ADDRESS / USDC_ADDRESS / GUARDIAN_ADDRESS / TREASURY_ADDRESS
contract DeploySincFluidAdapter is Script {
    // Canonical Base addresses (CANONICAL_ADDRESSES.md wins if drift is ever found)
    address constant SINC = 0x9C8cd8d3961F445D653713dE65C6578bE11668e7;      // 8 decimals
    address constant USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;      // 6 decimals
    address constant TREASURY = 0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac;
    address constant GUARDIAN = 0xdba7180cdd90D12B9Bc2F15080ddFD9B14fEf31a;   // TODO: rotate to multisig

    function run() external {
        address sinc = vm.envOr("SINC_ADDRESS", SINC);
        address usdc = vm.envOr("USDC_ADDRESS", USDC);
        address guardian = vm.envOr("GUARDIAN_ADDRESS", GUARDIAN);
        address treasury = vm.envOr("TREASURY_ADDRESS", TREASURY);

        require(block.chainid == 8453, "Base mainnet only");

        vm.startBroadcast();
        SincFluidAdapter adapter = new SincFluidAdapter(IERC20(sinc), IERC20(usdc), guardian, treasury);
        vm.stopBroadcast();

        console.log("SincFluidAdapter:", address(adapter));
        console.log("  guardian:", guardian);
        console.log("  treasury:", treasury);
        console.log("Next: register as strategy backer in SharedLiquidityVault 0xeA90a257e5Dae20a0472C4812775F28614459bb6");
    }
}
