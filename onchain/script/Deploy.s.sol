// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script} from "forge-std/Script.sol";
import {console} from "forge-std/console.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";

import {SharedLiquidityVault} from "../src/SharedLiquidityVault.sol";
import {SharedLiquidityHook} from "../src/SharedLiquidityHook.sol";
import {SINCLending} from "../src/SINCLending.sol";
import {SincPriceOracle} from "../src/SincPriceOracle.sol";
import {SincSwapRouter} from "../src/SincSwapRouter.sol";
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
///     SINC_ORACLE        (required for lending unless DEPLOY_LOOP_INFRA=1) ISincPriceOracle
///     SINC_ROUTER        (required for lending unless DEPLOY_LOOP_INFRA=1) ISincSwapRouter
///     GUARDIAN           (optional) defaults to deployer
///     TREASURY           (optional) defaults to canonical 0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac
///     DEPLOY_HOOK        (optional) "1" to also deploy SharedLiquidityHook
///     DEPLOY_LENDING     (optional) "1" to also deploy SINCLending
///     DEPLOY_LOOP_INFRA  (optional) "1" to deploy the production SincPriceOracle +
///                        SincSwapRouter and use them for lending automatically.
///                        Loop-infra env (Base defaults shown — script/deploy-base.sh exports them):
///                          SINC_CURVE      0x75dE341a2BC81806198364F125d4Cde36527619C
///                          CHAINLINK_ETH_USD 0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70
///                          WETH            0x4200000000000000000000000000000000000006
///                          AERO_ROUTER     0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43
///                          AERO_FACTORY    0x420DD381b31aEf6683db6B902084cB0FFECe40Da
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
        bool deployVault = vm.envOr("DEPLOY_VAULT", true);
        bool deployHook = vm.envOr("DEPLOY_HOOK", false);
        bool deployLending = vm.envOr("DEPLOY_LENDING", false);
        bool deployLoopInfra = vm.envOr("DEPLOY_LOOP_INFRA", false);

        vm.startBroadcast(pk);

        SharedLiquidityVault vault;
        if (deployVault) {
            vault = new SharedLiquidityVault(IERC20(sinc), IERC20(usdc), guardian, treasury);
            console.log("SharedLiquidityVault:", address(vault));
        }

        if (deployHook) {
            if (!deployVault) {
                vault = SharedLiquidityVault(vm.envAddress("VAULT"));
            }
            address pm = vm.envAddress("POOL_MANAGER");
            SharedLiquidityHook hook = new SharedLiquidityHook(IPoolManager(pm), vault, treasury, 10); // 0.10% protocol fee
            console.log("SharedLiquidityHook:", address(hook));
        }

        address oracleAddr;
        address routerAddr;

        if (deployLoopInfra) {
            address curve = vm.envAddress("SINC_CURVE");
            address clEthUsd = vm.envAddress("CHAINLINK_ETH_USD");
            address weth = vm.envAddress("WETH");
            address aeroRouter = vm.envAddress("AERO_ROUTER");
            address aeroFactory = vm.envAddress("AERO_FACTORY");

            SincPriceOracle oracleImpl = new SincPriceOracle(guardian, curve, clEthUsd, 2 hours);
            console.log("SincPriceOracle:", address(oracleImpl));
            SincSwapRouter routerImpl =
                new SincSwapRouter(sinc, usdc, weth, curve, aeroRouter, aeroFactory, oracleImpl, guardian);
            console.log("SincSwapRouter:", address(routerImpl));
            oracleAddr = address(oracleImpl);
            routerAddr = address(routerImpl);
        }

        if (deployLending) {
            // Env override wins; otherwise use the loop infra deployed above.
            address oracle = vm.envOr("SINC_ORACLE", oracleAddr);
            address router = vm.envOr("SINC_ROUTER", routerAddr);
            require(oracle != address(0) && router != address(0), "oracle/router missing");
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
