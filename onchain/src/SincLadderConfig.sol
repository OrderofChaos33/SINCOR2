// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {FloorPriceMath} from "./FloorPriceMath.sol";

/// @notice Shared ladder rung definitions for SINC/USDC treasury sell walls.
library SincLadderConfig {
    struct Rung {
        uint256 usdPerSincE18;
        uint256 sincAmount; // 8-decimal atoms
    }

    /// @dev CLOSED — $1.50 floor is non-negotiable. Never place sub-floor rungs again.
    function discoveryRamp() internal pure returns (Rung[] memory rungs) {
        rungs = new Rung[](0);
    }

    /// @dev Floor ladder: $1.50 minimum public floor (matches live deployment).
    function floorLadder() internal pure returns (Rung[] memory rungs) {
        rungs = new Rung[](6);
        rungs[0] = Rung({usdPerSincE18: 15e17, sincAmount: 5_000_000 * 1e8});
        rungs[1] = Rung({usdPerSincE18: 3e18, sincAmount: 4_000_000 * 1e8});
        rungs[2] = Rung({usdPerSincE18: 75e17, sincAmount: 4_000_000 * 1e8});
        rungs[3] = Rung({usdPerSincE18: 15e18, sincAmount: 3_000_000 * 1e8});
        rungs[4] = Rung({usdPerSincE18: 40e18, sincAmount: 2_000_000 * 1e8});
        rungs[5] = Rung({usdPerSincE18: 100e18, sincAmount: 2_000_000 * 1e8});
    }

    function totalSinc(Rung[] memory rungs) internal pure returns (uint256 total) {
        for (uint256 i = 0; i < rungs.length; i++) {
            total += rungs[i].sincAmount;
        }
    }

    function liquidityForRung(Rung memory rung) internal pure returns (int24 tick, uint128 liquidity) {
        tick = FloorPriceMath.tickFromUsd(rung.usdPerSincE18);
        liquidity = FloorPriceMath.liquidityForSincAmount(tick, rung.sincAmount);
    }
}