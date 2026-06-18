// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/console.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";
import {SincLimitOrderHook} from "../src/SincLimitOrderHook.sol";
import {SincLadderConfig} from "../src/SincLadderConfig.sol";
import {SignerScript} from "./SignerScript.sol";

/// @notice Place discovery ramp ($0.10–$0.95) on the LIVE hook at 0x8e0eE51….
/// @dev Bridges spot (~$0.0001) to the $1.50+ floor. Signer must hold 650k SINC (safe wallet).
contract PlaceDiscoveryRamp is SignerScript {
    /// @dev Safe wallet with ~985k SINC — set TREASURY + TREASURY_PRIVATE_KEY to this address in .env.
    address constant SAFE = 0x2d61752adF5092052Ff7D366a9884823C07Cdaf8;

    address constant SINC = 0x9C8cd8d3961F445D653713dE65C6578bE11668e7;
    address constant USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    address constant HOOK = 0x8e0eE51dCa5249c9e84dbec539fDD46b375110C0;

    uint24 constant POOL_FEE = 3000;
    int24 constant TICK_SPACING = 60;

    function run() external {
        revert("Discovery ramp closed - $1.50/SINC floor is non-negotiable");
        SincLadderConfig.Rung[] memory rungs = SincLadderConfig.discoveryRamp();
        uint256 totalSinc = SincLadderConfig.totalSinc(rungs);

        uint256 signerKey = _loadSignerKey();
        address treasury = vm.addr(signerKey);
        require(IERC20(SINC).balanceOf(treasury) >= totalSinc, "Insufficient SINC for discovery ramp");
        console.log("Signer / SINC source:", treasury);

        PoolKey memory key = _poolKey();
        SincLimitOrderHook hook = SincLimitOrderHook(payable(HOOK));

        console.log("Discovery ramp SINC total:", totalSinc);

        vm.startBroadcast(signerKey);
        IERC20(SINC).approve(HOOK, totalSinc);

        for (uint256 i = 0; i < rungs.length; i++) {
            (int24 tick, uint128 liquidity) = SincLadderConfig.liquidityForRung(rungs[i]);
            require(liquidity > 0, "zero liquidity");
            console.log("Rung index:", i);
            console.log("  usdE18:", rungs[i].usdPerSincE18);
            console.log("  tick:", tick);
            hook.placeOrder(key, tick, false, liquidity);
        }

        vm.stopBroadcast();
        console.log("Discovery ramp placed on live hook");
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