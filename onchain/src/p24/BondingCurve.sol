// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title P24 BondingCurve — supply^2/16000 pricing, $69k graduation cap
/// @notice buy_price(supply) = supply^2/16000, sell_price = (supply-1)^2/16000
///         in USDC-wei per whole token. At $69,000 market cap the curve closes
///         permanently and inventory migrates to an AMM pool (one-way).
/// @dev Self-contained for bare solc 0.8.24 compilation.
contract BondingCurve {
    uint256 public constant DIVISOR = 16_000;
    uint256 public constant GRADUATION_MCAP_USDC_WEI = 69_000 * 1e6;
    uint256 public constant MAX_CURVE_SUPPLY = 500_000_000; // 50% of 1e9

    address public immutable token;
    uint256 public supply;   // whole tokens sold on-curve
    bool public closed;

    event Buy(address indexed buyer, uint256 tokens, uint256 cost);
    event Sell(address indexed seller, uint256 tokens, uint256 proceeds);
    event Graduated(uint256 supply);

    error CurveClosed();
    error BadQuantity();
    error InventoryExceeded();

    constructor(address _token) {
        token = _token;
    }

    function buyPrice(uint256 supplyTokens) public pure returns (uint256) {
        return supplyTokens * supplyTokens * 1e6 / DIVISOR;
    }

    function sellPrice(uint256 supplyTokens) public pure returns (uint256) {
        require(supplyTokens > 0, "no supply");
        uint256 s = supplyTokens - 1;
        return s * s * 1e6 / DIVISOR;
    }

    function marketCap() public view returns (uint256) {
        return supply * buyPrice(supply);
    }

    /// @notice Exact cost: sum of marginal prices over the purchased range.
    function buy(uint256 tokens) external returns (uint256 cost) {
        if (closed) revert CurveClosed();
        if (tokens == 0) revert BadQuantity();
        if (supply + tokens > MAX_CURVE_SUPPLY) revert InventoryExceeded();
        for (uint256 i = 0; i < tokens; i++) {
            cost += buyPrice(supply + i);
        }
        supply += tokens;
        if (marketCap() >= GRADUATION_MCAP_USDC_WEI) {
            closed = true; // one-way: never re-opens
            emit Graduated(supply);
        }
        emit Buy(msg.sender, tokens, cost);
    }

    function sell(uint256 tokens) external returns (uint256 proceeds) {
        if (closed) revert CurveClosed();
        if (tokens == 0 || tokens > supply) revert BadQuantity();
        for (uint256 i = 0; i < tokens; i++) {
            proceeds += sellPrice(supply - i);
        }
        supply -= tokens;
        emit Sell(msg.sender, tokens, proceeds);
    }
}
