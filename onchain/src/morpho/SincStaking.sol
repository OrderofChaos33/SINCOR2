// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

contract SincStaking is Ownable, Pausable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    error ZeroAmount();
    error InsufficientBalance();
    error RewardTooHigh();
    error ZeroAddress();

    event Staked(address indexed user, uint256 amount);
    event Withdrawn(address indexed user, uint256 amount);
    event RewardPaid(address indexed user, uint256 reward);
    event RewardAdded(uint256 reward, uint256 duration);
    event EmergencyWithdraw(address indexed user, uint256 amount);

    IERC20 public immutable stakingToken; // SINC (8 decimals)
    IERC20 public rewardToken; // USDC recommended

    uint256 public rewardPerTokenStored;
    uint256 public lastUpdateTime;
    uint256 public rewardRate;
    uint256 public periodFinish;
    uint256 public totalSupply;

    mapping(address => uint256) public userRewardPerTokenPaid;
    mapping(address => uint256) public rewards;
    mapping(address => uint256) public balances;

    constructor(address _stakingToken, address _rewardToken, address initialOwner) Ownable(initialOwner) {
        if (_stakingToken == address(0) || _rewardToken == address(0) || initialOwner == address(0)) {
            revert ZeroAddress();
        }
        stakingToken = IERC20(_stakingToken);
        rewardToken = IERC20(_rewardToken);
    }

    modifier updateReward(address account) {
        rewardPerTokenStored = rewardPerToken();
        lastUpdateTime = lastTimeRewardApplicable();
        if (account != address(0)) {
            rewards[account] = earned(account);
            userRewardPerTokenPaid[account] = rewardPerTokenStored;
        }
        _;
    }

    function lastTimeRewardApplicable() public view returns (uint256) {
        return block.timestamp < periodFinish ? block.timestamp : periodFinish;
    }

    function rewardPerToken() public view returns (uint256) {
        if (totalSupply == 0) return rewardPerTokenStored;
        return rewardPerTokenStored
            + ((lastTimeRewardApplicable() - lastUpdateTime) * rewardRate * 1e18) / totalSupply;
    }

    function earned(address account) public view returns (uint256) {
        return (balances[account] * (rewardPerToken() - userRewardPerTokenPaid[account]) / 1e18) + rewards[account];
    }

    function stake(uint256 amount) external nonReentrant whenNotPaused updateReward(msg.sender) {
        if (amount == 0) revert ZeroAmount();
        totalSupply += amount;
        balances[msg.sender] += amount;
        stakingToken.safeTransferFrom(msg.sender, address(this), amount);
        emit Staked(msg.sender, amount);
    }

    function withdraw(uint256 amount) external nonReentrant updateReward(msg.sender) {
        if (amount == 0) revert ZeroAmount();
        if (balances[msg.sender] < amount) revert InsufficientBalance();
        totalSupply -= amount;
        balances[msg.sender] -= amount;
        stakingToken.safeTransfer(msg.sender, amount);
        emit Withdrawn(msg.sender, amount);
    }

    function claimReward() external nonReentrant updateReward(msg.sender) {
        uint256 reward = rewards[msg.sender];
        if (reward > 0) {
            rewards[msg.sender] = 0;
            rewardToken.safeTransfer(msg.sender, reward);
            emit RewardPaid(msg.sender, reward);
        }
    }

    function exit() external nonReentrant updateReward(msg.sender) {
        uint256 amount = balances[msg.sender];
        if (amount > 0) {
            totalSupply -= amount;
            balances[msg.sender] = 0;
            stakingToken.safeTransfer(msg.sender, amount);
            emit Withdrawn(msg.sender, amount);
        }
        uint256 reward = rewards[msg.sender];
        if (reward > 0) {
            rewards[msg.sender] = 0;
            rewardToken.safeTransfer(msg.sender, reward);
            emit RewardPaid(msg.sender, reward);
        }
    }

    /// @notice Emergency withdraw of staked tokens only (no rewards) when paused
    function emergencyWithdraw() external nonReentrant whenPaused {
        uint256 amount = balances[msg.sender];
        if (amount == 0) revert ZeroAmount();
        totalSupply -= amount;
        balances[msg.sender] = 0;
        rewards[msg.sender] = 0; // forfeit rewards
        stakingToken.safeTransfer(msg.sender, amount);
        emit EmergencyWithdraw(msg.sender, amount);
    }

    function notifyRewardAmount(uint256 reward, uint256 duration) external onlyOwner updateReward(address(0)) {
        if (duration == 0) revert ZeroAmount();
        if (block.timestamp >= periodFinish) {
            rewardRate = reward / duration;
        } else {
            uint256 remaining = periodFinish - block.timestamp;
            uint256 leftover = remaining * rewardRate;
            rewardRate = (reward + leftover) / duration;
        }
        // Safety: ensure we actually have the tokens
        uint256 balance = rewardToken.balanceOf(address(this));
        if (rewardRate * duration > balance) revert RewardTooHigh();

        lastUpdateTime = block.timestamp;
        periodFinish = block.timestamp + duration;
        emit RewardAdded(reward, duration);
    }

    function setRewardToken(address newRewardToken) external onlyOwner {
        if (newRewardToken == address(0)) revert ZeroAddress();
        rewardToken = IERC20(newRewardToken);
    }

    function pause() external onlyOwner {
        _pause();
    }

    function unpause() external onlyOwner {
        _unpause();
    }

    // View helpers
    function getRewardForDuration() external view returns (uint256) {
        return rewardRate * (periodFinish - lastUpdateTime);
    }
}
