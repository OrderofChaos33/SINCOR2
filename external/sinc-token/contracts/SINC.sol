// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/token/ERC20/extensions/ERC20Burnable.sol";
import "@openzeppelin/contracts/token/ERC20/extensions/ERC20Permit.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/**
 * @title SINC Token
 * @notice SINCOR (SINC) - Premium ERC-20 token with full interoperability
 * @dev Implements ERC-20, ERC-2612 (Permit), burnable, and ownership features
 * 
 * Features:
 * - Total Supply: 100,000,000 SINC
 * - Decimals: 18
 * - Burnable: Users can burn their tokens
 * - Permit: Gasless approvals via EIP-2612
 * - Ownable: Initial owner control for bonding curve integration
 * - Full ERC-20 interoperability with all major ecosystems
 */
contract SINC is ERC20, ERC20Burnable, ERC20Permit, Ownable {
    /// @notice Total supply of SINC tokens (100 million with 18 decimals)
    uint256 public constant TOTAL_SUPPLY = 100_000_000 * 10**18;
    
    /// @notice Bonding curve contract address (authorized minter)
    address public bondingCurve;
    
    /// @notice AMM pool address (for liquidity tracking)
    address public ammPool;
    
    /// @notice Event emitted when bonding curve is set
    event BondingCurveSet(address indexed bondingCurve);
    
    /// @notice Event emitted when AMM pool is set
    event AMMPoolSet(address indexed ammPool);
    
    /**
     * @notice Constructs the SINC token and mints initial supply to deployer
     * @param initialOwner Address to receive initial supply and ownership
     */
    constructor(address initialOwner) 
        ERC20("SINCOR", "SINC") 
        ERC20Permit("SINCOR")
        Ownable(initialOwner)
    {
        // Mint entire supply to initial owner (will be distributed via bonding curve)
        _mint(initialOwner, TOTAL_SUPPLY);
    }
    
    /**
     * @notice Sets the bonding curve contract address
     * @dev Only owner can set this, typically during initial setup
     * @param _bondingCurve Address of the bonding curve contract
     */
    function setBondingCurve(address _bondingCurve) external onlyOwner {
        require(_bondingCurve != address(0), "SINC: Invalid bonding curve address");
        bondingCurve = _bondingCurve;
        emit BondingCurveSet(_bondingCurve);
    }
    
    /**
     * @notice Sets the AMM pool address
     * @dev Only owner can set this, typically after liquidity deployment
     * @param _ammPool Address of the AMM pool (Aerodrome, Uniswap, etc.)
     */
    function setAMMPool(address _ammPool) external onlyOwner {
        require(_ammPool != address(0), "SINC: Invalid AMM pool address");
        ammPool = _ammPool;
        emit AMMPoolSet(_ammPool);
    }
    
    /**
     * @notice Returns the number of decimals used by the token
     * @return uint8 Number of decimals (18)
     */
    function decimals() public pure override returns (uint8) {
        return 18;
    }
    
    /**
     * @notice Allows owner to transfer tokens (for bonding curve/liquidity setup)
     * @dev This is a safety function for initial distribution
     * @param to Recipient address
     * @param amount Amount of tokens to transfer
     */
    function ownerTransfer(address to, uint256 amount) external onlyOwner {
        _transfer(owner(), to, amount);
    }
}
