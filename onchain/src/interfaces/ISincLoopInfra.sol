// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

/// @notice Minimal Chainlink AggregatorV3 interface (avoids an external dependency).
interface IChainlinkAggregatorV3 {
    function decimals() external view returns (uint8);
    function latestRoundData()
        external
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound);
}

/// @notice Bonding-curve price view (wei of ETH per whole SINC).
interface ISincCurvePrice {
    function currentPriceWei() external view returns (uint256);
}

/// @notice Bonding-curve trading surface used by the swap router.
interface ISincBondingCurve is ISincCurvePrice {
    function getBuyAmount(uint256 ethIn) external view returns (uint256 sincOut);
    function getSellRefund(uint256 sincIn) external view returns (uint256 ethOut);
    function buy(uint256 ethIn, address referrer) external payable returns (uint256 sincOut);
    function sell(uint256 sincIn) external returns (uint256 ethOut);
}

/// @notice Canonical WETH (Base: 0x4200000000000000000000000000000000000006).
interface IWETH9 {
    function deposit() external payable;
    function withdraw(uint256 amount) external;
}

/// @notice Aerodrome Router (Base: 0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43).
interface IAerodromeRouter {
    struct Route {
        address from;
        address to;
        bool stable;
        address factory;
    }

    function swapExactTokensForTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        Route[] calldata routes,
        address to,
        uint256 deadline
    ) external returns (uint256[] memory amounts);

    function getAmountsOut(uint256 amountIn, Route[] calldata routes)
        external
        view
        returns (uint256[] memory amounts);
}
