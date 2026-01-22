// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

contract SINCStaking is ERC20, ReentrancyGuard, Ownable {
    mapping(address => uint256) public stakedBalance;
    mapping(address => uint256) public stakingStart;
    uint256 public rewardRate = 10; // 10% APY
    uint256 public burnRate = 1; // 1% burn

    constructor() ERC20("SINC", "SINC") {
        _mint(msg.sender, 1000000000 * 10**decimals()); // 1B supply
    }

    function stake(uint256 amount) external nonReentrant {
        require(balanceOf(msg.sender) >= amount, "Insufficient balance");
        _transfer(msg.sender, address(this), amount);
        stakedBalance[msg.sender] += amount;
        stakingStart[msg.sender] = block.timestamp;
    }

    function unstake(uint256 amount) external nonReentrant {
        require(stakedBalance[msg.sender] >= amount, "Insufficient staked");
        uint256 reward = calculateReward(msg.sender);
        stakedBalance[msg.sender] -= amount;
        _transfer(address(this), msg.sender, amount + reward);
    }

    function calculateReward(address user) public view returns (uint256) {
        uint256 timeStaked = block.timestamp - stakingStart[user];
        return (stakedBalance[user] * rewardRate * timeStaked) / (365 days * 100);
    }

    function transfer(address recipient, uint256 amount) public override returns (bool) {
        uint256 burnAmount = (amount * burnRate) / 100;
        _burn(msg.sender, burnAmount);
        return super.transfer(recipient, amount - burnAmount);
    }
}

// AI Oracle Integration (Simplified)
contract SINCOracle is Ownable {
    address public oracleAddress;
    
    function setOracle(address _oracle) external onlyOwner {
        oracleAddress = _oracle;
    }
    
    function getAIData() external view returns (uint256) {
        // Hypothetical AI data feed
        return 42; // Placeholder
    }
}