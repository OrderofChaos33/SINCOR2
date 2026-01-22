// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "@aave/v3-core/contracts/flashloan/base/FlashLoanSimpleReceiverBase.sol";
import "@aave/v3-core/contracts/interfaces/IPoolAddressesProvider.sol";
import "@aave/v3-core/contracts/interfaces/IPool.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/**
 * @title MEVLiquidator
 * @notice Atomic flash loan liquidation contract with MEV protection
 * @dev Uses Aave V3 flash loans to execute liquidations without upfront capital
 *      Designed for Flashbots bundle submission for exclusive execution
 */
contract MEVLiquidator is FlashLoanSimpleReceiverBase, Ownable {
    using SafeERC20 for IERC20;

    // Events
    event LiquidationExecuted(
        address indexed user,
        address indexed collateralAsset,
        address indexed debtAsset,
        uint256 debtRepaid,
        uint256 collateralReceived,
        uint256 profit
    );
    event ProfitWithdrawn(address indexed token, uint256 amount);
    event ExecutorUpdated(address indexed executor, bool authorized);

    // State
    mapping(address => bool) public authorizedExecutors;
    
    // Liquidation params passed through flash loan
    struct LiquidationParams {
        address collateralAsset;
        address debtAsset;
        address user;
        uint256 debtToCover;
        bool receiveAToken;
    }

    constructor(
        address _addressProvider
    ) FlashLoanSimpleReceiverBase(IPoolAddressesProvider(_addressProvider)) Ownable(msg.sender) {
        authorizedExecutors[msg.sender] = true;
    }

    modifier onlyExecutor() {
        require(authorizedExecutors[msg.sender] || msg.sender == owner(), "Not authorized");
        _;
    }

    /**
     * @notice Execute a flash loan liquidation
     * @param collateralAsset The collateral to receive
     * @param debtAsset The debt to repay
     * @param user The user to liquidate
     * @param debtToCover Amount of debt to cover
     */
    function executeLiquidation(
        address collateralAsset,
        address debtAsset,
        address user,
        uint256 debtToCover
    ) external onlyExecutor {
        // Encode liquidation params for callback
        bytes memory params = abi.encode(
            LiquidationParams({
                collateralAsset: collateralAsset,
                debtAsset: debtAsset,
                user: user,
                debtToCover: debtToCover,
                receiveAToken: false
            })
        );

        // Request flash loan for the debt amount
        POOL.flashLoanSimple(
            address(this),
            debtAsset,
            debtToCover,
            params,
            0 // referral code
        );
    }

    /**
     * @notice Callback from Aave flash loan
     */
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external override returns (bool) {
        require(msg.sender == address(POOL), "Invalid caller");
        require(initiator == address(this), "Invalid initiator");

        // Decode params
        LiquidationParams memory liqParams = abi.decode(params, (LiquidationParams));

        // Approve debt asset for liquidation
        IERC20(asset).safeApprove(address(POOL), amount);

        // Get collateral balance before
        uint256 collateralBefore = IERC20(liqParams.collateralAsset).balanceOf(address(this));

        // Execute liquidation
        POOL.liquidationCall(
            liqParams.collateralAsset,
            liqParams.debtAsset,
            liqParams.user,
            liqParams.debtToCover,
            liqParams.receiveAToken
        );

        // Calculate collateral received
        uint256 collateralReceived = IERC20(liqParams.collateralAsset).balanceOf(address(this)) - collateralBefore;

        // Swap collateral to debt asset to repay flash loan (if different)
        uint256 amountOwed = amount + premium;
        
        if (liqParams.collateralAsset != asset) {
            // Swap collateral for debt asset using DEX
            _swapForRepayment(liqParams.collateralAsset, asset, collateralReceived, amountOwed);
        }

        // Approve repayment
        IERC20(asset).safeApprove(address(POOL), amountOwed);

        // Calculate profit
        uint256 remainingDebt = IERC20(asset).balanceOf(address(this));
        uint256 profit = remainingDebt > amountOwed ? remainingDebt - amountOwed : 0;

        emit LiquidationExecuted(
            liqParams.user,
            liqParams.collateralAsset,
            liqParams.debtAsset,
            amount,
            collateralReceived,
            profit
        );

        return true;
    }

    /**
     * @notice Swap collateral for debt repayment via Uniswap V3
     */
    function _swapForRepayment(
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 minAmountOut
    ) internal {
        // Uniswap V3 SwapRouter on Base
        address SWAP_ROUTER = 0x2626664c2603336E57B271c5C0b26F421741e481;
        
        IERC20(tokenIn).safeApprove(SWAP_ROUTER, amountIn);
        
        // Single hop swap
        ISwapRouter(SWAP_ROUTER).exactInputSingle(
            ISwapRouter.ExactInputSingleParams({
                tokenIn: tokenIn,
                tokenOut: tokenOut,
                fee: 3000, // 0.3% pool
                recipient: address(this),
                deadline: block.timestamp,
                amountIn: amountIn,
                amountOutMinimum: minAmountOut,
                sqrtPriceLimitX96: 0
            })
        );
    }

    /**
     * @notice Withdraw profits
     */
    function withdrawProfits(address token) external onlyOwner {
        uint256 balance = IERC20(token).balanceOf(address(this));
        require(balance > 0, "No balance");
        IERC20(token).safeTransfer(owner(), balance);
        emit ProfitWithdrawn(token, balance);
    }

    /**
     * @notice Withdraw ETH profits
     */
    function withdrawETH() external onlyOwner {
        uint256 balance = address(this).balance;
        require(balance > 0, "No ETH");
        payable(owner()).transfer(balance);
    }

    /**
     * @notice Update authorized executors
     */
    function setExecutor(address executor, bool authorized) external onlyOwner {
        authorizedExecutors[executor] = authorized;
        emit ExecutorUpdated(executor, authorized);
    }

    /**
     * @notice Check if liquidation is profitable
     * @dev View function to simulate before executing
     */
    function checkLiquidationProfitability(
        address collateralAsset,
        address debtAsset,
        address user,
        uint256 debtToCover
    ) external view returns (bool profitable, uint256 estimatedProfit) {
        // Get liquidation bonus from protocol
        IPool pool = POOL;
        
        // This is a simplified check - actual profitability depends on:
        // 1. Liquidation bonus (typically 5-10%)
        // 2. Flash loan fee (0.09%)
        // 3. Swap slippage
        // 4. Gas costs
        
        // For now return true if position is liquidatable
        (,,,,,uint256 healthFactor) = pool.getUserAccountData(user);
        profitable = healthFactor < 1e18;
        estimatedProfit = profitable ? debtToCover * 5 / 100 : 0; // Estimate 5% bonus
    }

    receive() external payable {}
}

// Uniswap V3 interface
interface ISwapRouter {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        address recipient;
        uint256 deadline;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }
    function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut);
}
