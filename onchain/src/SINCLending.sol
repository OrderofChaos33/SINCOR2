// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IERC20Metadata} from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import {Pausable} from "@openzeppelin/contracts/security/Pausable.sol";
import {FullMath} from "@uniswap/v4-core/src/libraries/FullMath.sol";

import {ISincPriceOracle, ISincSwapRouter} from "./interfaces/ISincLoop.sol";

/// @title SINCLending — isolated SINC-collateral / USDC-debt market with lending-loop ROI variants
/// @notice Compound-style index accounting (WAD-scaled, per-second linear accrual), kinked rate
///         model, $1 SINC price-floor aware collateral valuation, and preset loop variants.
/// @dev SINC's decimals are read from the token at construction (canonical SINC v3 on Base is
///      8 decimals). All SINC↔USDC valuation math normalizes by `sincUnit` (= 10**decimals), so
///      the market is correct for any ERC-20 collateral with ≤ 18 decimals.
contract SINCLending is ReentrancyGuard, Pausable {
    using SafeERC20 for IERC20;

    uint256 public constant WAD = 1e18;
    uint256 public constant BPS = 10_000;
    uint256 public constant SECONDS_PER_YEAR = 365 days;

    IERC20 public immutable SINC;              // decimals read at construction (8 for canonical SINC v3)
    IERC20 public immutable USDC;              // 6 decimals
    uint256 public immutable sincUnit;         // 10 ** SINC.decimals() — one whole SINC in raw units
    ISincPriceOracle public oracle;
    ISincSwapRouter public swapRouter;
    address public guardian;
    address public treasury;

    uint256 public priceFloor = 1e6; // $1.00

    uint256 public maxLTVBps = 7_500;
    uint256 public liquidationThresholdBps = 8_000;
    uint256 public liquidationBonusBps = 500;  // 5%
    uint256 public closeFactorBps = 5_000;     // max 50% of debt per liquidation
    uint256 public reserveFactorBps = 1_000;   // 10% of interest → treasury reserves

    uint256 public baseRateBps = 200;          // 2% APR
    uint256 public slope1Bps = 1_000;
    uint256 public slope2Bps = 10_000;
    uint256 public kinkBps = 8_000;            // 80% utilization kink

    uint256 public totalSupplyShares;
    uint256 public totalBorrowShares;
    uint256 public totalCash;
    uint256 public totalBorrows;
    uint256 public totalReserves;
    uint256 public totalCollateral;

    uint256 public borrowIndex = WAD;
    uint256 public lastAccrual;

    mapping(address => uint256) public supplyShares;
    mapping(address => uint256) public borrowSharesOf;
    mapping(address => uint256) public collateralOf;

    struct LoopVariant {
        uint256 ltvBps;
        uint256 maxLoops;
        bool active;
    }

    uint256 public constant CONSERVATIVE = 0;
    uint256 public constant BALANCED = 1;
    uint256 public constant AGGRESSIVE = 2;

    mapping(uint256 => LoopVariant) public loopVariants;
    uint256 public loopCount;

    event Supplied(address indexed user, uint256 usdc, uint256 shares);
    event Redeemed(address indexed user, uint256 shares, uint256 usdc);
    event CollateralDeposited(address indexed user, uint256 sinc);
    event CollateralWithdrawn(address indexed user, uint256 sinc);
    event Borrowed(address indexed user, uint256 usdc, uint256 shares);
    event Repaid(address indexed user, uint256 usdc, uint256 shares);
    event Liquidated(address indexed user, address indexed liquidator, uint256 repaid, uint256 seized);
    event LoopOpened(address indexed user, uint256 indexed variantId, uint256 initialSinc, uint256 loops, uint256 finalCollateral, uint256 finalDebt);
    event LoopClosed(address indexed user, uint256 debtRepaid, uint256 collateralReturned);
    event ReservesSwept(address indexed to, uint256 amount);
    event VariantConfigured(uint256 indexed variantId, uint256 ltvBps, uint256 maxLoops, bool active);
    event PriceFloorUpdated(uint256 newFloor);
    event RiskParamsUpdated(uint256 maxLtvBps, uint256 liquidationThresholdBps, uint256 liquidationBonusBps, uint256 closeFactorBps);
    event RateModelUpdated(uint256 baseRateBps, uint256 slope1Bps, uint256 slope2Bps, uint256 kinkBps);
    event OracleUpdated(address newOracle);
    event SwapRouterUpdated(address newRouter);
    event TreasuryUpdated(address newTreasury);

    error Unauthorized();
    error ZeroAmount();
    error InvalidConfig();
    error InsufficientLiquidity();
    error InsufficientCollateral();
    error UnhealthyPosition();
    error PositionHealthy();
    error NoDebt();
    error VariantInactive();
    error LoopLimitExceeded();
    error SolvencyBreached();

    modifier onlyGuardian() {
        if (msg.sender != guardian) revert Unauthorized();
        _;
    }

    constructor(IERC20 _sinc, IERC20 _usdc, ISincPriceOracle _oracle, ISincSwapRouter _router, address _guardian, address _treasury) {
        if (address(_sinc) == address(0) || address(_usdc) == address(0) || address(_oracle) == address(0)
            || address(_router) == address(0) || _guardian == address(0) || _treasury == address(0)) {
            revert InvalidConfig();
        }
        uint8 sincDecimals = 18; // ERC-20 default when decimals() is not implemented
        try IERC20Metadata(address(_sinc)).decimals() returns (uint8 d) {
            if (d > 18) revert InvalidConfig(); // keeps 10**d and mulDiv math in safe range
            sincDecimals = d;
        } catch {}
        SINC = _sinc;
        USDC = _usdc;
        sincUnit = 10 ** sincDecimals;
        oracle = _oracle;
        swapRouter = _router;
        guardian = _guardian;
        treasury = _treasury;
        lastAccrual = block.timestamp;

        _setVariant(CONSERVATIVE, 5_000, 2, true);   // ≤ ~1.97x leverage
        _setVariant(BALANCED, 6_500, 3, true);       // ≤ ~2.86x leverage
        _setVariant(AGGRESSIVE, 7_500, 4, true);     // ≤ ~4.00x leverage
    }

    // ================================================================== interest accrual

    function accrueInterest() public {
        uint256 dt = block.timestamp - lastAccrual;
        if (dt == 0) return;
        lastAccrual = block.timestamp;
        if (totalBorrows == 0) return;

        uint256 borrowRatePerSec = _borrowRatePerSec();
        uint256 interest = FullMath.mulDiv(totalBorrows, borrowRatePerSec * dt, WAD);
        totalBorrows += interest;
        totalReserves += FullMath.mulDiv(interest, reserveFactorBps, BPS);
        borrowIndex += FullMath.mulDiv(borrowIndex, borrowRatePerSec * dt, WAD);
    }

    function utilizationBps() public view returns (uint256) {
        uint256 total = totalCash + totalBorrows;
        if (total == 0) return 0;
        return FullMath.mulDiv(totalBorrows, BPS, total);
    }

    function borrowAPRBps() public view returns (uint256) {
        uint256 u = utilizationBps();
        if (u <= kinkBps) return baseRateBps + FullMath.mulDiv(slope1Bps, u, BPS);
        uint256 excess = u - kinkBps;
        return baseRateBps + FullMath.mulDiv(slope1Bps, kinkBps, BPS) + FullMath.mulDiv(slope2Bps, excess, BPS);
    }

    function supplyAPRBps() external view returns (uint256) {
        uint256 b = borrowAPRBps();
        uint256 u = utilizationBps();
        uint256 gross = FullMath.mulDiv(b, u, BPS);
        return gross - FullMath.mulDiv(gross, reserveFactorBps, BPS);
    }

    function _borrowRatePerSec() internal view returns (uint256) {
        return FullMath.mulDiv(borrowAPRBps(), WAD, BPS * SECONDS_PER_YEAR);
    }

    // ================================================================== pricing

    function collateralPrice() public view returns (uint256) {
        uint256 p = oracle.sincPriceUSDC();
        return p > priceFloor ? p : priceFloor;
    }

    function sincValueUSDC(uint256 sincAmount) public view returns (uint256) {
        return FullMath.mulDiv(sincAmount, collateralPrice(), sincUnit);
    }

    // ================================================================== suppliers (USDC)

    function supplyUSDC(uint256 amount) external nonReentrant whenNotPaused {
        if (amount == 0) revert ZeroAmount();
        accrueInterest();
        uint256 exchange = _supplyExchangeRate();
        uint256 shares = FullMath.mulDiv(amount, WAD, exchange);
        supplyShares[msg.sender] += shares;
        totalSupplyShares += shares;
        totalCash += amount;
        USDC.safeTransferFrom(msg.sender, address(this), amount);
        emit Supplied(msg.sender, amount, shares);
    }

    function redeemUSDC(uint256 shares) external nonReentrant {
        if (shares == 0) revert ZeroAmount();
        accrueInterest();
        if (shares > supplyShares[msg.sender]) revert InsufficientLiquidity();
        uint256 amount = FullMath.mulDiv(shares, _supplyExchangeRate(), WAD);
        if (amount > _availableCash()) revert InsufficientLiquidity();
        supplyShares[msg.sender] -= shares;
        totalSupplyShares -= shares;
        totalCash -= amount;
        USDC.safeTransfer(msg.sender, amount);
        emit Redeemed(msg.sender, shares, amount);
    }

    function supplyBalanceUSDC(address user) external view returns (uint256) {
        return FullMath.mulDiv(supplyShares[user], _supplyExchangeRateView(), WAD);
    }

    function _supplyExchangeRate() internal view returns (uint256) {
        return _exchangeRate(totalCash + totalBorrows - totalReserves);
    }

    function _supplyExchangeRateView() internal view returns (uint256) {
        uint256 cash = totalCash;
        uint256 borrows = totalBorrows;
        uint256 reserves = totalReserves;
        uint256 dt = block.timestamp - lastAccrual;
        if (dt > 0 && borrows > 0) {
            uint256 interest = FullMath.mulDiv(borrows, _borrowRatePerSec() * dt, WAD);
            borrows += interest;
            reserves += FullMath.mulDiv(interest, reserveFactorBps, BPS);
        }
        return _exchangeRate(cash + borrows - reserves);
    }

    function _exchangeRate(uint256 poolTotal) internal view returns (uint256) {
        if (totalSupplyShares == 0 || poolTotal == 0) return WAD;
        return FullMath.mulDiv(poolTotal, WAD, totalSupplyShares);
    }

    function _availableCash() internal view returns (uint256) {
        uint256 bal = USDC.balanceOf(address(this));
        return bal > totalReserves ? bal - totalReserves : 0;
    }

    // ================================================================== borrowers (SINC collateral, USDC debt)

    function depositCollateral(uint256 sincAmount) external nonReentrant whenNotPaused {
        if (sincAmount == 0) revert ZeroAmount();
        accrueInterest();
        SINC.safeTransferFrom(msg.sender, address(this), sincAmount);
        collateralOf[msg.sender] += sincAmount;
        totalCollateral += sincAmount;
        emit CollateralDeposited(msg.sender, sincAmount);
    }

    function withdrawCollateral(uint256 sincAmount) external nonReentrant {
        if (sincAmount == 0) revert ZeroAmount();
        accrueInterest();
        if (sincAmount > collateralOf[msg.sender]) revert InsufficientCollateral();
        collateralOf[msg.sender] -= sincAmount;
        totalCollateral -= sincAmount;
        if (!_isHealthy(msg.sender)) revert UnhealthyPosition();
        SINC.safeTransfer(msg.sender, sincAmount);
        emit CollateralWithdrawn(msg.sender, sincAmount);
    }

    function borrow(uint256 usdcAmount) external nonReentrant whenNotPaused {
        if (usdcAmount == 0) revert ZeroAmount();
        accrueInterest();
        _borrow(msg.sender, usdcAmount);
        USDC.safeTransfer(msg.sender, usdcAmount);
    }

    function repay(uint256 usdcAmount) external nonReentrant {
        if (usdcAmount == 0) revert ZeroAmount();
        accrueInterest();
        USDC.safeTransferFrom(msg.sender, address(this), usdcAmount);
        _repayLedger(msg.sender, usdcAmount);
    }

    function _borrow(address user, uint256 usdcAmount) internal {
        if (usdcAmount > _availableCash()) revert InsufficientLiquidity();
        uint256 shares = FullMath.mulDiv(usdcAmount, WAD, borrowIndex);
        borrowSharesOf[user] += shares;
        totalBorrowShares += shares;
        totalBorrows += usdcAmount;
        totalCash -= usdcAmount;
        if (!_isHealthy(user)) revert UnhealthyPosition();
        emit Borrowed(user, usdcAmount, shares);
    }

    /// @dev Ledger-only repayment: updates shares/borrows/cash, refunds overpayment to `user`.
    ///      Contains no state writes after its final external call (CEI-clean for callers).
    function _repayLedger(address user, uint256 usdcAmount) internal returns (uint256 applied) {
        uint256 debt = borrowBalance(user);
        applied = usdcAmount > debt ? debt : usdcAmount;
        bool fullRepay = applied == debt;
        // borrowBalance rounds UP (+1 wei) while totalBorrows is mulDiv-floored — clamp so the
        // aggregate ledger can never underflow on a full repay.
        if (applied > totalBorrows) applied = totalBorrows;
        uint256 sharesBurn = fullRepay ? borrowSharesOf[user] : FullMath.mulDiv(applied, WAD, borrowIndex);
        borrowSharesOf[user] -= sharesBurn;
        totalBorrowShares -= sharesBurn;
        totalBorrows -= applied;
        totalCash += applied;
        uint256 refund = usdcAmount - applied;
        emit Repaid(user, applied, sharesBurn);
        if (refund > 0) USDC.safeTransfer(user, refund); // final external call — no writes after
    }

    function borrowBalance(address user) public view returns (uint256) {
        uint256 shares = borrowSharesOf[user];
        if (shares == 0) return 0;
        // round UP — obligations never understated (Bunni-hardening convention)
        return FullMath.mulDiv(shares, _borrowIndexView(), WAD) + 1;
    }

    function _borrowIndexView() internal view returns (uint256) {
        uint256 idx = borrowIndex;
        uint256 dt = block.timestamp - lastAccrual;
        if (dt > 0 && totalBorrows > 0) {
            idx += FullMath.mulDiv(idx, _borrowRatePerSec() * dt, WAD);
        }
        return idx;
    }

    // ================================================================== health & liquidation

    function healthFactor(address user) public view returns (uint256) {
        uint256 debt = borrowBalance(user);
        if (debt == 0) return type(uint256).max;
        uint256 collateralValue = sincValueUSDC(collateralOf[user]);
        uint256 adjusted = FullMath.mulDiv(collateralValue, liquidationThresholdBps, BPS);
        return FullMath.mulDiv(adjusted, WAD, debt);
    }

    function _isHealthy(address user) internal view returns (bool) {
        uint256 hf = healthFactor(user);
        return hf >= WAD;
    }

    function liquidate(address user, uint256 repayAmount) external nonReentrant {
        if (repayAmount == 0) revert ZeroAmount();
        accrueInterest();
        if (_isHealthy(user)) revert PositionHealthy();
        uint256 debt = borrowBalance(user);
        uint256 maxClose = FullMath.mulDiv(debt, closeFactorBps, BPS);
        uint256 repay_ = repayAmount > maxClose ? maxClose : repayAmount;

        // seize SINC worth repay_ * (1 + bonus), valued at collateral price
        uint256 seizeValue = repay_ + FullMath.mulDiv(repay_, liquidationBonusBps, BPS);
        uint256 seizeSinc = FullMath.mulDiv(seizeValue, sincUnit, collateralPrice());
        if (seizeSinc > collateralOf[user]) seizeSinc = collateralOf[user];

        // --- effects (all ledger state settled before any external call) ---
        _repayLedger(user, repay_);
        collateralOf[user] -= seizeSinc;
        totalCollateral -= seizeSinc;

        // --- interactions ---
        USDC.safeTransferFrom(msg.sender, address(this), repay_);
        SINC.safeTransfer(msg.sender, seizeSinc);
        emit Liquidated(user, msg.sender, repay_, seizeSinc);
    }

    // ================================================================== lending loops (lever-long SINC)

    /// @dev Accepted-risk (AUDIT.md A1): each loop iteration necessarily writes ledger state
    ///      after the router swap — the amount bought is only known post-swap. Reentry is blocked
    ///      by the nonReentrant mutex, the router is a trusted guardian-set address, and the loop
    ///      is bounded by the variant's maxLoops.
    // slither-disable-next-line reentrancy-no-eth
    function openLoop(uint256 variantId, uint256 sincAmount, uint256 loops)
        external
        nonReentrant
        whenNotPaused
    {
        if (sincAmount == 0) revert ZeroAmount();
        LoopVariant storage v = loopVariants[variantId];
        if (!v.active) revert VariantInactive();
        if (loops == 0 || loops > v.maxLoops) revert LoopLimitExceeded();

        accrueInterest();
        SINC.safeTransferFrom(msg.sender, address(this), sincAmount);
        collateralOf[msg.sender] += sincAmount;
        totalCollateral += sincAmount;

        for (uint256 i = 0; i < loops; i++) {
            uint256 collateralValue = sincValueUSDC(collateralOf[msg.sender]);
            uint256 debt = borrowBalance(msg.sender);
            uint256 headroomValue = FullMath.mulDiv(collateralValue, v.ltvBps, BPS);
            uint256 borrowAmt = headroomValue > debt ? headroomValue - debt : 0;
            if (borrowAmt == 0) break;

            _borrow(msg.sender, borrowAmt);
            USDC.safeIncreaseAllowance(address(swapRouter), borrowAmt);
            uint256 sincOut = swapRouter.swapUSDCForSINC(borrowAmt);
            collateralOf[msg.sender] += sincOut;
            totalCollateral += sincOut;
        }

        if (!_isHealthy(msg.sender)) revert UnhealthyPosition();
        emit LoopOpened(msg.sender, variantId, sincAmount, loops, collateralOf[msg.sender], borrowBalance(msg.sender));
    }

    function closeLoop() external nonReentrant {
        accrueInterest();
        address user = msg.sender;
        uint256 debt = borrowBalance(user);
        if (debt == 0) revert NoDebt();

        uint256 sincNeeded = FullMath.mulDiv(debt, sincUnit, collateralPrice());
        sincNeeded += FullMath.mulDiv(sincNeeded, 100, BPS); // +1% buffer for swap fee/rounding
        uint256 userColl = collateralOf[user];
        if (sincNeeded > userColl) revert InsufficientCollateral();
        uint256 remaining = userColl - sincNeeded;

        // --- effects: entire collateral position settled before any external call ---
        collateralOf[user] = 0;
        totalCollateral -= userColl;

        // --- interactions (trusted guardian-set router; whole tx reverts on failure) ---
        SINC.safeIncreaseAllowance(address(swapRouter), sincNeeded);
        uint256 usdcOut = swapRouter.swapSINCForUSDC(sincNeeded);
        _repayLedger(user, usdcOut); // any surplus USDC refunds to user inside

        if (borrowBalance(user) > 1) revert SolvencyBreached(); // dust of 1 wei tolerated
        if (remaining > 0) SINC.safeTransfer(user, remaining);
        emit LoopClosed(user, debt, remaining);
    }

    function simulateLoopROI(uint256 collateralValueUSDC, uint256 variantId, int256 priceChangeBps, uint256 horizonDays)
        external
        view
        returns (uint256 leverageBps, int256 roiBps, uint256 borrowCostBps)
    {
        LoopVariant storage v = loopVariants[variantId];
        if (!v.active) revert VariantInactive();

        uint256 lambda = FullMath.mulDiv(v.ltvBps, WAD, BPS);
        uint256 term = WAD;
        uint256 sum = WAD;
        for (uint256 i = 0; i < v.maxLoops; i++) {
            term = FullMath.mulDiv(term, lambda, WAD);
            sum += term;
        }
        leverageBps = FullMath.mulDiv(sum, BPS, WAD);

        uint256 debtFracWad = sum - WAD;
        uint256 aprWad = FullMath.mulDiv(borrowAPRBps(), WAD, BPS);
        uint256 costWad = FullMath.mulDiv(debtFracWad, aprWad * horizonDays, SECONDS_PER_YEAR / 1 days);
        borrowCostBps = FullMath.mulDiv(costWad, BPS, WAD);

        int256 pricePnlBps = int256(FullMath.mulDiv(sum, BPS, WAD)) * priceChangeBps / int256(BPS);
        roiBps = pricePnlBps - int256(borrowCostBps);
    }

    // ================================================================== admin

    function configureVariant(uint256 variantId, uint256 ltvBps, uint256 maxLoops, bool active) external onlyGuardian {
        if (ltvBps > maxLTVBps) revert InvalidConfig();
        if (variantId >= loopCount) loopCount = variantId + 1;
        _setVariant(variantId, ltvBps, maxLoops, active);
    }

    function _setVariant(uint256 id, uint256 ltvBps, uint256 maxLoops, bool active) internal {
        loopVariants[id] = LoopVariant({ltvBps: ltvBps, maxLoops: maxLoops, active: active});
        emit VariantConfigured(id, ltvBps, maxLoops, active);
    }

    function sweepReserves() external nonReentrant {
        accrueInterest();
        uint256 amt = totalReserves;
        if (amt == 0) revert ZeroAmount();
        totalReserves = 0;
        USDC.safeTransfer(treasury, amt);
        emit ReservesSwept(treasury, amt);
    }

    function setPriceFloor(uint256 floor_) external onlyGuardian {
        priceFloor = floor_;
        emit PriceFloorUpdated(floor_);
    }
    function setRiskParams(uint256 _maxLtv, uint256 _liqThreshold, uint256 _liqBonus, uint256 _closeFactor) external onlyGuardian {
        if (_liqThreshold > 9_000 || _maxLtv >= _liqThreshold || _liqBonus > 2_000 || _closeFactor > BPS) revert InvalidConfig();
        maxLTVBps = _maxLtv;
        liquidationThresholdBps = _liqThreshold;
        liquidationBonusBps = _liqBonus;
        closeFactorBps = _closeFactor;
        emit RiskParamsUpdated(_maxLtv, _liqThreshold, _liqBonus, _closeFactor);
    }
    function setRateModel(uint256 _base, uint256 _s1, uint256 _s2, uint256 _kink) external onlyGuardian {
        if (_kink > BPS) revert InvalidConfig();
        baseRateBps = _base; slope1Bps = _s1; slope2Bps = _s2; kinkBps = _kink;
        emit RateModelUpdated(_base, _s1, _s2, _kink);
    }
    function setOracle(ISincPriceOracle _oracle) external onlyGuardian {
        if (address(_oracle) == address(0)) revert InvalidConfig();
        oracle = _oracle;
        emit OracleUpdated(address(_oracle));
    }
    function setSwapRouter(ISincSwapRouter _router) external onlyGuardian {
        if (address(_router) == address(0)) revert InvalidConfig();
        swapRouter = _router;
        emit SwapRouterUpdated(address(_router));
    }
    function setTreasury(address _treasury) external onlyGuardian {
        if (_treasury == address(0)) revert InvalidConfig();
        treasury = _treasury;
        emit TreasuryUpdated(_treasury);
    }
    function pause() external onlyGuardian { _pause(); }
    function unpause() external onlyGuardian { _unpause(); }
}
