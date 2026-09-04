// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";

import {SincConstants} from "./SincConstants.sol";

/// @title SincSovereignRevenueRouter
/// @notice Routes protocol revenue into node ops, POL, and treasury reserve, and self-funds internal tasks.
contract SincSovereignRevenueRouter is AccessControl {
    using SafeERC20 for IERC20;

    bytes32 public constant REVENUE_ROUTER_ROLE = keccak256("REVENUE_ROUTER_ROLE");

    enum InternalTaskKind {
        NODE_HEALTH_AUDIT,
        PARAMETER_TUNING,
        INDEX_COMPRESSION
    }

    struct RevenueSplit {
        uint16 nodeOpsBps;
        uint16 polBps;
        uint16 treasuryBps;
    }

    struct TreasuryThreshold {
        uint256 triggerAmount;
        uint256 taskBudget;
    }

    RevenueSplit public split;
    address public nodeOpsReceiver;
    address public polReceiver;
    address public immutable treasuryReserve;

    mapping(address token => TreasuryThreshold) public treasuryThresholdByToken;
    mapping(address token => uint256) public treasuryBalanceByToken;

    uint256 public taskNonce;

    event RevenueRouted(
        address indexed token,
        bytes32 indexed source,
        uint256 totalAmount,
        uint256 nodeOpsAmount,
        uint256 polAmount,
        uint256 treasuryAmount
    );
    event RevenueSplitUpdated(uint16 nodeOpsBps, uint16 polBps, uint16 treasuryBps);
    event ReceiversUpdated(address nodeOpsReceiver, address polReceiver);
    event TreasuryThresholdUpdated(address indexed token, uint256 triggerAmount, uint256 taskBudget);
    event InternalTaskFunded(
        bytes32 indexed taskId,
        InternalTaskKind indexed kind,
        address indexed token,
        uint256 budget,
        uint256 remainingTreasuryBalance
    );

    error InvalidConfig();

    constructor(
        address admin,
        address nodeOpsReceiver_,
        address polReceiver_,
        uint16 nodeOpsBps,
        uint16 polBps,
        uint16 treasuryBps
    ) {
        if (admin == address(0) || nodeOpsReceiver_ == address(0) || polReceiver_ == address(0)) revert InvalidConfig();

        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(REVENUE_ROUTER_ROLE, admin);

        treasuryReserve = SincConstants.TREASURY_SEED_ADDRESS;
        nodeOpsReceiver = nodeOpsReceiver_;
        polReceiver = polReceiver_;
        _setSplit(nodeOpsBps, polBps, treasuryBps);
    }

    function setRevenueSplit(uint16 nodeOpsBps, uint16 polBps, uint16 treasuryBps)
        external
        onlyRole(DEFAULT_ADMIN_ROLE)
    {
        _setSplit(nodeOpsBps, polBps, treasuryBps);
    }

    function setReceivers(address nodeOpsReceiver_, address polReceiver_) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (nodeOpsReceiver_ == address(0) || polReceiver_ == address(0)) revert InvalidConfig();
        nodeOpsReceiver = nodeOpsReceiver_;
        polReceiver = polReceiver_;
        emit ReceiversUpdated(nodeOpsReceiver_, polReceiver_);
    }

    function setTreasuryThreshold(address token, uint256 triggerAmount, uint256 taskBudget)
        external
        onlyRole(DEFAULT_ADMIN_ROLE)
    {
        if (token == address(0) || triggerAmount == 0 || taskBudget == 0 || taskBudget > triggerAmount) revert InvalidConfig();
        treasuryThresholdByToken[token] = TreasuryThreshold({triggerAmount: triggerAmount, taskBudget: taskBudget});
        emit TreasuryThresholdUpdated(token, triggerAmount, taskBudget);
    }

    function routeRevenue(address token, uint256 amount, bytes32 source)
        external
        onlyRole(REVENUE_ROUTER_ROLE)
        returns (uint256 nodeOpsAmount, uint256 polAmount, uint256 treasuryAmount)
    {
        if (token == address(0) || amount == 0) revert InvalidConfig();

        IERC20 erc20 = IERC20(token);
        erc20.safeTransferFrom(msg.sender, address(this), amount);

        nodeOpsAmount = Math.mulDiv(amount, split.nodeOpsBps, SincConstants.BPS_DENOM);
        polAmount = Math.mulDiv(amount, split.polBps, SincConstants.BPS_DENOM);
        treasuryAmount = amount - nodeOpsAmount - polAmount;

        if (nodeOpsAmount > 0) erc20.safeTransfer(nodeOpsReceiver, nodeOpsAmount);
        if (polAmount > 0) erc20.safeTransfer(polReceiver, polAmount);
        if (treasuryAmount > 0) {
            erc20.safeTransfer(treasuryReserve, treasuryAmount);
            treasuryBalanceByToken[token] += treasuryAmount;
        }

        emit RevenueRouted(token, source, amount, nodeOpsAmount, polAmount, treasuryAmount);
        _triggerInternalTaskIfNeeded(token);
    }

    function _setSplit(uint16 nodeOpsBps, uint16 polBps, uint16 treasuryBps) internal {
        if (nodeOpsBps + polBps + treasuryBps != SincConstants.BPS_DENOM) revert InvalidConfig();
        split = RevenueSplit({nodeOpsBps: nodeOpsBps, polBps: polBps, treasuryBps: treasuryBps});
        emit RevenueSplitUpdated(nodeOpsBps, polBps, treasuryBps);
    }

    function _triggerInternalTaskIfNeeded(address token) internal {
        TreasuryThreshold memory cfg = treasuryThresholdByToken[token];
        if (cfg.triggerAmount == 0 || cfg.taskBudget == 0) return;
        uint256 treasuryBalance = treasuryBalanceByToken[token];
        if (treasuryBalance < cfg.triggerAmount) return;

        treasuryBalanceByToken[token] = treasuryBalance - cfg.taskBudget;
        bytes32 taskId = keccak256(abi.encodePacked(block.chainid, token, block.number, taskNonce++));
        InternalTaskKind kind = InternalTaskKind(taskNonce % 3);

        emit InternalTaskFunded(taskId, kind, token, cfg.taskBudget, treasuryBalanceByToken[token]);
    }
}
