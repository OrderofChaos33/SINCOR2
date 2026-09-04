// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

import {EpochSessionKeyValidator} from "./EpochSessionKeyValidator.sol";
import {AgentStakeSlash} from "./AgentStakeSlash.sol";

/// @title MemoryBoundSettlement
/// @notice Settlement gate that verifies epoch-root integrity before payout.
contract MemoryBoundSettlement is AccessControl {
    using SafeERC20 for IERC20;

    bytes32 public constant SETTLER_ROLE = keccak256("SETTLER_ROLE");

    EpochSessionKeyValidator public immutable validator;
    AgentStakeSlash public immutable stakeSlash;
    IERC20 public immutable payoutToken;

    mapping(bytes32 taskId => bool) public settled;

    event TaskSettled(bytes32 indexed taskId, address indexed agent, uint256 amount, bytes32 epochId, bytes32 epochRoot);
    event InvalidProofSlashed(bytes32 indexed taskId, address indexed agent, uint256 slashAmount, bytes32 epochId, bytes32 epochRoot);

    error InvalidConfig();
    error AlreadySettled();
    error AgentNotEligible();

    constructor(address admin, EpochSessionKeyValidator validator_, AgentStakeSlash stakeSlash_, IERC20 payoutToken_) {
        if (admin == address(0) || address(validator_) == address(0) || address(stakeSlash_) == address(0) || address(payoutToken_) == address(0)) {
            revert InvalidConfig();
        }
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(SETTLER_ROLE, admin);
        validator = validator_;
        stakeSlash = stakeSlash_;
        payoutToken = payoutToken_;
    }

    function settleTask(
        bytes32 taskId,
        address agent,
        uint256 amount,
        bytes32 epochId,
        bytes32 epochRoot
    ) external onlyRole(SETTLER_ROLE) {
        if (settled[taskId]) revert AlreadySettled();
        if (!stakeSlash.canParticipate(agent)) revert AgentNotEligible();
        validator.validateExecutionProof(epochId, epochRoot);
        settled[taskId] = true;
        payoutToken.safeTransfer(agent, amount);
        emit TaskSettled(taskId, agent, amount, epochId, epochRoot);
    }

    function slashInvalidProof(
        bytes32 taskId,
        address agent,
        uint256 slashAmount,
        bytes32 epochId,
        bytes32 epochRoot
    ) external onlyRole(SETTLER_ROLE) {
        if (settled[taskId]) revert AlreadySettled();
        // If validator call reverts, proof is invalid and caller should slash.
        try validator.validateExecutionProof(epochId, epochRoot) returns (bool ok) {
            if (ok) revert InvalidConfig();
        } catch {
            stakeSlash.slash(agent, slashAmount, epochId, epochRoot);
            emit InvalidProofSlashed(taskId, agent, slashAmount, epochId, epochRoot);
            return;
        }
    }
}
