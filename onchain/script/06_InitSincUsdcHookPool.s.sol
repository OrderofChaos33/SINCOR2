// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Script.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";
import {PoolId, PoolIdLibrary} from "@uniswap/v4-core/src/types/PoolId.sol";
import {StateLibrary} from "@uniswap/v4-core/src/libraries/StateLibrary.sol";
import {FloorPriceMath} from "../src/FloorPriceMath.sol";

/// @notice Initialize SINC/USDC v4 pool with SincLimitOrderHook at discovery price (~$0.0001/SINC).
/// @dev Signer must be any EOA with ETH for gas. Idempotent: skips if pool already initialized.
contract InitSincUsdcHookPool is Script {
    using PoolIdLibrary for PoolKey;
    using StateLibrary for IPoolManager;

    address constant SINC = 0x9C8cd8d3961F445D653713dE65C6578bE11668e7;
    address constant USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    address constant POOL_MANAGER = 0x498581fF718922c3f8e6A244956aF099B2652b2b;
    address constant HOOK = 0x8e0eE51dCa5249c9e84dbec539fDD46b375110C0;

    uint24 constant POOL_FEE = 3000;
    int24 constant TICK_SPACING = 60;

    /// @dev Discovery init so $1.50+ sell walls are out-of-range above spot.
    uint256 constant INIT_USD_PER_SINC_E18 = 1e14; // $0.0001

    function run() external {
        PoolKey memory key = _poolKey();
        PoolId id = key.toId();
        (uint160 sqrtPrice,,,) = IPoolManager(POOL_MANAGER).getSlot0(id);

        if (sqrtPrice != 0) {
            console.log("Pool already initialized; sqrtPriceX96:", sqrtPrice);
            return;
        }

        uint160 initSqrt = FloorPriceMath.sqrtPriceX96FromUsd(INIT_USD_PER_SINC_E18);
        int24 initTick = FloorPriceMath.tickFromUsd(INIT_USD_PER_SINC_E18);
        console.log("Init sqrtPriceX96:", initSqrt);
        console.log("Init tick:", initTick);

        vm.startBroadcast();
        IPoolManager(POOL_MANAGER).initialize(key, initSqrt);
        vm.stopBroadcast();

        console.log("SINC/USDC hook pool initialized");
    }

    function _poolKey() internal pure returns (PoolKey memory key) {
        key = PoolKey({
            currency0: Currency.wrap(USDC),
            currency1: Currency.wrap(SINC),
            fee: POOL_FEE,
            tickSpacing: TICK_SPACING,
            hooks: IHooks(HOOK)
        });
    }
}