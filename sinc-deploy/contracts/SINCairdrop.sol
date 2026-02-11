// SINC Airdrop Smart Contract - Base Network
// Ready to Deploy to Base Mainnet
// Handles 1% marketing token allocation for Phase 1 airdrop

pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/**
 * @title SINC Airdrop Manager
 * @notice Manages distribution of 1% marketing token allocation
 * First 500 verified members receive 1,000 SINC each
 */
contract SINCairdrop is Ownable, ReentrancyGuard {
    
    // ============ STATE VARIABLES ============
    
    IERC20 public sincToken;
    
    uint256 public constant TOKENS_PER_CLAIM = 1000 ether; // 1,000 SINC with 18 decimals
    uint256 public constant MAX_CLAIMS = 500; // Only first 500 can claim
    uint256 public constant AIRDROP_START = 1738464000; // Feb 2, 2026 00:00 UTC
    uint256 public constant AIRDROP_END = 1738982400; // Feb 8, 2026 23:59 UTC
    
    uint256 public totalClaimed = 0;
    
    // Mapping: wallet address => has claimed
    mapping(address => bool) public hasClaimed;
    
    // Mapping: wallet address => verified by team
    mapping(address => bool) public isVerified;
    
    // Array to track all claimants
    address[] public claimants;
    
    // ============ EVENTS ============
    
    event AirdropClaimed(address indexed recipient, uint256 amount, uint256 timestamp);
    event WalletVerified(address indexed wallet, uint256 timestamp);
    event ClaimWindowUpdated(uint256 newStart, uint256 newEnd);
    
    // ============ CONSTRUCTOR ============
    
    constructor(address _sincTokenAddress) Ownable(msg.sender) {
        require(_sincTokenAddress != address(0), "Invalid token address");
        sincToken = IERC20(_sincTokenAddress);
    }
    
    // ============ VERIFICATION & CLAIMING ============
    
    /**
     * @notice Owner verifies wallet addresses for airdrop eligibility
     * @param _wallets Array of wallet addresses to verify
     */
    function verifyWallets(address[] calldata _wallets) external onlyOwner {
        for (uint256 i = 0; i < _wallets.length; i++) {
            require(_wallets[i] != address(0), "Invalid wallet");
            isVerified[_wallets[i]] = true;
            emit WalletVerified(_wallets[i], block.timestamp);
        }
    }
    
    /**
     * @notice Single wallet verification
     * @param _wallet Wallet address to verify
     */
    function verifyWallet(address _wallet) external onlyOwner {
        require(_wallet != address(0), "Invalid wallet");
        isVerified[_wallet] = true;
        emit WalletVerified(_wallet, block.timestamp);
    }
    
    /**
     * @notice Claim airdrop - called by verified participant
     * Requirements:
     * - Must be verified by team
     * - Must not have already claimed
     * - Must be within claim window (Feb 2-8)
     * - Must be within first 500 claims
     */
    function claimAirdrop() external nonReentrant {
        require(isVerified[msg.sender], "Wallet not verified for airdrop");
        require(!hasClaimed[msg.sender], "Already claimed airdrop");
        require(block.timestamp >= AIRDROP_START, "Airdrop not started");
        require(block.timestamp <= AIRDROP_END, "Airdrop period ended");
        require(totalClaimed < MAX_CLAIMS, "Airdrop limit reached");
        
        // Mark as claimed
        hasClaimed[msg.sender] = true;
        totalClaimed++;
        claimants.push(msg.sender);
        
        // Transfer tokens
        require(
            sincToken.transfer(msg.sender, TOKENS_PER_CLAIM),
            "Token transfer failed"
        );
        
        emit AirdropClaimed(msg.sender, TOKENS_PER_CLAIM, block.timestamp);
    }
    
    // ============ VIEW FUNCTIONS ============
    
    /**
     * @notice Check if wallet can claim
     */
    function canClaim(address _wallet) external view returns (bool) {
        return isVerified[_wallet] && 
               !hasClaimed[_wallet] && 
               block.timestamp >= AIRDROP_START &&
               block.timestamp <= AIRDROP_END &&
               totalClaimed < MAX_CLAIMS;
    }
    
    /**
     * @notice Get total claims made
     */
    function getTotalClaims() external view returns (uint256) {
        return totalClaimed;
    }
    
    /**
     * @notice Get remaining claimable spots
     */
    function getRemainingClaims() external view returns (uint256) {
        return MAX_CLAIMS - totalClaimed;
    }
    
    /**
     * @notice Get all claimants (for analytics)
     */
    function getClaimants() external view returns (address[] memory) {
        return claimants;
    }
    
    /**
     * @notice Get claimant count
     */
    function getClaimantCount() external view returns (uint256) {
        return claimants.length;
    }
    
    /**
     * @notice Check if airdrop period is active
     */
    function isAirdropActive() external view returns (bool) {
        return block.timestamp >= AIRDROP_START && 
               block.timestamp <= AIRDROP_END;
    }
    
    // ============ ADMIN FUNCTIONS ============
    
    /**
     * @notice Emergency: recover unclaimed tokens after airdrop ends
     * @param _recipient Address to send recovered tokens
     */
    function recoverUnclaimedTokens(address _recipient) external onlyOwner {
        require(block.timestamp > AIRDROP_END, "Airdrop still active");
        require(_recipient != address(0), "Invalid recipient");
        
        uint256 balance = sincToken.balanceOf(address(this));
        require(balance > 0, "No tokens to recover");
        
        require(sincToken.transfer(_recipient, balance), "Transfer failed");
    }
    
    /**
     * @notice Update claim window (for testing/emergency)
     */
    function updateClaimWindow(uint256 _newStart, uint256 _newEnd) external onlyOwner {
        require(_newStart < _newEnd, "Invalid window");
        // Commented out to use constants, but kept for reference
        // AIRDROP_START = _newStart;
        // AIRDROP_END = _newEnd;
        emit ClaimWindowUpdated(_newStart, _newEnd);
    }
    
    /**
     * @notice Emergency pause - allow owner to revoke verification
     */
    function revokeVerification(address _wallet) external onlyOwner {
        isVerified[_wallet] = false;
    }
    
    /**
     * @notice Batch revoke verification
     */
    function revokeVerifications(address[] calldata _wallets) external onlyOwner {
        for (uint256 i = 0; i < _wallets.length; i++) {
            isVerified[_wallets[i]] = false;
        }
    }
}
