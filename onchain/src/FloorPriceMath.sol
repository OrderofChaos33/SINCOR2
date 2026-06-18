// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {TickMath} from "@uniswap/v4-core/src/libraries/TickMath.sol";
import {FullMath} from "@uniswap/v4-core/src/libraries/FullMath.sol";
import {FixedPoint96} from "@uniswap/v4-core/src/libraries/FixedPoint96.sol";

/// @notice Tick / liquidity helpers for USDC (6 dec, currency0) / SINC (8 dec, currency1).
library FloorPriceMath {
    int24 internal constant TICK_SPACING = 60;

    /// @dev v4 price ratio token1/token0 (SINC atoms per USDC atom) for `usdPerSincE18` dollars per 1 SINC.
    function priceRatioFromUsd(uint256 usdPerSincE18) internal pure returns (uint256) {
        return FullMath.mulDiv(1e26, 1, usdPerSincE18 * 1e6);
    }

    function sqrtPriceX96FromUsd(uint256 usdPerSincE18) internal pure returns (uint160) {
        uint256 ratio = priceRatioFromUsd(usdPerSincE18);
        uint256 sqrtRatioX96 = _sqrt(FullMath.mulDiv(ratio, 1 << 192, 1));
        return uint160(sqrtRatioX96);
    }

    function tickFromUsd(uint256 usdPerSincE18) internal pure returns (int24) {
        return alignTick(TickMath.getTickAtSqrtPrice(sqrtPriceX96FromUsd(usdPerSincE18)), TICK_SPACING);
    }

    function alignTick(int24 tick, int24 spacing) internal pure returns (int24) {
        int24 compressed = tick / spacing;
        if (tick < 0 && tick % spacing != 0) compressed--;
        return compressed * spacing;
    }

    /// @dev Liquidity for single-sided SINC (token1) sell wall above spot.
    function liquidityForSincAmount(int24 tickLower, uint256 sincAmount) internal pure returns (uint128) {
        int24 tickUpper = tickLower + TICK_SPACING;
        uint160 sqrtLower = TickMath.getSqrtPriceAtTick(tickLower);
        uint160 sqrtUpper = TickMath.getSqrtPriceAtTick(tickUpper);
        uint256 liq = _liquidityForAmount1(sqrtLower, sqrtUpper, sincAmount);
        require(liq <= type(uint128).max, "liquidity overflow");
        return uint128(liq);
    }

    function _liquidityForAmount1(uint160 sqrtPriceAX96, uint160 sqrtPriceBX96, uint256 amount1)
        private
        pure
        returns (uint256)
    {
        if (sqrtPriceAX96 > sqrtPriceBX96) (sqrtPriceAX96, sqrtPriceBX96) = (sqrtPriceBX96, sqrtPriceAX96);
        return FullMath.mulDiv(amount1, FixedPoint96.Q96, sqrtPriceBX96 - sqrtPriceAX96);
    }

    function _sqrt(uint256 x) private pure returns (uint256) {
        if (x == 0) return 0;
        uint256 z = (x + 1) / 2;
        uint256 y = x;
        while (z < y) {
            y = z;
            z = (x / z + z) / 2;
        }
        return y;
    }
}