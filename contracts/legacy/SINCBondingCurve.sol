// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * SINC Bonding Curve
 * - Starting price: ~$1.09 per SINC (0.00047 ETH at ~$2,319/ETH)
 * - Price rises gently as tokens are bought
 * - Sell back at the curve price
 * - No owner, no admin, no backdoors
 * - Treasury can withdraw ETH and unsold SINC
 */

interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
    function approve(address spender, uint256 amount) external returns (bool);
}

contract SINCBondingCurve {
    IERC20 public immutable sincToken;
    address public immutable treasury;

    // Base price: 0.00047 ETH per SINC ≈ $1.09 at $2,319/ETH
    uint256 public constant BASE_PRICE = 470000000000000;

    // Price increment per token sold: 0.00000000047 ETH
    // After 100k tokens sold: price ≈ $1.20
    // After 1M tokens sold: price ≈ $2.18 (doubles)
    uint256 public constant PRICE_INCREMENT = 470000000;

    // How many tokens have been sold through this curve
    uint256 public tokensSold;

    event Buy(address indexed buyer, uint256 amount, uint256 cost);
    event Sell(address indexed seller, uint256 amount, uint256 refund);
    event WithdrawETH(uint256 amount);

    constructor(address _token, address _treasury) {
        sincToken = IERC20(_token);
        treasury = _treasury;
    }

    /// @notice Current price per SINC token in wei
    function currentPrice() public view returns (uint256) {
        return BASE_PRICE + (PRICE_INCREMENT * tokensSold);
    }

    /// @notice Calculate ETH cost to buy `amount` SINC tokens
    function getBuyCost(uint256 amount) public view returns (uint256) {
        // Sum of linear prices: amount * BASE_PRICE + PRICE_INCREMENT * (amount * tokensSold + amount*(amount-1)/2)
        return amount * BASE_PRICE
            + PRICE_INCREMENT * (amount * tokensSold + amount * (amount - 1) / 2);
    }

    /// @notice Calculate ETH refund for selling `amount` SINC tokens
    function getSellRefund(uint256 amount) public view returns (uint256) {
        require(amount <= tokensSold, "Exceeds sold");
        uint256 newSold = tokensSold - amount;
        return amount * BASE_PRICE
            + PRICE_INCREMENT * (amount * newSold + amount * (amount - 1) / 2);
    }

    /// @notice Buy SINC tokens with ETH
    function buy(uint256 amount) external payable {
        require(amount > 0, "Zero amount");
        uint256 cost = getBuyCost(amount);
        require(msg.value >= cost, "Insufficient ETH");
        require(sincToken.balanceOf(address(this)) >= amount, "Not enough SINC");

        tokensSold += amount;

        require(sincToken.transfer(msg.sender, amount), "Transfer failed");

        // Refund excess ETH
        if (msg.value > cost) {
            (bool ok, ) = msg.sender.call{value: msg.value - cost}("");
            require(ok, "Refund failed");
        }

        emit Buy(msg.sender, amount, cost);
    }

    /// @notice Sell SINC tokens back for ETH (must approve this contract first)
    function sell(uint256 amount) external {
        require(amount > 0 && amount <= tokensSold, "Invalid amount");
        uint256 refund = getSellRefund(amount);
        require(address(this).balance >= refund, "Insufficient reserve");

        tokensSold -= amount;

        require(sincToken.transferFrom(msg.sender, address(this), amount), "TransferFrom failed");

        (bool ok, ) = msg.sender.call{value: refund}("");
        require(ok, "ETH transfer failed");

        emit Sell(msg.sender, amount, refund);
    }

    /// @notice Treasury can withdraw unsold SINC tokens
    function withdrawUnsoldTokens(uint256 amount) external {
        require(msg.sender == treasury, "Not treasury");
        require(amount <= sincToken.balanceOf(address(this)), "Exceeds balance");
        require(sincToken.transfer(treasury, amount), "Transfer failed");
    }

    /// @notice Treasury can withdraw ETH
    function withdrawETH(uint256 amount) external {
        require(msg.sender == treasury, "Not treasury");
        require(amount <= address(this).balance, "Exceeds balance");
        (bool ok, ) = treasury.call{value: amount}("");
        require(ok, "ETH transfer failed");
        emit WithdrawETH(amount);
    }

    /// @notice View reserve ETH backing sold tokens
    function reserveBalance() external view returns (uint256) {
        return address(this).balance;
    }

    /// @notice View available SINC tokens for sale
    function availableTokens() external view returns (uint256) {
        return sincToken.balanceOf(address(this));
    }

    receive() external payable {}
}
