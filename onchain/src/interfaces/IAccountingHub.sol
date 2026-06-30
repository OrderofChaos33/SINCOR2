// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

/// @title IAccountingHub
/// @notice Interface for the central AccountingHub used by hardened V4 hooks and rehypo adapters.
///         Enforces the core invariant on every state change.
interface IAccountingHub {
    function recordRehypoDeposit(
        address user,
        address asset,
        uint256 amount,
        uint256 sharesReceived,
        uint256 depositId
    ) external;

    function recordRehypoWithdrawal(
        address user,
        uint256 depositId,
        uint256 amountWithdrawn,
        uint256 actualDelta,
        uint256 remainingShares
    ) external;

    function recordProtocolFee(address currency, uint256 amount) external;

    function recordMEVRedirect(uint256 amount) external;

    function reconcileExternal(uint256 reportedExternalValue) external returns (bool);

    function checkInvariant() external view returns (bool);

    // Bounded keeper/automation entrypoints
    function rebalance(uint256 poolId, int256 delta0, int256 delta1) external;

    // Emergency
    function emergencyPauseAll() external;
    function emergencyWithdrawFromYield(address adapter, address token, uint256 amount) external;
}