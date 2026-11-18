// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

contract SincorTaskEscrow is ReentrancyGuard {
    using SafeERC20 for IERC20;

    IERC20 public immutable sincorToken;
    address public treasury;

    enum TaskStatus { Unassigned, Claimed, Verified, Slashed, Cancelled }

    struct Task {
        address creator;
        address assignedAgent;
        uint256 rewardAmount;
        uint256 bondRequired;
        uint256 expiryTimestamp;
        TaskStatus status;
    }

    uint256 public nextTaskId;
    mapping(uint256 => Task) public tasks;

    event TaskCreated(uint256 indexed taskId, address indexed creator, uint256 rewardAmount, uint256 bondRequired, uint256 expiryTimestamp);
    event TaskClaimed(uint256 indexed taskId, address indexed agent, uint256 bondStaked);
    event TaskCompleted(uint256 indexed taskId, address indexed agent, uint256 totalPayout);
    event TaskSlashed(uint256 indexed taskId, address indexed agent, address indexed reporter, uint256 slashedBond);
    event TaskCancelled(uint256 indexed taskId, address indexed creator, uint256 refundAmount);

    constructor(address _sincorToken, address _treasury) {
        require(_sincorToken != address(0) && _treasury != address(0), "Invalid addresses");
        sincorToken = IERC20(_sincorToken);
        treasury = _treasury;
    }

    function createTask(uint256 rewardAmount, uint256 bondRequired, uint256 duration) external nonReentrant returns (uint256 taskId) {
        require(rewardAmount > 0, "Reward must be > 0");
        taskId = nextTaskId++;

        tasks[taskId] = Task({
            creator: msg.sender,
            assignedAgent: address(0),
            rewardAmount: rewardAmount,
            bondRequired: bondRequired,
            expiryTimestamp: block.timestamp + duration,
            status: TaskStatus.Unassigned
        });

        sincorToken.safeTransferFrom(msg.sender, address(this), rewardAmount);
        emit TaskCreated(taskId, msg.sender, rewardAmount, bondRequired, block.timestamp + duration);
    }

    /// @notice Cancel an unclaimed task and refund its full reward to the creator.
    /// @dev While the task is Unassigned (no agent has staked a bond yet, so
    ///      the creator is the only party with funds at stake), the creator
    ///      may cancel at any time. Once the task's expiry has passed — the
    ///      claim window closes at expiryTimestamp, since claimTask requires
    ///      block.timestamp < expiryTimestamp — ANY caller may trigger the
    ///      cancellation; the refund always goes to the task's creator,
    ///      never to the caller. This is the recovery path for tasks that
    ///      expire unclaimed — without it their rewards would be locked in
    ///      this contract permanently (W-189).
    function cancelTask(uint256 taskId) external nonReentrant {
        Task storage task = tasks[taskId];
        require(task.status == TaskStatus.Unassigned, "Task not cancellable");
        require(
            msg.sender == task.creator || block.timestamp >= task.expiryTimestamp,
            "Unauthorized cancellation"
        );

        task.status = TaskStatus.Cancelled;
        uint256 refundAmount = task.rewardAmount;
        task.rewardAmount = 0;

        sincorToken.safeTransfer(task.creator, refundAmount);
        emit TaskCancelled(taskId, task.creator, refundAmount);
    }

    function claimTask(uint256 taskId) external nonReentrant {
        Task storage task = tasks[taskId];
        require(task.status == TaskStatus.Unassigned, "Task unavailable");
        require(block.timestamp < task.expiryTimestamp, "Task expired");

        task.assignedAgent = msg.sender;
        task.status = TaskStatus.Claimed;

        if (task.bondRequired > 0) {
            sincorToken.safeTransferFrom(msg.sender, address(this), task.bondRequired);
        }

        emit TaskClaimed(taskId, msg.sender, task.bondRequired);
    }

    function completeTask(uint256 taskId) external nonReentrant {
        Task storage task = tasks[taskId];
        require(task.status == TaskStatus.Claimed, "Task not active");
        require(msg.sender == task.creator, "Unauthorized verification");

        task.status = TaskStatus.Verified;
        uint256 totalPayout = task.rewardAmount + task.bondRequired;

        sincorToken.safeTransfer(task.assignedAgent, totalPayout);
        emit TaskCompleted(taskId, task.assignedAgent, totalPayout);
    }

    function slashTask(uint256 taskId, address reporter) external nonReentrant {
        Task storage task = tasks[taskId];
        require(task.status == TaskStatus.Claimed, "Task not active");
        require(block.timestamp > task.expiryTimestamp || msg.sender == task.creator, "Slash condition not met");
        require(reporter != address(0), "Invalid reporter");

        task.status = TaskStatus.Slashed;

        sincorToken.safeTransfer(task.creator, task.rewardAmount);

        if (task.bondRequired > 0) {
            uint256 verifierShare = task.bondRequired / 2;
            uint256 treasuryShare = task.bondRequired - verifierShare;

            sincorToken.safeTransfer(reporter, verifierShare);
            sincorToken.safeTransfer(treasury, treasuryShare);

            emit TaskSlashed(taskId, task.assignedAgent, reporter, task.bondRequired);
        }
    }
}
