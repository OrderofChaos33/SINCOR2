// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

/// @title AgentStakeSlash
/// @notice Collateral staking and slashing for Contract-Net auction participants.
contract AgentStakeSlash is AccessControl {
    using SafeERC20 for IERC20;

    bytes32 public constant SLASHER_ROLE = keccak256("SLASHER_ROLE");

    IERC20 public immutable collateralToken;
    address public immutable treasury;
    uint256 public minStake;

    mapping(address agent => uint256) public stakeOf;

    event Staked(address indexed agent, uint256 amount);
    event Unstaked(address indexed agent, uint256 amount);
    event Slashed(address indexed agent, uint256 amount, bytes32 epochId, bytes32 epochRoot);
    event MinStakeUpdated(uint256 minStake);

    error InvalidConfig();
    error InsufficientStake();

    constructor(address admin, IERC20 token, address treasury_, uint256 minStake_) {
        if (admin == address(0) || address(token) == address(0) || treasury_ == address(0)) revert InvalidConfig();
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(SLASHER_ROLE, admin);
        collateralToken = token;
        treasury = treasury_;
        minStake = minStake_;
    }

    function setMinStake(uint256 minStake_) external onlyRole(DEFAULT_ADMIN_ROLE) {
        minStake = minStake_;
        emit MinStakeUpdated(minStake_);
    }

    function stake(uint256 amount) external {
        if (amount == 0) revert InvalidConfig();
        collateralToken.safeTransferFrom(msg.sender, address(this), amount);
        stakeOf[msg.sender] += amount;
        emit Staked(msg.sender, amount);
    }

    function unstake(uint256 amount) external {
        uint256 current = stakeOf[msg.sender];
        if (amount == 0 || amount > current) revert InsufficientStake();
        if (current - amount < minStake && current != amount) revert InsufficientStake();
        stakeOf[msg.sender] = current - amount;
        collateralToken.safeTransfer(msg.sender, amount);
        emit Unstaked(msg.sender, amount);
    }

    function canParticipate(address agent) external view returns (bool) {
        return stakeOf[agent] >= minStake;
    }

    function slash(address agent, uint256 amount, bytes32 epochId, bytes32 epochRoot) external onlyRole(SLASHER_ROLE) {
        uint256 current = stakeOf[agent];
        if (amount == 0 || amount > current) revert InsufficientStake();
        stakeOf[agent] = current - amount;
        collateralToken.safeTransfer(treasury, amount);
        emit Slashed(agent, amount, epochId, epochRoot);
    }
}
