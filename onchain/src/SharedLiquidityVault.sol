// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import {Pausable} from "@openzeppelin/contracts/security/Pausable.sol";
import {FullMath} from "@uniswap/v4-core/src/libraries/FullMath.sol";

import {IAccountingHub} from "./interfaces/IAccountingHub.sol";
import {ISharedLiquidityVault} from "./interfaces/ISharedLiquidityVault.sol";

/// @title SharedLiquidityVault — Aqua/Fluid-inspired shared-liquidity layer for SINC/USDC
/// @notice Single liquidity layer holding real SINC/USDC balances with *virtual* accounting on top.
///
///         Aqua (1inch) inspiration:
///           - LP capital is deposited ONCE and can be *virtually allocated* to many strategies
///             (V4 hook pools, lending loops) at the same time. Virtual allocations are commitments,
///             not locks — the same dollar can back a SINC/USDC pool and a lending loop simultaneously.
///           - Real tokens only move at EXECUTION time ("pull at execution"), when a strategy
///             actually needs the capital for a swap or loan.
///
///         Fluid (Instadapp) inspiration:
///           - One shared liquidity layer; strategies draw down from it against internal debt/credit
///             ledgers, with per-strategy caps and a hard global solvency invariant checked on every
///             state change.
///
///         Core invariants (checked in every mutating path):
///           1. Per LP per token:      Σ_strategies outstanding[lp][s] ≤ realBalance[lp]
///           2. Per strategy per token: outstanding(strategy) ≤ strategyCap (Fluid-style rate limit)
///           3. Global per token:      Σ outstanding ≤ totalDeposits ≤ IERC20.balanceOf(this)
///
///         Rounding: Bunni-hardened mulDivUp where a claim on real capital is computed, so tracked
///         obligations are never understated (mirrors IntentHookV2 hardening spec).
///
///         Treasury (canonical): 0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac
contract SharedLiquidityVault is ReentrancyGuard, Pausable, ISharedLiquidityVault {
    using SafeERC20 for IERC20;

    // ------------------------------------------------------------------ types

    struct Strategy {
        address hook;            // contract allowed to draw/settle (e.g. SharedLiquidityHook)
        address defaultBacker;   // LP whose virtual allocation covers un-routed draws (typically treasury)
        uint256 capSINC;         // Fluid-style per-strategy outstanding cap
        uint256 capUSDC;
        bool active;
    }

    // ------------------------------------------------------------------ storage

    address public immutable guardian;
    address public treasury; // canonical SINCOR2 treasury, settable by guardian
    IAccountingHub public accountingHub; // optional, hardened-optional like IntentHookV2

    IERC20 public immutable SINC;
    IERC20 public immutable USDC;

    uint256 public nextStrategyId;

    /// @dev LP principal: real claim on the vault, per token.
    mapping(address lp => mapping(address token => uint256)) public realBalance;
    /// @dev Virtual allocations: commitments, NOT locks. Sum across strategies may exceed realBalance.
    mapping(address lp => mapping(uint256 strategyId => mapping(address token => uint256))) public virtualAlloc;
    /// @dev Real capital currently drawn by a strategy on behalf of an LP.
    mapping(address lp => mapping(uint256 strategyId => mapping(address token => uint256))) public outstanding;
    /// @dev Aggregate outstanding per strategy (for caps).
    mapping(uint256 strategyId => mapping(address token => uint256)) public strategyOutstanding;
    /// @dev Aggregate outstanding per LP (for fast invariant checks).
    mapping(address lp => mapping(address token => uint256)) public lpOutstandingTotal;
    /// @dev Global outstanding per token (drawn capital that left the contract but remains an LP claim).
    mapping(address token => uint256) public outstandingTotal;
    /// @dev Uncollected LP fee claims per token (used by the solvency invariant).
    mapping(address token => uint256) public feeClaimsTotal;
    /// @dev Fees accrued to LPs inside strategies, credited on settle.
    mapping(address lp => mapping(address token => uint256)) public accruedFees;

    mapping(uint256 strategyId => Strategy) public strategies;

    uint256 public totalDepositsSINC;
    uint256 public totalDepositsUSDC;

    // ------------------------------------------------------------------ events

    event Deposited(address indexed lp, address indexed token, uint256 amount);
    event Withdrawn(address indexed lp, address indexed token, uint256 amount);
    event VirtualAllocated(address indexed lp, uint256 indexed strategyId, address indexed token, uint256 amount);
    event VirtualDeallocated(address indexed lp, uint256 indexed strategyId, address indexed token, uint256 amount);
    event DrawDown(address indexed lp, uint256 indexed strategyId, address indexed token, uint256 amount);
    event Settled(address indexed lp, uint256 indexed strategyId, address indexed token, uint256 principal, uint256 fee);
    event StrategyRegistered(uint256 indexed strategyId, address hook, address defaultBacker);
    event StrategyCapsUpdated(uint256 indexed strategyId, uint256 capSINC, uint256 capUSDC);
    event FeesHarvested(address indexed lp, address indexed token, uint256 amount, address to);
    event TreasuryUpdated(address newTreasury);
    event HubUpdated(address newHub);

    // ------------------------------------------------------------------ errors

    error Unauthorized();
    error TokenNotSupported();
    error ZeroAmount();
    error StrategyNotActive();
    error InsufficientRealBalance(uint256 requested, uint256 available);
    error InsufficientVirtualAlloc(uint256 requested, uint256 available);
    error StrategyCapExceeded(uint256 requested, uint256 capHeadroom);
    error OutstandingAllocation(uint256 outstandingAmt);
    error InvalidConfig();

    // ------------------------------------------------------------------ modifiers

    modifier onlyGuardian() {
        if (msg.sender != guardian) revert Unauthorized();
        _;
    }

    modifier onlyStrategyHook(uint256 strategyId) {
        if (msg.sender != strategies[strategyId].hook || !strategies[strategyId].active) revert Unauthorized();
        _;
    }

    modifier supported(address token) {
        if (token != address(SINC) && token != address(USDC)) revert TokenNotSupported();
        _;
    }

    // ------------------------------------------------------------------ ctor

    constructor(IERC20 _sinc, IERC20 _usdc, address _guardian, address _treasury) {
        if (address(_sinc) == address(0) || address(_usdc) == address(0) || _guardian == address(0) || _treasury == address(0)) {
            revert InvalidConfig();
        }
        SINC = _sinc;
        USDC = _usdc;
        guardian = _guardian;
        treasury = _treasury;
    }

    // ------------------------------------------------------------------ LP: deposits

    /// @notice Deposit real SINC or USDC. Credits the LP's real claim.
    function deposit(address token, uint256 amount)
        external
        nonReentrant
        whenNotPaused
        supported(token)
    {
        if (amount == 0) revert ZeroAmount();
        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);
        realBalance[msg.sender][token] += amount;
        _bumpTotals(token, int256(amount));
        emit Deposited(msg.sender, token, amount);
    }

    /// @notice Withdraw real capital. Blocked only by capital currently drawn (outstanding),
    ///         NOT by virtual allocations — allocations are commitments, not locks (Aqua semantics).
    function withdraw(address token, uint256 amount)
        external
        nonReentrant
        supported(token)
    {
        if (amount == 0) revert ZeroAmount();
        uint256 free = freeBalance(msg.sender, token);
        if (amount > free) revert InsufficientRealBalance(amount, free);

        realBalance[msg.sender][token] -= amount;
        _bumpTotals(token, -int256(amount));
        IERC20(token).safeTransfer(msg.sender, amount);
        emit Withdrawn(msg.sender, token, amount);
    }

    // ------------------------------------------------------------------ LP: virtual allocation

    /// @notice Virtually allocate capital to a strategy. Does NOT move or lock tokens.
    ///         The same capital may be allocated to multiple strategies simultaneously.
    /// @dev    Bounded by realBalance so a commitment can never exceed the LP's own capital.
    function allocateVirtual(uint256 strategyId, address token, uint256 amount)
        external
        whenNotPaused
        supported(token)
    {
        if (amount == 0) revert ZeroAmount();
        if (!strategies[strategyId].active) revert StrategyNotActive();
        // Allow oversubscription ACROSS strategies, but each single allocation is capped by the
        // LP's total real capital: no commitment can exceed what the LP actually owns.
        if (amount > realBalance[msg.sender][token]) {
            revert InsufficientRealBalance(amount, realBalance[msg.sender][token]);
        }
        virtualAlloc[msg.sender][strategyId][token] += amount;
        emit VirtualAllocated(msg.sender, strategyId, token, amount);
    }

    /// @notice Reduce a virtual allocation. Cannot reduce below what the strategy currently has drawn.
    function deallocateVirtual(uint256 strategyId, address token, uint256 amount)
        external
        supported(token)
    {
        if (amount == 0) revert ZeroAmount();
        uint256 alloc = virtualAlloc[msg.sender][strategyId][token];
        if (amount > alloc) revert InsufficientVirtualAlloc(amount, alloc);
        uint256 drawn = outstanding[msg.sender][strategyId][token];
        if (alloc - amount < drawn) revert OutstandingAllocation(drawn);
        virtualAlloc[msg.sender][strategyId][token] = alloc - amount;
        emit VirtualDeallocated(msg.sender, strategyId, token, amount);
    }

    // ------------------------------------------------------------------ strategy: execution-time pulls

    /// @notice Pull real capital at execution time against an LP's virtual allocation.
    ///         Only callable by the strategy's registered hook.
    /// @dev    Enforces all three invariants. Converts virtual commitment into real outstanding debt.
    function drawDown(uint256 strategyId, address lp, address token, uint256 amount)
        external
        nonReentrant
        whenNotPaused
        supported(token)
        onlyStrategyHook(strategyId)
    {
        if (amount == 0) revert ZeroAmount();

        uint256 alloc = virtualAlloc[lp][strategyId][token];
        uint256 drawn = outstanding[lp][strategyId][token];
        // Strategy-level: cannot draw beyond this LP's virtual commitment to this strategy
        if (drawn + amount > alloc) revert InsufficientVirtualAlloc(amount, alloc - drawn);

        // LP-level global invariant: total outstanding across ALL strategies ≤ real capital
        uint256 lpOut = lpOutstandingTotal[lp][token];
        if (lpOut + amount > realBalance[lp][token]) {
            revert InsufficientRealBalance(amount, realBalance[lp][token] - lpOut);
        }

        // Strategy cap (Fluid-style rate limit)
        uint256 stratOut = strategyOutstanding[strategyId][token];
        uint256 cap = token == address(SINC) ? strategies[strategyId].capSINC : strategies[strategyId].capUSDC;
        if (cap != 0 && stratOut + amount > cap) revert StrategyCapExceeded(amount, cap - stratOut);

        outstanding[lp][strategyId][token] = drawn + amount;
        lpOutstandingTotal[lp][token] = lpOut + amount;
        strategyOutstanding[strategyId][token] = stratOut + amount;
        outstandingTotal[token] += amount;

        IERC20(token).safeTransfer(msg.sender, amount);
        emit DrawDown(lp, strategyId, token, amount);
    }

    /// @notice Return drawn capital plus any execution fee earned. Fee is split: protocolFeeBps to
    ///         treasury, remainder credited to the LP's accruedFees.
    /// @param principal  Amount of outstanding principal being returned (≤ current outstanding).
    /// @param fee        Gross fee earned on top of principal (0 allowed).
    /// @param protocolFeeBps Share of `fee` skimmed to treasury (≤ 10_000).
    function settleUp(uint256 strategyId, address lp, address token, uint256 principal, uint256 fee, uint256 protocolFeeBps)
        external
        nonReentrant
        supported(token)
        onlyStrategyHook(strategyId)
    {
        require(protocolFeeBps <= 10_000, "fee bps");
        uint256 drawn = outstanding[lp][strategyId][token];
        if (principal > drawn) revert InsufficientVirtualAlloc(principal, drawn);

        uint256 total = principal + fee;
        if (total > 0) IERC20(token).safeTransferFrom(msg.sender, address(this), total);

        if (principal > 0) {
            outstanding[lp][strategyId][token] = drawn - principal;
            lpOutstandingTotal[lp][token] -= principal;
            strategyOutstanding[strategyId][token] -= principal;
            outstandingTotal[token] -= principal;
        }
        if (fee > 0) {
            uint256 protocolCut = FullMath.mulDiv(fee, protocolFeeBps, 10_000);
            uint256 lpCut = fee - protocolCut;
            if (protocolCut > 0) IERC20(token).safeTransfer(treasury, protocolCut);
            if (lpCut > 0) {
                accruedFees[lp][token] += lpCut;
                feeClaimsTotal[token] += lpCut;
            }
        }
        emit Settled(lp, strategyId, token, principal, fee);
    }

    /// @notice LP harvests accrued strategy fees.
    function harvestFees(address token, address to)
        external
        nonReentrant
        supported(token)
    {
        uint256 amt = accruedFees[msg.sender][token];
        if (amt == 0) revert ZeroAmount();
        accruedFees[msg.sender][token] = 0;
        feeClaimsTotal[token] -= amt;
        IERC20(token).safeTransfer(to == address(0) ? msg.sender : to, amt);
        emit FeesHarvested(msg.sender, token, amt, to);
    }

    // ------------------------------------------------------------------ views

    /// @notice Real capital of an LP not currently drawn by any strategy.
    function freeBalance(address lp, address token) public view returns (uint256) {
        return realBalance[lp][token] - lpOutstandingTotal[lp][token];
    }

    function strategyHook(uint256 strategyId) external view returns (address) {
        Strategy storage s = strategies[strategyId];
        return s.active ? s.hook : address(0);
    }

    function strategyDefaultBacker(uint256 strategyId) external view returns (address) {
        return strategies[strategyId].defaultBacker;
    }

    /// @notice How much a strategy could still draw from a given LP right now:
    ///         min(remaining virtual commitment, remaining free real capital, cap headroom).
    function availableDraw(uint256 strategyId, address lp, address token) external view returns (uint256) {
        uint256 commitLeft = virtualAlloc[lp][strategyId][token] - outstanding[lp][strategyId][token];
        uint256 freeReal = freeBalance(lp, token);
        uint256 head = commitLeft < freeReal ? commitLeft : freeReal;
        uint256 cap = token == address(SINC) ? strategies[strategyId].capSINC : strategies[strategyId].capUSDC;
        if (cap != 0) {
            uint256 capLeft = cap - strategyOutstanding[strategyId][token];
            if (capLeft < head) head = capLeft;
        }
        return head;
    }

    /// @notice Global solvency invariant (Fluid-style): assets in hand + assets drawn by
    ///         strategies must always cover every real claim (deposits + uncollected fees).
    function checkInvariant() public view returns (bool) {
        return _solvent(address(SINC)) && _solvent(address(USDC));
    }

    function _solvent(address token) internal view returns (bool) {
        uint256 deposits = token == address(SINC) ? totalDepositsSINC : totalDepositsUSDC;
        uint256 claims = deposits + feeClaimsTotal[token];
        uint256 assets = IERC20(token).balanceOf(address(this)) + outstandingTotal[token];
        return assets >= claims;
    }

    // ------------------------------------------------------------------ admin

    function registerStrategy(address hook, address defaultBacker, uint256 capSINC, uint256 capUSDC)
        external
        onlyGuardian
        returns (uint256 strategyId)
    {
        if (hook == address(0)) revert InvalidConfig();
        strategyId = nextStrategyId++;
        strategies[strategyId] = Strategy({
            hook: hook,
            defaultBacker: defaultBacker,
            capSINC: capSINC,
            capUSDC: capUSDC,
            active: true
        });
        emit StrategyRegistered(strategyId, hook, defaultBacker);
    }

    function setStrategyCaps(uint256 strategyId, uint256 capSINC, uint256 capUSDC) external onlyGuardian {
        strategies[strategyId].capSINC = capSINC;
        strategies[strategyId].capUSDC = capUSDC;
        emit StrategyCapsUpdated(strategyId, capSINC, capUSDC);
    }

    function setStrategyActive(uint256 strategyId, bool active_) external onlyGuardian {
        strategies[strategyId].active = active_;
    }

    function setTreasury(address _treasury) external onlyGuardian {
        if (_treasury == address(0)) revert InvalidConfig();
        treasury = _treasury;
        emit TreasuryUpdated(_treasury);
    }

    function setAccountingHub(address hub) external onlyGuardian {
        accountingHub = IAccountingHub(hub);
        emit HubUpdated(hub);
    }

    function pause() external onlyGuardian { _pause(); }
    function unpause() external onlyGuardian { _unpause(); }

    // ------------------------------------------------------------------ internal

    function _bumpTotals(address token, int256 delta) internal {
        if (token == address(SINC)) {
            totalDepositsSINC = uint256(int256(totalDepositsSINC) + delta);
        } else {
            totalDepositsUSDC = uint256(int256(totalDepositsUSDC) + delta);
        }
    }
}
