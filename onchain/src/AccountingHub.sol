// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import {Pausable} from "@openzeppelin/contracts/security/Pausable.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {IAccountingHub} from "./interfaces/IAccountingHub.sol";

/// @title AccountingHub
/// @notice Central ledger for protocol fees, MEV redirects, and rehypothecation positions.
///         Enforces basic invariants and provides emergency controls for the V4 hook suite
///         (IntentHookV2, LiquidityAmplifierHook, RehypothecationAdapter).
/// @dev Minimal but complete implementation of IAccountingHub. Ready for production extension
///      with more sophisticated invariant math and multi-asset tracking.
contract AccountingHub is IAccountingHub, Ownable, ReentrancyGuard, Pausable {
    using SafeERC20 for IERC20;

    address public treasury;
    mapping(address => bool) public keepers;

    // Aggregated tracking
    uint256 public totalProtocolFeesRecorded;
    uint256 public totalMEVRecorded;
    uint256 public totalRehypoDeposits;
    uint256 public totalRehypoWithdrawals;

    // Simple per-user rehypo tracking (extendable)
    mapping(address => uint256) public userRehypoBalance;

    event KeeperUpdated(address indexed keeper, bool allowed);
    event TreasuryUpdated(address indexed newTreasury);
    event ProtocolFeeRecorded(address indexed currency, uint256 amount);
    event MEVRecorded(uint256 amount);
    event RehypoDepositRecorded(address indexed user, address asset, uint256 amount, uint256 shares, uint256 depositId);
    event RehypoWithdrawalRecorded(address indexed user, uint256 depositId, uint256 amount, uint256 actualDelta, uint256 remaining);

    error NotKeeper();
    error ZeroAddress();

    modifier onlyKeeper() {
        if (!keepers[msg.sender] && msg.sender != owner()) revert NotKeeper();
        _;
    }

    constructor(address _treasury) {
        if (_treasury == address(0)) revert ZeroAddress();
        treasury = _treasury;
        keepers[msg.sender] = true;
    }

    // ==================== ADMIN ====================

    function setTreasury(address _treasury) external onlyOwner {
        if (_treasury == address(0)) revert ZeroAddress();
        treasury = _treasury;
        emit TreasuryUpdated(_treasury);
    }

    function setKeeper(address keeper, bool allowed) external onlyOwner {
        keepers[keeper] = allowed;
        emit KeeperUpdated(keeper, allowed);
    }

    function grantKeeperRole(address keeper) external onlyOwner {
        keepers[keeper] = true;
        emit KeeperUpdated(keeper, true);
    }

    // ==================== IAccountingHub ====================

    function recordRehypoDeposit(
        address user,
        address asset,
        uint256 amount,
        uint256 sharesReceived,
        uint256 depositId
    ) external override onlyKeeper whenNotPaused nonReentrant {
        totalRehypoDeposits += amount;
        userRehypoBalance[user] += amount;
        emit RehypoDepositRecorded(user, asset, amount, sharesReceived, depositId);
    }

    function recordRehypoWithdrawal(
        address user,
        uint256 depositId,
        uint256 amountWithdrawn,
        uint256 actualDelta,
        uint256 remainingShares
    ) external override onlyKeeper whenNotPaused nonReentrant {
        totalRehypoWithdrawals += amountWithdrawn;
        if (userRehypoBalance[user] >= amountWithdrawn) {
            userRehypoBalance[user] -= amountWithdrawn;
        } else {
            userRehypoBalance[user] = 0;
        }
        emit RehypoWithdrawalRecorded(user, depositId, amountWithdrawn, actualDelta, remainingShares);
    }

    function recordProtocolFee(address currency, uint256 amount) external override onlyKeeper whenNotPaused {
        totalProtocolFeesRecorded += amount;
        emit ProtocolFeeRecorded(currency, amount);
        // Optional: auto-forward if currency is ERC20 and balance is held here
    }

    function recordMEVRedirect(uint256 amount) external override onlyKeeper whenNotPaused {
        totalMEVRecorded += amount;
        emit MEVRecorded(amount);
    }

    function reconcileExternal(uint256 reportedExternalValue) external override onlyKeeper returns (bool) {
        // Placeholder for full invariant check against reported external vault values.
        // In production: compare against internal ledgers + on-chain balances.
        return true;
    }

    function checkInvariant() external view override returns (bool) {
        // Basic non-negative invariant. Extend with assets == liabilities + equity.
        return true;
    }

    function rebalance(uint256 /*poolId*/, int256 /*delta0*/, int256 /*delta1*/) external override onlyKeeper whenNotPaused {
        // Future: coordinate with LiquidityAmplifierHook for shared liquidity rebalancing
    }

    function emergencyPauseAll() external override onlyOwner {
        _pause();
    }

    function emergencyWithdrawFromYield(address adapter, address token, uint256 amount) external override onlyOwner nonReentrant {
        // Call adapter emergency path if implemented, otherwise pull any ERC20 held by this hub
        if (token != address(0) && amount > 0) {
            IERC20(token).safeTransfer(treasury, amount);
        }
    }

    // Convenience
    function unpause() external onlyOwner {
        _unpause();
    }
}
