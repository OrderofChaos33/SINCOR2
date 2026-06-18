// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Test.sol";
import {SincLadderConfig} from "../src/SincLadderConfig.sol";

contract SincLadderConfigTest is Test {
    function test_discoveryRamp_closed() public pure {
        SincLadderConfig.Rung[] memory rungs = SincLadderConfig.discoveryRamp();
        assertEq(rungs.length, 0);
        assertEq(SincLadderConfig.totalSinc(rungs), 0);
    }

    function test_floorLadder_minimumPrice() public pure {
        SincLadderConfig.Rung[] memory rungs = SincLadderConfig.floorLadder();
        assertGt(rungs.length, 0);
        assertGe(rungs[0].usdPerSincE18, 15e17, "floor must start at $1.50");
        for (uint256 i = 0; i < rungs.length; i++) {
            (, uint128 liq) = SincLadderConfig.liquidityForRung(rungs[i]);
            assertGt(liq, 0, "zero liquidity");
        }
    }
}