// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {FullMath} from "@uniswap/v4-core/src/libraries/FullMath.sol";

import {IAccountingHub} from "./interfaces/IAccountingHub.sol";
import {ISharedLiquidityVault} from "./interfaces/ISharedLiquidityVault.sol";

/// @title SharedLiquidityVault — Aqua/Fluid-inspired shared-liquidity layer for SINC/USDC
/// @notice LP capital stays deposited here and is *virtually* allocated to multiple strategies
///         (hooks). A strategy hook may draw down real capital for the duration of a swap and
///         must settle principal (+fees) afterwards. Cross-strategy draws are bounded by the
///         LP's real balance, not by per-strategy virtual balances.
contract SharedLiquidityVault is ReentrancyGuard, Pausable, ISharedLiquidityVault {
    using SafeERC20 for IERC20;

    struct Strategy {
        address hook;
        address defaultBacker;
        uint256 capSINC;
        uint256 capUSDC;
        bool active;
    }

    address public immutable guardian;
    address public treasury;
    IAccountingHub public accountingHub;

    IERC20 public immutable SINC;
    IERC20 public immutable USDC;

    uint256 public nextStrategyId;

    // LP real capital (deposits minus withdrawals)
    mapping(address lp => mapping(address token => uint256)) public realBalance;
    // Virtual commitment per LP/strategy/token (Fluid-style liquidity layers)
    mapping(address lp => mapping(uint256 strategyId => mapping(address token => uint256))) public virtualAlloc;
    // Outstanding draws (real capital currently lent out by a strategy)
    mapping(address lp => mapping(uint256 strategyId => mapping(address token => uint256))) public outstanding;
    mapping(uint256 strategyId => mapping(address token => uint256)) public strategyOutstanding;
    mapping(address lp => mapping(address token => uint256)) public lpOutstandingTotal;
    mapping(address token => uint256) public outstandingTotal;
    // Fee claims (accrued, unharvested)
    mapping(address token => uint256) public feeClaimsTotal;
    mapping(address lp => mapping(address token => uint256)) public accruedFees;

    mapping(uint256 strategyId => Strategy) public strategies;

    uint256 public totalDepositsSINC;
    uint256 public totalDepositsUSDC;

    event Deposited(address indexed lp, address indexed token, uint256 amount);
    event Withdrawn(address indexed lp, address indexed token, uint256 amount);
    event VirtualAllocated(address indexed lp, uint256 indexed strategyId, address indexed token, uint256 amount);
    event VirtualDeallocated(address indexed lp, uint256 indexed strategyId, address indexed token, uint256 amount);
    event DrawDown(address indexed lp, uint256 indexed strategyId, address indexed token, uint256 amount);
    event Settled(address indexed lp, uint256 indexed strategyId, address indexed token, uint256 principal, uint256 fee);
    event StrategyRegistered(uint256 indexed strategyId, address hook, address defaultBacker);
    event StrategyCapsUpdated(uint256 indexed strategyId, uint256 capSINC, uint256 capUSDC);
    event StrategyActiveUpdated(uint256 indexed strategyId, bool active);
    event FeesHarvested(address indexed lp, address indexed token, uint256 amount, address to);
    event TreasuryUpdated(address newTreasury);
    event HubUpdated(address newHub);

    error Unauthorized();
    error TokenNotSupported();
    error ZeroAmount();
    error StrategyNotActive();
    error InsufficientRealBalance(uint256 requested, uint256 available);
    error InsufficientVirtualAlloc(uint256 requested, uint256 available);
    error StrategyCapExceeded(uint256 requested, uint256 capHeadroom);
    error OutstandingAllocation(uint256 outstandingAmt);
    error InvalidConfig();

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

    constructor(IERC20 _sinc, IERC20 _usdc, address _guardian, address _treasury) {
        if (address(_sinc) == address(0) || address(_usdc) == address(0) || _guardian == address(0) || _treasury == address(0)) {
            revert InvalidConfig();
        }
        SINC = _sinc;
        USDC = _usdc;
        guardian = _guardian;
        treasury = _treasury;
    }

    // ==================== LP REAL LIQUIDITY ====================

    function deposit(address token, uint256 amount)
        external
        nonReentrant
        whenNotPaused
        supported(token)
    {
        if (amount == 0) revert ZeroAmount();
        realBalance[msg.sender][token] += amount;
        _bumpTotals(token, int256(amount));
        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);
        emit Deposited(msg.sender, token, amount);
    }

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

    // ==================== VIRTUAL ALLOCATION ====================

    function allocateVirtual(uint256 strategyId, address token, uint256 amount)
        external
        whenNotPaused
        supported(token)
    {
        if (amount == 0) revert ZeroAmount();
        if (!strategies[strategyId].active) revert StrategyNotActive();
        if (amount > realBalance[msg.sender][token]) {
            revert InsufficientRealBalance(amount, realBalance[msg.sender][token]);
        }
        virtualAlloc[msg.sender][strategyId][token] += amount;
        emit VirtualAllocated(msg.sender, strategyId, token, amount);
    }

    function deallocateVirtual(uint256 strategyId, address token, uint256 amount)
        external
        supported(token)
    {
        if (amount == 0) revert ZeroAmount();
        uint256 alloc = virtualAlloc[msg.sender][strategyId][token];
        if (amount > alloc) revert InsufficientVirtualAlloc(alloc, amount);
        uint256 drawn = outstanding[msg.sender][strategyId][token];
        if (alloc - amount < drawn) revert OutstandingAllocation(drawn);
        virtualAlloc[msg.sender][strategyId][token] = alloc - amount;
        emit VirtualDeallocated(msg.sender, strategyId, token, amount);
    }

    // ==================== STRATEGY DRAWDOWN / SETTLEMENT ====================

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
        if (drawn + amount > alloc) revert InsufficientVirtualAlloc(amount, alloc - drawn);

        uint256 lpOut = lpOutstandingTotal[lp][token];
        if (lpOut + amount > realBalance[lp][token]) {
            revert InsufficientRealBalance(amount, realBalance[lp][token] - lpOut);
        }

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

    function settleUp(uint256 strategyId, address lp, address token, uint256 principal, uint256 fee, uint256 protocolFeeBps)
        external
        nonReentrant
        supported(token)
        onlyStrategyHook(strategyId)
    {
        if (protocolFeeBps > 10_000) revert InvalidConfig();
        uint256 drawn = outstanding[lp][strategyId][token];
        if (principal > drawn) revert InsufficientVirtualAlloc(principal, drawn);

        uint256 protocolCut = 0;
        uint256 lpCut = 0;
        if (principal > 0) {
            outstanding[lp][strategyId][token] = drawn - principal;
            lpOutstandingTotal[lp][token] -= principal;
            strategyOutstanding[strategyId][token] -= principal;
            outstandingTotal[token] -= principal;
        }
        if (fee > 0) {
            protocolCut = FullMath.mulDiv(fee, protocolFeeBps, 10_000);
            lpCut = fee - protocolCut;
            if (lpCut > 0) {
                accruedFees[lp][token] += lpCut;
                feeClaimsTotal[token] += lpCut;
            }
        }

        uint256 total = principal + fee;
        if (total > 0) IERC20(token).safeTransferFrom(msg.sender, address(this), total);
        if (protocolCut > 0) IERC20(token).safeTransfer(treasury, protocolCut);
        emit Settled(lp, strategyId, token, principal, fee);
    }

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

    // ==================== VIEWS ====================

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

    function checkInvariant() public view returns (bool) {
        return _solvent(address(SINC)) && _solvent(address(USDC));
    }

    function _solvent(address token) internal view returns (bool) {
        uint256 deposits = token == address(SINC) ? totalDepositsSINC : totalDepositsUSDC;
        uint256 claims = deposits + feeClaimsTotal[token];
        uint256 assets = IERC20(token).balanceOf(address(this)) + outstandingTotal[token];
        return assets >= claims;
    }

    // ==================== GUARDIAN ADMIN ====================

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
        emit StrategyActiveUpdated(strategyId, active_);
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

    function _bumpTotals(address token, int256 delta) internal {
        if (token == address(SINC)) {
            totalDepositsSINC = uint256(int256(totalDepositsSINC) + delta);
        } else {
            totalDepositsUSDC = uint256(int256(totalDepositsUSDC) + delta);
        }
    }
}
