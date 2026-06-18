// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Script.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {IPositionManager} from "@uniswap/v4-periphery/src/interfaces/IPositionManager.sol";
import {PositionInfo} from "@uniswap/v4-periphery/src/libraries/PositionInfoLibrary.sol";
import {Actions} from "@uniswap/v4-periphery/src/libraries/Actions.sol";
import {Planner, Plan} from "../lib/v4-periphery/test/shared/Planner.sol";

/// @notice Burn creator v4 position and send USDC + SINC to SAFE_ADDRESS.
contract ExitCreatorV4Position is Script {
    address constant POSITION_MANAGER = 0x7C5f5A4bBd8fD63184577525326123B519429bDc;
    uint256 constant TOKEN_ID = 2265866;

    function run() external {
        uint256 signerKey = vm.envUint("CREATOR_PRIVATE_KEY");
        address safe = vm.envAddress("SAFE_ADDRESS");

        IPositionManager pm = IPositionManager(POSITION_MANAGER);
        (PoolKey memory poolKey, PositionInfo info) = pm.getPoolAndPositionInfo(TOKEN_ID);
        uint256 liquidity = pm.getPositionLiquidity(TOKEN_ID);

        console.log("tokenId", TOKEN_ID);
        console.log("liquidity", liquidity);
        console.log("tickLower", info.tickLower());
        console.log("tickUpper", info.tickUpper());
        console.log("safe", safe);

        require(liquidity > 0, "position empty");

        Plan memory plan = Planner.init();
        plan = plan.add(
            Actions.BURN_POSITION,
            abi.encode(TOKEN_ID, uint128(0), uint128(0), bytes(""))
        );
        bytes memory calls = Planner.finalizeModifyLiquidityWithTakePair(plan, poolKey, safe);

        vm.startBroadcast(signerKey);
        pm.modifyLiquidities(calls, block.timestamp + 600);
        vm.stopBroadcast();
    }
}