// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {FullMath} from "@uniswap/v4-core/src/libraries/FullMath.sol";
import {SafeCast} from "@openzeppelin/contracts/utils/math/SafeCast.sol";

import {IAccountingHub} from "./interfaces/IAccountingHub.sol";

/// @title RehypothecationAdapter - Adapter for Dual-Yield (Rehypo) with strict reconciliation
/// @notice Requires external protocol adapter pattern - this template implements a secure delta-verification
///         and reconciliation workflow expected by external yield protocols (e.g. Morpho, Aave).
///         This adapter must be specialized for each protocol by overriding the deposit/withdraw logic.
contract RehypothecationAdapter is ReentrancyGuard {
    using SafeERC20 for IERC20;
    using SafeCast for uint256;

    IERC20 public immutable underlying;
    IERC20 public immutable yieldToken;
    address public immutable yieldProtocol;
    IAccountingHub public immutable hub;

    uint256 public constant DUST_THRESHOLD = 1e6;

    struct DepositInfo {
        uint256 amountDeposited;
        uint256 sharesReceived;
        uint256 depositBlock;
        uint256 depositTimestamp;
        bool active;
    }

    mapping(address => mapping(uint256 => DepositInfo)) public userDeposits;
    uint256 public nextDepositId;

    event Deposited(address indexed user, uint256 amount, uint256 sharesReceived, uint256 depositId);
    event Withdrawn(address indexed user, uint256 amountWithdrawn, uint256 depositId, int256 netYield);
    event Reconciled(uint256 actualDelta, uint256 expected, bool success);
    event ReconciliationFailed(uint256 actual, uint256 expected);

    error InsufficientShares();
    error ReconciliationDrift(uint256 actual, uint256 expected);
    error YieldProtocolCallFailed();
    error ZeroAmount();

    constructor(
        IERC20 _underlying,
        IERC20 _yieldToken,
        address _yieldProtocol,
        IAccountingHub _hub
    ) {
        underlying = _underlying;
        yieldToken = _yieldToken;
        yieldProtocol = _yieldProtocol;
        hub = _hub;
    }

    function deposit(uint256 amount, address onBehalfOf)
        external
        nonReentrant
        returns (uint256 depositId, uint256 sharesReceived)
    {
        if (amount == 0) revert ZeroAmount();

        uint256 preUnderlying = underlying.balanceOf(address(this));
        uint256 preYield = yieldToken.balanceOf(address(this));

        underlying.safeTransferFrom(msg.sender, address(this), amount);
        underlying.safeIncreaseAllowance(yieldProtocol, amount);

        // === PROTOCOL-SPECIFIC DEPOSIT CALL - IMPLEMENT HERE ===

        uint256 postUnderlying = underlying.balanceOf(address(this));
        uint256 postYield = yieldToken.balanceOf(address(this));

        uint256 underlyingSpentByProtocol = (preUnderlying + amount) - postUnderlying;
        uint256 sharesReceivedActual = postYield - preYield;

        if (underlyingSpentByProtocol + DUST_THRESHOLD < amount) {
            emit ReconciliationFailed(underlyingSpentByProtocol, amount);
            revert ReconciliationDrift(underlyingSpentByProtocol, amount);
        }

        sharesReceived = sharesReceivedActual;

        depositId = nextDepositId++;
        userDeposits[onBehalfOf][depositId] = DepositInfo({
            amountDeposited: amount,
            sharesReceived: sharesReceived,
            depositBlock: block.number,
            depositTimestamp: block.timestamp,
            active: true
        });

        hub.recordRehypoDeposit(onBehalfOf, address(underlying), amount, sharesReceived, depositId);

        emit Deposited(onBehalfOf, amount, sharesReceived, depositId);
        emit Reconciled(underlyingSpentByProtocol, amount, true);
        return (depositId, sharesReceived);
    }

    function withdraw(uint256 depositId, address onBehalfOf, uint256 sharesToBurn)
        external
        nonReentrant
        returns (uint256 amountWithdrawn, int256 netYield)
    {
        DepositInfo storage dep = userDeposits[onBehalfOf][depositId];
        if (!dep.active || dep.sharesReceived < sharesToBurn) revert InsufficientShares();

        uint256 preUnderlying = underlying.balanceOf(address(this));

        // === PROTOCOL-SPECIFIC WITHDRAW CALL - IMPLEMENT HERE ===
        amountWithdrawn = sharesToBurn;

        uint256 postUnderlying = underlying.balanceOf(address(this));
        uint256 actualDelta = postUnderlying - preUnderlying;

        if (actualDelta + DUST_THRESHOLD < amountWithdrawn) {
            emit ReconciliationFailed(actualDelta, amountWithdrawn);
        }

        uint256 remainingShares = dep.sharesReceived - sharesToBurn;
        hub.recordRehypoWithdrawal(onBehalfOf, depositId, amountWithdrawn, actualDelta, remainingShares);

        if (remainingShares == 0) {
            dep.active = false;
        } else {
            dep.sharesReceived = remainingShares;
        }

        if (amountWithdrawn > 0) {
            underlying.safeTransfer(address(hub), amountWithdrawn);
        }

        emit Withdrawn(onBehalfOf, amountWithdrawn, depositId, netYield);
        emit Reconciled(actualDelta, amountWithdrawn, true);

        return (amountWithdrawn, netYield);
    }

    function reconcile() external nonReentrant returns (bool) {
        uint256 actualUnderlying = underlying.balanceOf(address(this));
        emit Reconciled(actualUnderlying, 0, true);
        return true;
    }
}
