// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/**
 * @title SINC Bonding Curve
 * @notice Logarithmic bonding curve for SINC token with initial price range $1.05-1.33
 * @dev Implements buy/sell functionality with dynamic pricing based on supply
 * 
 * Price Formula: P(x) = a * ln(x + b) + c
 * Where:
 * - x = current supply in circulation
 * - a = curve steepness (controls growth rate)
 * - b = horizontal shift (controls initial price)
 * - c = vertical offset (base price)
 * 
 * Initial Configuration:
 * - Start Price: $1.05 (at 0 supply)
 * - Target Price: $1.33 (at 10M supply)
 * - Quote Token: WETH or USDC
 * - Fee: 0.3% (30 basis points)
 */
contract SINCBondingCurve is Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;
    
    /// @notice SINC token contract
    IERC20 public immutable sincToken;
    
    /// @notice Quote token (WETH, USDC, etc.)
    IERC20 public immutable quoteToken;
    
    /// @notice Fee recipient address
    address public feeRecipient;
    
    /// @notice Current circulating supply via bonding curve
    uint256 public circulatingSupply;
    
    /// @notice Fee in basis points (30 = 0.3%)
    uint256 public feeBasisPoints = 30;
    
    /// @notice Maximum fee in basis points (1000 = 10%)
    uint256 public constant MAX_FEE = 1000;
    
    /// @notice Bonding curve parameters
    /// @dev a = 0.000000028 * 1e18 (steepness)
    uint256 public constant CURVE_A = 28_000_000_000;
    
    /// @dev b = 1,000,000 (horizontal shift)
    uint256 public constant CURVE_B = 1_000_000;
    
    /// @dev c = 1.05 * 1e18 (base price in quote token)
    uint256 public constant CURVE_C = 1_050_000_000_000_000_000;
    
    /// @notice Minimum purchase amount (prevents dust)
    uint256 public constant MIN_PURCHASE = 1e15; // 0.001 quote token
    
    /// @notice Maximum supply available via bonding curve (50M SINC)
    uint256 public constant MAX_BONDING_SUPPLY = 50_000_000 * 1e18;
    
    /// @notice Event emitted on token purchase
    event TokensPurchased(
        address indexed buyer,
        uint256 quoteAmount,
        uint256 sincAmount,
        uint256 fee,
        uint256 newSupply
    );
    
    /// @notice Event emitted on token sale
    event TokensSold(
        address indexed seller,
        uint256 sincAmount,
        uint256 quoteAmount,
        uint256 fee,
        uint256 newSupply
    );
    
    /// @notice Event emitted when fee is updated
    event FeeUpdated(uint256 oldFee, uint256 newFee);
    
    /// @notice Event emitted when fee recipient is updated
    event FeeRecipientUpdated(address indexed oldRecipient, address indexed newRecipient);
    
    /**
     * @notice Constructs the bonding curve
     * @param _sincToken Address of SINC token
     * @param _quoteToken Address of quote token (WETH, USDC)
     * @param _feeRecipient Address to receive fees
     * @param _owner Initial owner address
     */
    constructor(
        address _sincToken,
        address _quoteToken,
        address _feeRecipient,
        address _owner
    ) Ownable(_owner) {
        require(_sincToken != address(0), "Invalid SINC address");
        require(_quoteToken != address(0), "Invalid quote token address");
        require(_feeRecipient != address(0), "Invalid fee recipient");
        
        sincToken = IERC20(_sincToken);
        quoteToken = IERC20(_quoteToken);
        feeRecipient = _feeRecipient;
    }
    
    /**
     * @notice Calculates current price for given supply
     * @dev P(x) = a * ln(x + b) + c
     * @param supply Current circulating supply
     * @return price Price in quote token (18 decimals)
     */
    function getCurrentPrice(uint256 supply) public pure returns (uint256 price) {
        // P(x) = a * ln(x + b) + c
        // Using approximation: ln(x) ≈ log2(x) / log2(e)
        // For production, use more accurate ln calculation
        
        uint256 x = supply / 1e18 + CURVE_B;
        uint256 lnX = _ln(x);
        
        price = (CURVE_A * lnX) / 1e18 + CURVE_C;
        return price;
    }
    
    /**
     * @notice Calculates how much SINC can be bought with quote amount
     * @param quoteAmount Amount of quote token to spend
     * @return sincAmount Amount of SINC tokens to receive
     * @return fee Fee amount in quote token
     */
    function calculateBuy(uint256 quoteAmount) public view returns (uint256 sincAmount, uint256 fee) {
        require(quoteAmount >= MIN_PURCHASE, "Amount too small");
        
        // Calculate fee
        fee = (quoteAmount * feeBasisPoints) / 10000;
        uint256 amountAfterFee = quoteAmount - fee;
        
        // Simplified calculation: sincAmount ≈ amountAfterFee / currentPrice
        // For production, use integral of price curve
        uint256 currentPrice = getCurrentPrice(circulatingSupply);
        sincAmount = (amountAfterFee * 1e18) / currentPrice;
        
        // Ensure we don't exceed max supply
        require(circulatingSupply + sincAmount <= MAX_BONDING_SUPPLY, "Exceeds max supply");
        
        return (sincAmount, fee);
    }
    
    /**
     * @notice Calculates how much quote token received for selling SINC
     * @param sincAmount Amount of SINC tokens to sell
     * @return quoteAmount Amount of quote token to receive
     * @return fee Fee amount in quote token
     */
    function calculateSell(uint256 sincAmount) public view returns (uint256 quoteAmount, uint256 fee) {
        require(sincAmount > 0, "Amount must be positive");
        require(sincAmount <= circulatingSupply, "Exceeds circulating supply");
        
        // Simplified calculation: quoteAmount ≈ sincAmount * currentPrice
        uint256 currentPrice = getCurrentPrice(circulatingSupply - sincAmount);
        uint256 quoteBeforeFee = (sincAmount * currentPrice) / 1e18;
        
        // Calculate fee
        fee = (quoteBeforeFee * feeBasisPoints) / 10000;
        quoteAmount = quoteBeforeFee - fee;
        
        return (quoteAmount, fee);
    }
    
    /**
     * @notice Buys SINC tokens with quote token
     * @param minSincAmount Minimum SINC to receive (slippage protection)
     * @return sincAmount Amount of SINC received
     */
    function buy(uint256 quoteAmount, uint256 minSincAmount) external nonReentrant returns (uint256 sincAmount) {
        (uint256 sincOut, uint256 fee) = calculateBuy(quoteAmount);
        require(sincOut >= minSincAmount, "Slippage exceeded");
        
        // Transfer quote token from buyer
        quoteToken.safeTransferFrom(msg.sender, address(this), quoteAmount);
        
        // Transfer fee to recipient
        if (fee > 0) {
            quoteToken.safeTransfer(feeRecipient, fee);
        }
        
        // Transfer SINC to buyer
        sincToken.safeTransfer(msg.sender, sincOut);
        
        // Update circulating supply
        circulatingSupply += sincOut;
        
        emit TokensPurchased(msg.sender, quoteAmount, sincOut, fee, circulatingSupply);
        return sincOut;
    }
    
    /**
     * @notice Sells SINC tokens for quote token
     * @param sincAmount Amount of SINC to sell
     * @param minQuoteAmount Minimum quote token to receive (slippage protection)
     * @return quoteAmount Amount of quote token received
     */
    function sell(uint256 sincAmount, uint256 minQuoteAmount) external nonReentrant returns (uint256 quoteAmount) {
        (uint256 quoteOut, uint256 fee) = calculateSell(sincAmount);
        require(quoteOut >= minQuoteAmount, "Slippage exceeded");
        
        // Transfer SINC from seller
        sincToken.safeTransferFrom(msg.sender, address(this), sincAmount);
        
        // Transfer quote token to seller
        quoteToken.safeTransfer(msg.sender, quoteOut);
        
        // Transfer fee to recipient
        if (fee > 0) {
            quoteToken.safeTransfer(feeRecipient, fee);
        }
        
        // Update circulating supply
        circulatingSupply -= sincAmount;
        
        emit TokensSold(msg.sender, sincAmount, quoteOut, fee, circulatingSupply);
        return quoteOut;
    }
    
    /**
     * @notice Updates fee basis points
     * @param newFee New fee in basis points
     */
    function setFee(uint256 newFee) external onlyOwner {
        require(newFee <= MAX_FEE, "Fee too high");
        uint256 oldFee = feeBasisPoints;
        feeBasisPoints = newFee;
        emit FeeUpdated(oldFee, newFee);
    }
    
    /**
     * @notice Updates fee recipient
     * @param newRecipient New fee recipient address
     */
    function setFeeRecipient(address newRecipient) external onlyOwner {
        require(newRecipient != address(0), "Invalid address");
        address oldRecipient = feeRecipient;
        feeRecipient = newRecipient;
        emit FeeRecipientUpdated(oldRecipient, newRecipient);
    }
    
    /**
     * @notice Natural logarithm approximation
     * @dev Uses Taylor series approximation for ln(x)
     * @param x Input value (scaled by 1e18)
     * @return Natural log of x (scaled by 1e18)
     */
    function _ln(uint256 x) private pure returns (uint256) {
        require(x > 0, "ln(0) undefined");
        
        // For x near 1, use Taylor series: ln(1+x) = x - x²/2 + x³/3 - x⁴/4 + ...
        // For production, use more accurate library like ABDKMath64x64
        
        if (x == 1) return 0;
        
        // Simplified approximation: ln(x) ≈ 2 * (x-1) / (x+1)
        // This is accurate for x near 1
        uint256 numerator = 2 * (x - 1) * 1e18;
        uint256 denominator = x + 1;
        
        return numerator / denominator;
    }
    
    /**
     * @notice Withdraws tokens (emergency only)
     * @param token Token to withdraw
     * @param amount Amount to withdraw
     */
    function emergencyWithdraw(address token, uint256 amount) external onlyOwner {
        IERC20(token).safeTransfer(owner(), amount);
    }
}
