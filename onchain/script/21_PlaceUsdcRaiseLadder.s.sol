// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/console.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";
import {PoolId, PoolIdLibrary} from "@uniswap/v4-core/src/types/PoolId.sol";
import {StateLibrary} from "@uniswap/v4-core/src/libraries/StateLibrary.sol";
import {SincLimitOrderHook} from "../src/SincLimitOrderHook.sol";
import {FloorPriceMath} from "../src/FloorPriceMath.sol";
import {SignerScript} from "./SignerScript.sol";

/// @notice Place $1.50+ USDC raise rungs from treasury SINC (fills -> USDC to signer).
/// @dev Set RAISE_SINC_ATOMS (8-dec) or use defaults: ~2.7k SINC ($4k), ~13.3k ($20k), ~33k ($50k).
contract PlaceUsdcRaiseLadder is SignerScript {
    using PoolIdLibrary for PoolKey;
    using StateLibrary for IPoolManager;

    address constant SINC = 0x9C8cd8d3961F445D653713dE65C6578bE11668e7;
    address constant USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    address constant POOL_MANAGER = 0x498581fF718922c3f8e6A244956aF099B2652b2b;
    address constant HOOK = 0x8e0eE51dCa5249c9e84dbec539fDD46b375110C0;

    uint24 constant POOL_FEE = 3000;
    int24 constant TICK_SPACING = 60;

    struct Rung {
        uint256 usdPerSincE18;
        uint256 sincAmount;
    }

    function run() external {
        uint256 signerKey = _loadSignerKey();
        address treasury = vm.addr(signerKey);

        PoolKey memory key = _poolKey();
        PoolId id = key.toId();
        (uint160 sqrtPrice,,,) = IPoolManager(POOL_MANAGER).getSlot0(id);
        require(sqrtPrice != 0, "Pool not initialized");

        Rung[] memory rungs = _rungs();
        uint256 totalSinc;
        for (uint256 i = 0; i < rungs.length; i++) {
            totalSinc += rungs[i].sincAmount;
        }

        uint256 balance = IERC20(SINC).balanceOf(treasury);
        require(balance >= totalSinc, "Insufficient treasury SINC");
        console.log("Treasury:", treasury);
        console.log("SINC balance:", balance);
        console.log("Placing SINC total:", totalSinc);

        SincLimitOrderHook hook = SincLimitOrderHook(payable(HOOK));

        vm.startBroadcast(signerKey);
        IERC20(SINC).approve(HOOK, totalSinc);

        for (uint256 i = 0; i < rungs.length; i++) {
            int24 tick = FloorPriceMath.tickFromUsd(rungs[i].usdPerSincE18);
            uint128 liquidity = FloorPriceMath.liquidityForSincAmount(tick, rungs[i].sincAmount);
            require(liquidity > 0, "zero liquidity");
            console.log("Rung", i);
            console.log("  usdE18:", rungs[i].usdPerSincE18);
            console.log("  sinc:", rungs[i].sincAmount);
            hook.placeOrder(key, tick, false, liquidity);
        }

        vm.stopBroadcast();
        console.log("USDC raise ladder placed at $1.50+ - USDC arrives when buyers fill rungs");
    }

    function _rungs() internal view returns (Rung[] memory rungs) {
        uint256 tranche1 = vm.envOr("RAISE_SINC_TRANCHE1", uint256(2_667 * 1e8)); // ~$4k @ $1.50
        uint256 tranche2 = vm.envOr("RAISE_SINC_TRANCHE2", uint256(13_333 * 1e8)); // ~$20k
        uint256 tranche3 = vm.envOr("RAISE_SINC_TRANCHE3", uint256(33_334 * 1e8)); // ~$50k cap
        rungs = new Rung[](3);
        rungs[0] = Rung({usdPerSincE18: 15e17, sincAmount: tranche1});
        rungs[1] = Rung({usdPerSincE18: 15e17, sincAmount: tranche2});
        rungs[2] = Rung({usdPerSincE18: 3e18, sincAmount: tranche3});
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