// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import "forge-std/Script.sol";
import {PhantomCreditToken} from "../src/hooks/PhantomCreditToken.sol";
import {MoebiusMEVHook} from "../src/hooks/MoebiusMEVHook.sol";
import {LiquidityAmplifierHook} from "../src/LiquidityAmplifierHook.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";

/// @title DeployProfitHooks
/// @notice One-command deployment for the highest-profit missing hooks.
///         Deploys in correct dependency order and logs everything.
///
/// Usage (1-click):
///   forge script script/DeployProfitHooks.s.sol \
///     --rpc-url $BASE_RPC_URL \
///     --broadcast \
///     --verify \
///     -vvvv
///
/// After deploy: Update CANONICAL_ADDRESSES.md with the new addresses.
contract DeployProfitHooks is Script {
    function run() external {
        uint256 deployerKey = vm.envUint("DEPLOYER_PRIVATE_KEY");
        address poolManager = vm.envAddress("POOL_MANAGER_ADDRESS");
        address guardian = vm.envAddress("GUARDIAN_ADDRESS");
        address treasury = vm.envAddress("TREASURY_ADDRESS");

        vm.startBroadcast(deployerKey);

        // 1. Deploy PhantomCreditToken (pToken) - required by MoebiusMEVHook
        PhantomCreditToken pToken = new PhantomCreditToken(address(0));
        console.log("1. PhantomCreditToken deployed at:", address(pToken));

        // 2. Deploy MoebiusMEVHook (main MEV revenue engine)
        MoebiusMEVHook moebius = new MoebiusMEVHook(
            IPoolManager(poolManager),
            address(pToken),
            treasury
        );
        console.log("2. MoebiusMEVHook deployed at:", address(moebius));

        // 3. Deploy LiquidityAmplifierHook (capital efficiency + yield)
        LiquidityAmplifierHook amplifier = new LiquidityAmplifierHook(
            IPoolManager(poolManager),
            guardian
        );
        console.log("3. LiquidityAmplifierHook deployed at:", address(amplifier));

        vm.stopBroadcast();

        console.log("
=== DEPLOYMENT COMPLETE ===");
        console.log("Next steps:");
        console.log("- Update CANONICAL_ADDRESSES.md with the addresses above");
        console.log("- Call setYieldVault() on LiquidityAmplifierHook for target pools");
        console.log("- Initialize MoebiusMEVHook if needed via guardian/owner functions");
        console.log("- Run tests/fork tests before mainnet traffic");
    }
}
