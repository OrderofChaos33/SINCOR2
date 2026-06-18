// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Test.sol";
import {IERC721} from "@openzeppelin/contracts/token/ERC721/IERC721.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {IPositionManager} from "@uniswap/v4-periphery/src/interfaces/IPositionManager.sol";
import {Actions} from "@uniswap/v4-periphery/src/libraries/Actions.sol";
import {Planner, Plan} from "../lib/v4-periphery/test/shared/Planner.sol";

contract LiquidateTreasuryNftsTest is Test {
    address constant POSITION_MANAGER = 0x7C5f5A4bBd8fD63184577525326123B519429bDc;
    address constant TREASURY = 0xAf9B539D8043C634b7E611818518BA7E850F289e;
    address constant SAFE = 0x2d61752adF5092052Ff7D366a9884823C07Cdaf8;

    uint256[] internal tokenIds = [2439297, 2496724, 2496782, 2496822, 2496876];

    function test_burnEmptyPositions_succeeds() public {
        IPositionManager pm = IPositionManager(POSITION_MANAGER);
        vm.startPrank(TREASURY);
        for (uint256 i = 0; i < tokenIds.length; i++) {
            uint256 tokenId = tokenIds[i];
            assertEq(IERC721(address(pm)).ownerOf(tokenId), TREASURY);
            assertEq(pm.getPositionLiquidity(tokenId), 0);

            (PoolKey memory poolKey,) = pm.getPoolAndPositionInfo(tokenId);
            Plan memory plan = Planner.init();
            plan = plan.add(
                Actions.BURN_POSITION,
                abi.encode(tokenId, uint128(0), uint128(0), bytes(""))
            );
            bytes memory calls = Planner.finalizeModifyLiquidityWithTakePair(plan, poolKey, SAFE);
            pm.modifyLiquidities(calls, block.timestamp + 600);
        }
        vm.stopPrank();
    }
}