// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/**
 * @title SINC AMM Router
 * @notice Simplified AMM router for SINC token with Aerodrome/Uniswap V2 compatibility
 * @dev Integrates with standard AMM pools for swaps and liquidity provision
 */
contract SINCAMMRouter is Ownable {
    using SafeERC20 for IERC20;
    
    /// @notice SINC token address
    address public immutable sincToken;
    
    /// @notice WETH address (quote token)
    address public immutable WETH;
    
    /// @notice Aerodrome/Uniswap factory address
    address public factory;
    
    /// @notice Aerodrome/Uniswap router address
    address public ammRouter;
    
    /// @notice Event emitted when liquidity is added
    event LiquidityAdded(
        address indexed provider,
        uint256 sincAmount,
        uint256 wethAmount,
        uint256 liquidity
    );
    
    /// @notice Event emitted when liquidity is removed
    event LiquidityRemoved(
        address indexed provider,
        uint256 liquidity,
        uint256 sincAmount,
        uint256 wethAmount
    );
    
    /// @notice Event emitted when tokens are swapped
    event TokensSwapped(
        address indexed user,
        address indexed tokenIn,
        address indexed tokenOut,
        uint256 amountIn,
        uint256 amountOut
    );
    
    /**
     * @notice Constructs the AMM router
     * @param _sincToken SINC token address
     * @param _weth WETH address
     * @param _factory AMM factory address
     * @param _ammRouter AMM router address
     * @param _owner Initial owner
     */
    constructor(
        address _sincToken,
        address _weth,
        address _factory,
        address _ammRouter,
        address _owner
    ) Ownable(_owner) {
        require(_sincToken != address(0), "Invalid SINC address");
        require(_weth != address(0), "Invalid WETH address");
        require(_factory != address(0), "Invalid factory address");
        require(_ammRouter != address(0), "Invalid router address");
        
        sincToken = _sincToken;
        WETH = _weth;
        factory = _factory;
        ammRouter = _ammRouter;
    }
    
    /**
     * @notice Adds liquidity to SINC/WETH pool
     * @param sincAmount Amount of SINC tokens
     * @param wethAmount Amount of WETH
     * @param minSinc Minimum SINC to add (slippage protection)
     * @param minWeth Minimum WETH to add (slippage protection)
     * @param deadline Transaction deadline
     * @return liquidity LP tokens received
     */
    function addLiquidity(
        uint256 sincAmount,
        uint256 wethAmount,
        uint256 minSinc,
        uint256 minWeth,
        uint256 deadline
    ) external returns (uint256 liquidity) {
        require(deadline >= block.timestamp, "Expired");
        
        // Transfer tokens from user
        IERC20(sincToken).safeTransferFrom(msg.sender, address(this), sincAmount);
        IERC20(WETH).safeTransferFrom(msg.sender, address(this), wethAmount);
        
        // Approve router (use forceApprove for OpenZeppelin v5)
        IERC20(sincToken).forceApprove(ammRouter, sincAmount);
        IERC20(WETH).forceApprove(ammRouter, wethAmount);
        
        // Call AMM router addLiquidity
        // This is a simplified interface - adapt for actual AMM
        (uint256 sincUsed, uint256 wethUsed, uint256 liquidityOut) = 
            IAMMRouter(ammRouter).addLiquidity(
                sincToken,
                WETH,
                sincAmount,
                wethAmount,
                minSinc,
                minWeth,
                msg.sender,
                deadline
            );
        
        // Refund unused tokens
        if (sincAmount > sincUsed) {
            IERC20(sincToken).safeTransfer(msg.sender, sincAmount - sincUsed);
        }
        if (wethAmount > wethUsed) {
            IERC20(WETH).safeTransfer(msg.sender, wethAmount - wethUsed);
        }
        
        emit LiquidityAdded(msg.sender, sincUsed, wethUsed, liquidityOut);
        return liquidityOut;
    }
    
    /**
     * @notice Swaps exact tokens for tokens
     * @param amountIn Amount of input token
     * @param amountOutMin Minimum output amount
     * @param path Swap path
     * @param deadline Transaction deadline
     * @return amounts Output amounts
     */
    function swapExactTokensForTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        address[] calldata path,
        uint256 deadline
    ) external returns (uint256[] memory amounts) {
        require(deadline >= block.timestamp, "Expired");
        require(path.length >= 2, "Invalid path");
        require(path[0] == sincToken || path[path.length - 1] == sincToken, "Must include SINC");
        
        // Transfer input token
        IERC20(path[0]).safeTransferFrom(msg.sender, address(this), amountIn);
        IERC20(path[0]).forceApprove(ammRouter, amountIn);
        
        // Execute swap via AMM router
        amounts = IAMMRouter(ammRouter).swapExactTokensForTokens(
            amountIn,
            amountOutMin,
            path,
            msg.sender,
            deadline
        );
        
        emit TokensSwapped(msg.sender, path[0], path[path.length - 1], amountIn, amounts[amounts.length - 1]);
        return amounts;
    }
    
    /**
     * @notice Gets amount out for given input
     * @param amountIn Input amount
     * @param path Swap path
     * @return amounts Expected output amounts
     */
    function getAmountsOut(uint256 amountIn, address[] calldata path) 
        external 
        view 
        returns (uint256[] memory amounts) 
    {
        return IAMMRouter(ammRouter).getAmountsOut(amountIn, path);
    }
    
    /**
     * @notice Updates AMM router address
     * @param newRouter New router address
     */
    function setAMMRouter(address newRouter) external onlyOwner {
        require(newRouter != address(0), "Invalid address");
        ammRouter = newRouter;
    }
    
    /**
     * @notice Emergency token withdrawal
     * @param token Token address
     * @param amount Amount to withdraw
     */
    function emergencyWithdraw(address token, uint256 amount) external onlyOwner {
        IERC20(token).safeTransfer(owner(), amount);
    }
}

/**
 * @notice Minimal AMM Router interface
 */
interface IAMMRouter {
    function addLiquidity(
        address tokenA,
        address tokenB,
        uint256 amountADesired,
        uint256 amountBDesired,
        uint256 amountAMin,
        uint256 amountBMin,
        address to,
        uint256 deadline
    ) external returns (uint256 amountA, uint256 amountB, uint256 liquidity);
    
    function swapExactTokensForTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        address[] calldata path,
        address to,
        uint256 deadline
    ) external returns (uint256[] memory amounts);
    
    function getAmountsOut(uint256 amountIn, address[] calldata path)
        external
        view
        returns (uint256[] memory amounts);
}
