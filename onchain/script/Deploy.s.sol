// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script} from "forge-std/Script.sol";
import {console} from "forge-std/console.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";

import {SharedLiquidityVault} from "../src/SharedLiquidityVault.sol";
import {SharedLiquidityHook} from "../src/SharedLiquidityHook.sol";
import {SINCLending} from "../src/SINCLending.sol";
import {ISincPriceOracle, ISincSwapRouter} from "../src/interfaces/ISincLoop.sol";

/// @title Deploy — one-click deployment of the SINC shared-liquidity + lending stack
/// @notice Usage:
///   forge script script/Deploy.s.sol --broadcast --rpc-url $RPC_URL
///
///   Env vars:
///     PRIVATE_KEY        (required) deployer key — becomes default guardian/owner
///     SINC_TOKEN         (required) SINC ERC-20 address (decimals auto-detected; canonical
///                        SINC v3 on Base = 0x9C8cd8d3961F445D653713dE65C6578bE11668e7, 8 dp)
///     USDC_TOKEN         (required) USDC ERC-20 address (6 dp)
///                        Base:    0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
///                        Polygon: 0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359
///     POOL_MANAGER       (required for hook) Uniswap V4 PoolManager — SAME address on every
///                        chain incl. Base & Polygon:
///                        0x498581fF718922c3f8e6A244956aF099B2652b2b
///     SINC_ORACLE        (required for lending) ISincPriceOracle implementation
///     SINC_ROUTER        (required for lending) ISincSwapRouter implementation
///     GUARDIAN           (optional) defaults to deployer
///     TREASURY           (optional) defaults to canonical 0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac
///     DEPLOY_HOOK        (optional) "1" to also deploy SharedLiquidityHook
///     DEPLOY_LENDING     (optional) "1" to also deploy SINCLending
///
///   POST-DEPLOY CHECKLIST (printed at the end of the run):
///     1. HOOK ADDRESS MINING: a production SharedLiquidityHook must live at an address whose
///        permission bits encode beforeSwap (1<<7) + afterSwap (1<<6) = 0xC0. Mine the salt with
///        v4-periphery HookMiner and deploy via CREATE2 before registering pools. The direct
///        deployment here is for accounting-layer staging only.
///     2. vault.registerStrategy(hook, treasuryBacker, capSINC, capUSDC) from the guardian.
///     3. hook.registerPoolStrategy(poolKey, strategyId) from the hook owner.
///     4. LP deposits + allocateVirtual, then run a small shadow swap and confirm
///        vault.checkInvariant() == true.
///     5. Fund lending market: supplier supplyUSDC; seed router liquidity.
contract Deploy is Script {
    address constant CANONICAL_TREASURY = 0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac;

    function run() external {
        uint256 pk = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(pk);

        address sinc = vm.envAddress("SINC_TOKEN");
        address usdc = vm.envAddress("USDC_TOKEN");
        address guardian = vm.envOr("GUARDIAN", deployer);
        address treasury = vm.envOr("TREASURY", CANONICAL_TREASURY);
        bool deployHook = vm.envOr("DEPLOY_HOOK", false);
        bool deployLending = vm.envOr("DEPLOY_LENDING", false);

        vm.startBroadcast(pk);

        SharedLiquidityVault vault = new SharedLiquidityVault(IERC20(sinc), IERC20(usdc), guardian, treasury);
        console.log("SharedLiquidityVault:", address(vault));

        if (deployHook) {
            address pm = vm.envAddress("POOL_MANAGER");
            SharedLiquidityHook hook = new SharedLiquidityHook(IPoolManager(pm), vault, treasury, 10); // 0.10% protocol fee
            console.log("SharedLiquidityHook:", address(hook));
        }

        if (deployLending) {
            address oracle = vm.envAddress("SINC_ORACLE");
            address router = vm.envAddress("SINC_ROUTER");
            SINCLending lending = new SINCLending(
                IERC20(sinc), IERC20(usdc), ISincPriceOracle(oracle), ISincSwapRouter(router), guardian, treasury
            );
            console.log("SINCLending:", address(lending));
        }

        vm.stopBroadcast();

        console.log("guardian:", guardian);
        console.log("treasury:", treasury);
        console.log("NEXT: see POST-DEPLOY CHECKLIST in script/Deploy.s.sol");
    }
}
