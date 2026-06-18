// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Script.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";
import {PoolId, PoolIdLibrary} from "@uniswap/v4-core/src/types/PoolId.sol";
import {StateLibrary} from "@uniswap/v4-core/src/libraries/StateLibrary.sol";
import {SincLimitOrderHook} from "../src/SincLimitOrderHook.sol";
import {FloorPriceMath} from "../src/FloorPriceMath.sol";

/// @notice Treasury places sell-side SINC limit orders at $1.50+ via SincLimitOrderHook.
/// @dev Run 06_InitSincUsdcHookPool first. Requires TREASURY_PRIVATE_KEY in onchain/.env.
contract PlaceFloorLimitLadder is Script {
    using PoolIdLibrary for PoolKey;
    using StateLibrary for IPoolManager;

    // Set TREASURY in onchain/.env (operational signer wallet with SINC inventory).
    address constant SINC = 0x9C8cd8d3961F445D653713dE65C6578bE11668e7;
    address constant USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    address constant POOL_MANAGER = 0x498581fF718922c3f8e6A244956aF099B2652b2b;
    address constant HOOK = 0x8e0eE51dCa5249c9e84dbec539fDD46b375110C0;

    uint24 constant POOL_FEE = 3000;
    int24 constant TICK_SPACING = 60;

    struct Rung {
        uint256 usdPerSincE18;
        uint256 sincAmount; // 8-decimal atoms
    }

    function run() external {
        PoolKey memory key = _poolKey();
        PoolId id = key.toId();
        (uint160 sqrtPrice,,,) = IPoolManager(POOL_MANAGER).getSlot0(id);
        require(sqrtPrice != 0, "Pool not initialized - run 06_InitSincUsdcHookPool first");

        Rung[] memory rungs = _rungs();
        uint256 totalSinc;
        for (uint256 i = 0; i < rungs.length; i++) {
            totalSinc += rungs[i].sincAmount;
        }

        address treasury = vm.envAddress("TREASURY");
        uint256 balance = IERC20(SINC).balanceOf(treasury);
        require(balance >= totalSinc, "Insufficient treasury SINC for ladder");
        console.log("Treasury SINC balance:", balance);
        console.log("Ladder SINC total:", totalSinc);

        SincLimitOrderHook hook = SincLimitOrderHook(payable(HOOK));

        uint256 signerKey = vm.envUint("TREASURY_PRIVATE_KEY");
        require(vm.addr(signerKey) == treasury, "TREASURY_PRIVATE_KEY must sign as TREASURY");

        vm.startBroadcast(signerKey);
        // LimitOrderHook settles via safeTransferFrom(payer, poolManager) — spender is HOOK.
        IERC20(SINC).approve(HOOK, totalSinc);

        for (uint256 i = 0; i < rungs.length; i++) {
            Rung memory rung = rungs[i];
            int24 tick = FloorPriceMath.tickFromUsd(rung.usdPerSincE18);
            uint128 liquidity = FloorPriceMath.liquidityForSincAmount(tick, rung.sincAmount);

            console.log("Rung", i);
            console.log("  usdE18:", rung.usdPerSincE18);
            console.log("  sinc:", rung.sincAmount);
            console.log("  tick:", tick);
            console.log("  liquidity:", liquidity);

            require(liquidity > 0, "zero liquidity");
            hook.placeOrder(key, tick, false, liquidity);
        }

        vm.stopBroadcast();
        console.log("Floor limit ladder placed ($1.50 minimum)");
    }

    function _rungs() internal pure returns (Rung[] memory rungs) {
        rungs = new Rung[](6);
        rungs[0] = Rung({usdPerSincE18: 15e17, sincAmount: 5_000_000 * 1e8}); // $1.50 -> 5M
        rungs[1] = Rung({usdPerSincE18: 3e18, sincAmount: 4_000_000 * 1e8}); // $3.00 -> 4M
        rungs[2] = Rung({usdPerSincE18: 75e17, sincAmount: 4_000_000 * 1e8}); // $7.50 -> 4M
        rungs[3] = Rung({usdPerSincE18: 15e18, sincAmount: 3_000_000 * 1e8}); // $15 -> 3M
        rungs[4] = Rung({usdPerSincE18: 40e18, sincAmount: 2_000_000 * 1e8}); // $40 -> 2M
        rungs[5] = Rung({usdPerSincE18: 100e18, sincAmount: 2_000_000 * 1e8}); // $100 -> 2M
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