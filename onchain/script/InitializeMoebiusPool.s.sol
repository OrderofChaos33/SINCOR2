// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console2} from "forge-std/Script.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

interface IMoebiusHook {
    function pToken() external view returns (address);
    function setPoolSwapFee(PoolKey calldata key, uint24 newFee) external;
    function seedLiquidity(
        PoolKey calldata key, int24 tickLower, int24 tickUpper, uint256 liquidity, uint256 maxReal, uint256 amountPmev
    ) external payable;
}

/// @title InitializeMoebiusPool
/// @notice Second (and final) activation step after DeployMoebius:
///         initializes the pMEV/<realAsset> pool, sets the swap fee (policy
///         lever #2), and seeds the redemption window (central bank balance sheet).
///
///         Env: DEPLOYER_PRIVATE_KEY, POOL_MANAGER, HOOK, REAL_ASSET (0x0 = native ETH),
///         SQRT_PRICE_X96 (1:1 = 79228162514264337593543950336), TICK_SPACING, SWAP_FEE,
///         TICK_LOWER, TICK_UPPER, LIQUIDITY, MAX_REAL, AMOUNT_PMEV
///
///         Run: forge script script/InitializeMoebiusPool.s.sol --rpc-url $BASE_RPC --broadcast
contract InitializeMoebiusPool is Script {
    uint24 internal constant DYNAMIC_FEE = 0x800000;

    struct Cfg {
        address poolManager;
        address hook;
        address realAsset;
        uint160 sqrtPriceX96;
        int24 tickSpacing;
        uint24 swapFee;
        int24 tickLower;
        int24 tickUpper;
        uint256 liquidity;
        uint256 maxReal;
        uint256 amountPmev;
    }

    function run() external returns (PoolKey memory key) {
        uint256 deployerKey = vm.envUint("DEPLOYER_PRIVATE_KEY");
        Cfg memory c = _loadCfg();

        address pToken = IMoebiusHook(c.hook).pToken();

        // Currency sorting: address(0) (native) sorts first
        bool pTokenIs0 = pToken == address(0) || (c.realAsset != address(0) && pToken < c.realAsset);
        key = PoolKey({
            currency0: Currency.wrap(pTokenIs0 ? pToken : c.realAsset),
            currency1: Currency.wrap(pTokenIs0 ? c.realAsset : pToken),
            fee: DYNAMIC_FEE,
            tickSpacing: c.tickSpacing,
            hooks: IHooks(c.hook)
        });

        vm.startBroadcast(deployerKey);

        IPoolManager(c.poolManager).initialize(key, c.sqrtPriceX96);
        IMoebiusHook(c.hook).setPoolSwapFee(key, c.swapFee);

        if (c.realAsset == address(0)) {
            IMoebiusHook(c.hook).seedLiquidity{value: c.maxReal}(
                key, c.tickLower, c.tickUpper, c.liquidity, c.maxReal, c.amountPmev
            );
        } else {
            IERC20(c.realAsset).approve(c.hook, c.maxReal);
            IMoebiusHook(c.hook).seedLiquidity(key, c.tickLower, c.tickUpper, c.liquidity, c.maxReal, c.amountPmev);
        }

        vm.stopBroadcast();

        console2.log("Pool initialized. pMEV:", pToken, "realAsset:", c.realAsset);
        console2.log("Swap fee:", c.swapFee, "| liquidity:", c.liquidity);
        console2.log("ACTIVATED. Searchers can now call executeMEV().");
    }

    function _loadCfg() internal returns (Cfg memory c) {
        c.poolManager = vm.envAddress("POOL_MANAGER");
        c.hook = vm.envAddress("HOOK");
        c.realAsset = vm.envAddress("REAL_ASSET");
        c.sqrtPriceX96 = uint160(vm.envUint("SQRT_PRICE_X96"));
        c.tickSpacing = int24(int256(vm.envInt("TICK_SPACING")));
        c.swapFee = uint24(vm.envUint("SWAP_FEE"));
        c.tickLower = int24(int256(vm.envInt("TICK_LOWER")));
        c.tickUpper = int24(int256(vm.envInt("TICK_UPPER")));
        c.liquidity = vm.envUint("LIQUIDITY");
        c.maxReal = vm.envUint("MAX_REAL");
        c.amountPmev = vm.envUint("AMOUNT_PMEV");
    }
}
