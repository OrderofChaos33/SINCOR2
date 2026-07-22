// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console2} from "forge-std/Script.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {TickMath} from "v4-core/src/libraries/TickMath.sol";
import {MoebiusMEVHook} from "../src/hooks/MoebiusMEVHook.sol";

/// @title InitializeMoebiusPool — bring up a pMEV/realAsset dynamic-fee pool
/// @notice Steps, in order, against an already-deployed MoebiusMEVHook + pMEV:
///
///           1. poolManager.initialize(key, sqrtPriceX96) with the dynamic-fee
///              flag (0x800000) and hooks = the Moebius hook.
///           2. hook.setPoolSwapFee(key, fee) — policy lever #2.
///           3. hook.seedLiquidity(...) — the central-bank desk gives pMEV a price.
///
///         Env:
///           PRIVATE_KEY       owner key (must be hook owner for steps 2-3)
///           POOL_MANAGER      v4 PoolManager
///           MOEBIUS_HOOK      deployed MoebiusMEVHook
///           PMEV_TOKEN        deployed PhantomCreditToken
///           REAL_ASSET        real asset (address(0) for native ETH)
///           POOL_FEE          initial dynamic fee (e.g. 3000 = 0.3%)
///           TICK_SPACING      e.g. 60
///           SQRT_PRICE_X96    initial price
///           SEED_LIQUIDITY    v4 liquidity delta for the seed position
///           SEED_MAX_REAL     max real asset to escrow for the seed
///           SEED_PMEV         pMEV to mint for the seed
contract InitializeMoebiusPool is Script {
    uint24 internal constant DYNAMIC_FEE_FLAG = 0x800000;

    function run() external {
        address poolManager = vm.envAddress("POOL_MANAGER");
        MoebiusMEVHook hook = MoebiusMEVHook(vm.envAddress("MOEBIUS_HOOK"));
        address pmev = vm.envAddress("PMEV_TOKEN");
        address real = vm.envAddress("REAL_ASSET");
        uint24 fee = uint24(vm.envUint("POOL_FEE"));
        int24 spacing = int24(int256(vm.envUint("TICK_SPACING")));
        uint160 sqrtPrice = uint160(vm.envUint("SQRT_PRICE_X96"));
        uint256 seedLiq = vm.envUint("SEED_LIQUIDITY");
        uint256 seedMaxReal = vm.envUint("SEED_MAX_REAL");
        uint256 seedPmev = vm.envUint("SEED_PMEV");
        uint256 key = vm.envUint("PRIVATE_KEY");

        // currency0 must sort below currency1 (native = address(0) sorts lowest)
        (Currency c0, Currency c1) = real == address(0) || real < pmev
            ? (Currency.wrap(real), Currency.wrap(pmev))
            : (Currency.wrap(pmev), Currency.wrap(real));

        PoolKey memory poolKey = PoolKey({
            currency0: c0,
            currency1: c1,
            fee: DYNAMIC_FEE_FLAG,
            tickSpacing: spacing,
            hooks: IHooks(address(hook))
        });

        vm.startBroadcast(key);

        IPoolManager(poolManager).initialize(poolKey, sqrtPrice);
        hook.setPoolSwapFee(poolKey, fee);

        int24 tickLower = (TickMath.MIN_TICK / spacing) * spacing;
        int24 tickUpper = (TickMath.MAX_TICK / spacing) * spacing;
        hook.seedLiquidity(poolKey, tickLower, tickUpper, seedLiq, seedMaxReal, seedPmev);

        vm.stopBroadcast();

        console2.log("pMEV pool initialized + seeded");
    }
}
