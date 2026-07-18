// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

/// @notice SINC price oracle: USDC (6 decimals) per whole SINC, scaled 1e6.
interface ISincPriceOracle {
    function sincPriceUSDC() external view returns (uint256);
}

/// @notice Swap router abstraction used by lending loops (V4 pool / SharedLiquidityHook route).
interface ISincSwapRouter {
    function swapUSDCForSINC(uint256 usdcIn) external returns (uint256 sincOut);
    function swapSINCForUSDC(uint256 sincIn) external returns (uint256 usdcOut);
}
