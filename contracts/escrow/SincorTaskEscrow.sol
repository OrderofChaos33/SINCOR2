// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

contract SincorTaskEscrow is ReentrancyGuard {
    using SafeERC20 for IERC20;

    IERC20 public immutable sincorToken;
    address public treasury;

    /// @notice P04 re-auction fee: 10% (1000 bps) of the escrowed reward,
    ///         charged to treasury each time an expired task is re-auctioned.
    ///         The rate is the researched P04 list price (2026-11-16 market
    ///         research) and is UNRATIFIED — it is a built constant in an
    ///         undeployed contract, not a charged fee. See
    ///         contracts/escrow/REAUCTION_FEE_RULE.md.
    uint256 public constant REAUCTION_FEE_BPS = 1000;
    uint256 public constant BPS_DENOMINATOR = 10_000;

    enum TaskStatus { Unassigned, Claimed, Verified, Slashed }

    struct Task {
        address creator;
        address assignedAgent;
        uint256 rewardAmount;
        uint256 bondRequired;
        uint256 expiryTimestamp;
        TaskStatus status;
        uint256 timesReauctioned;
    }

    uint256 public nextTaskId;
    mapping(uint256 => Task) public tasks;

    event TaskCreated(uint256 indexed taskId, address indexed creator, uint256 rewardAmount, uint256 bondRequired, uint256 expiryTimestamp);
    event TaskClaimed(uint256 indexed taskId, address indexed agent, uint256 bondStaked);
    event TaskCompleted(uint256 indexed taskId, address indexed agent, uint256 totalPayout);
    event TaskSlashed(uint256 indexed taskId, address indexed agent, address indexed reporter, uint256 slashedBond);
    event TaskReauctioned(uint256 indexed taskId, address indexed creator, uint256 feeAmount, uint256 newRewardAmount, uint256 newExpiryTimestamp, uint256 timesReauctioned);

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
            status: TaskStatus.Unassigned,
            timesReauctioned: 0
        });

        sincorToken.safeTransferFrom(msg.sender, address(this), rewardAmount);
        emit TaskCreated(taskId, msg.sender, rewardAmount, bondRequired, block.timestamp + duration);
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

    /// @notice P04 — re-auction an expired task (the Dual-Pool re-auction
    ///         fee rule; see REAUCTION_FEE_RULE.md next to this file).
    /// @dev Creator-only recycling path for tasks that expired Unassigned
    ///      or Claimed-but-unfinished:
    ///        - a claimed task's bond is returned to the agent in full
    ///          (re-auction is recycling, not punishment — slashTask
    ///          remains the punitive path and is mutually exclusive with
    ///          this one, since both move the task out of Claimed);
    ///        - a fee of REAUCTION_FEE_BPS of the escrowed reward is paid
    ///          to treasury; the reduced reward stays escrowed;
    ///        - the task returns to Unassigned with a fresh expiry.
    ///      Conservation: the escrow balance falls by exactly
    ///      fee + returned bond; every other escrowed amount is untouched.
    ///      Repeat re-auctions charge on the already-reduced reward, so
    ///      the fee decays geometrically instead of draining the task.
    function reauctionTask(uint256 taskId, uint256 newDuration) external nonReentrant returns (uint256 feeAmount) {
        Task storage task = tasks[taskId];
        require(msg.sender == task.creator, "Only creator can re-auction");
        require(
            task.status == TaskStatus.Unassigned || task.status == TaskStatus.Claimed,
            "Task not reauctionable"
        );
        require(block.timestamp > task.expiryTimestamp, "Task not expired");
        require(newDuration > 0, "Duration must be > 0");

        if (task.status == TaskStatus.Claimed) {
            if (task.bondRequired > 0) {
                sincorToken.safeTransfer(task.assignedAgent, task.bondRequired);
            }
            task.assignedAgent = address(0);
            task.status = TaskStatus.Unassigned;
        }

        feeAmount = (task.rewardAmount * REAUCTION_FEE_BPS) / BPS_DENOMINATOR;
        if (feeAmount > 0) {
            task.rewardAmount -= feeAmount;
            sincorToken.safeTransfer(treasury, feeAmount);
        }

        task.timesReauctioned += 1;
        task.expiryTimestamp = block.timestamp + newDuration;

        emit TaskReauctioned(taskId, msg.sender, feeAmount, task.rewardAmount, task.expiryTimestamp, task.timesReauctioned);
    }
}
