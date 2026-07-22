// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";

import {IFluidLiquidity, IFluidDexFactory, IFluidDexT1, IFluidVaultT1, IFToken} from "./FluidInterfaces.sol";

/// @title  SincFluidAdapter — vault deposit/withdraw extension onto Fluid's unified layer (Base)
/// @notice Three-stage amplifier for SINC liquidity:
///
///   Stage 1 (live today, permissionless): LP USDC is parked in Fluid fUSDC,
///           earning lending yield while idle. SINC cannot be supplied to Fluid
///           lending (no fSINC market) — it only becomes productive via Stage 2.
///
///   Stage 2 (after Fluid governance deploys/lists the SINC-USDC DEX): the pair
///           is supplied via DexT1.deposit() as SMART COLLATERAL — the position is
///           the LP liquidity itself and earns swap fees while it can back borrows.
///           deployDex is deployer-auth gated by Fluid governance (verified); the
///           pair must be created/listed by Fluid or a governance-authorized deployer.
///
///   Stage 3 (treasury-only, after Stage 2): smart debt / VaultT2 operate() lets
///           treasury borrow against the smart-collateral LP to compound depth.
///           Guardian-gated. Per-user borrowing is deliberately NOT pooled here —
///           each user's debt position must be their own Fluid account.
///
/// @dev    SINC has 8 decimals; USDC has 6. All amounts are raw token units.
///         Token approvals target the Fluid Liquidity contract (the layer that
///         settles every Fluid protocol), fUSDC for the lending leg.
///         Wired into SharedLiquidityVault as a strategy backer (registerStrategy).
contract SincFluidAdapter is ReentrancyGuard, Pausable {
    using SafeERC20 for IERC20;

    // ---------------- Fluid Base (8453) — verified against fluid-contracts-public ----------------
    IFluidLiquidity public constant LIQUIDITY = IFluidLiquidity(0x52Aa899454998Be5b000Ad077a46Bbe360F4e497);
    IFluidDexFactory public constant DEX_FACTORY = IFluidDexFactory(0x91716C4EDA1Fb55e84Bf8b4c7085f84285c19085);
    IFToken public constant F_USDC = IFToken(0xf42f5795D9ac7e9D757dB633D693cD548Cfd9169);
    IFToken public constant F_WETH = IFToken(0x9272D6153133175175Bc276512B2336BE3931CE9);
    address public constant DEX_RESOLVER = 0x11D80CfF056Cef4F9E6d23da8672fE9873e5cC07;
    address public constant LENDING_RESOLVER = 0x48D32f49aFeAEC7AE66ad7B9264f446fc11a1569;

    IERC20 public immutable SINC;
    IERC20 public immutable USDC;
    address public immutable guardian;
    address public treasury;

    /// @notice SINC/USDC Fluid DEX pool — address(0) until Fluid governance lists it
    address public fluidDex;
    /// @notice Fluid VaultT2 (smart col SINC-USDC LP / debt) — address(0) until listed
    address public smartVault;

    // per-user accounting (adapter holds the Fluid positions; users hold claims)
    mapping(address user => uint256 fShares) public fUsdcShares;
    mapping(address user => uint256 colShares) public dexColShares;
    uint256 public totalDexColShares;

    // treasury-level smart-debt accounting
    uint256 public totalDebtShares;
    uint256 public lastNftId;

    event FluidDepositUSDC(address indexed user, uint256 assets, uint256 shares);
    event FluidWithdrawUSDC(address indexed user, uint256 assets, uint256 shares);
    event DexSupplied(address indexed user, address indexed dex, uint256 token0Amt, uint256 token1Amt, uint256 shares);
    event DexWithdrawn(address indexed user, address indexed dex, uint256 token0Amt, uint256 token1Amt, uint256 shares);
    event SmartDebtBorrowed(address indexed dex, uint256 token0Amt, uint256 token1Amt, uint256 shares, address to);
    event SmartDebtRepaid(address indexed dex, uint256 token0Amt, uint256 token1Amt, uint256 shares);
    event VaultOperated(address indexed vault, uint256 nftId, int256 newCol, int256 newDebt, address to);
    event FluidDexUpdated(address dex);
    event SmartVaultUpdated(address vault);
    event TreasuryUpdated(address treasury);
    event Rescued(address indexed token, address indexed to, uint256 amount);

    error OnlyGuardian();
    error ZeroAmount();
    error DexNotSet();
    error VaultNotSet();
    error InsufficientShares(uint256 requested, uint256 available);
    error ZeroAddress();

    modifier onlyGuardian() {
        if (msg.sender != guardian) revert OnlyGuardian();
        _;
    }

    constructor(IERC20 _sinc, IERC20 _usdc, address _guardian, address _treasury) {
        if (address(_sinc) == address(0) || address(_usdc) == address(0) || _guardian == address(0) || _treasury == address(0)) {
            revert ZeroAddress();
        }
        SINC = _sinc;
        USDC = _usdc;
        guardian = _guardian;
        treasury = _treasury;

        // One-time max approvals. Fluid protocols settle through the Liquidity
        // layer (supply side pulls tokens from the caller). fUSDC is the ERC-4626 leg.
        _usdc.forceApprove(address(F_USDC), type(uint256).max);
        _usdc.forceApprove(address(LIQUIDITY), type(uint256).max);
        _sinc.forceApprove(address(LIQUIDITY), type(uint256).max);
    }

    // ==================== STAGE 1 — FLUID LENDING (permissionless, live) ====================

    /// @notice Deposit USDC into Fluid fUSDC. Shares are held by the adapter and
    ///         accounted per user. Live multiplier: reads via LendingResolver / convertToAssets.
    function depositUSDC(uint256 assets) external nonReentrant whenNotPaused returns (uint256 shares) {
        if (assets == 0) revert ZeroAmount();
        USDC.safeTransferFrom(msg.sender, address(this), assets);
        shares = F_USDC.deposit(assets, address(this));
        fUsdcShares[msg.sender] += shares;
        emit FluidDepositUSDC(msg.sender, assets, shares);
    }

    /// @notice Redeem fUSDC shares back to USDC for the caller.
    function withdrawUSDC(uint256 shares) external nonReentrant returns (uint256 assets) {
        if (shares == 0) revert ZeroAmount();
        uint256 owned = fUsdcShares[msg.sender];
        if (shares > owned) revert InsufficientShares(shares, owned);
        fUsdcShares[msg.sender] = owned - shares;
        assets = F_USDC.redeem(shares, msg.sender, address(this));
        emit FluidWithdrawUSDC(msg.sender, assets, shares);
    }

    /// @notice Live USDC value of a user's Fluid lending position (6 decimals).
    function userValueUSDC(address user) external view returns (uint256) {
        uint256 s = fUsdcShares[user];
        return s == 0 ? 0 : F_USDC.convertToAssets(s);
    }

    // ==================== STAGE 2 — FLUID DEX SMART COLLATERAL (post-governance) ====================

    /// @notice Supply SINC + USDC into the SINC-USDC Fluid DEX as smart collateral.
    ///         The resulting position IS the pool's LP liquidity and earns swap fees.
    ///         token0/token1 amounts are in pool order — check DexResolver.constantsView()
    ///         (SINC 0x9C8c… > USDC 0x8335…, so SINC is likely token1; confirm on-chain).
    function supplyToDex(uint256 sincAmt, uint256 usdcAmt, uint256 minShares)
        external nonReentrant whenNotPaused returns (uint256 shares)
    {
        address dex = fluidDex;
        if (dex == address(0)) revert DexNotSet();
        if (sincAmt == 0 && usdcAmt == 0) revert ZeroAmount();

        if (sincAmt > 0) SINC.safeTransferFrom(msg.sender, address(this), sincAmt);
        if (usdcAmt > 0) USDC.safeTransferFrom(msg.sender, address(this), usdcAmt);

        shares = IFluidDexT1(dex).deposit(sincAmt, usdcAmt, minShares, false);
        dexColShares[msg.sender] += shares;
        totalDexColShares += shares;
        emit DexSupplied(msg.sender, dex, sincAmt, usdcAmt, shares);
    }

    /// @notice Withdraw smart-collateral liquidity; underlying tokens go to the caller.
    function withdrawFromDex(uint256 sincAmt, uint256 usdcAmt, uint256 maxShares)
        external nonReentrant returns (uint256 shares)
    {
        address dex = fluidDex;
        if (dex == address(0)) revert DexNotSet();
        shares = IFluidDexT1(dex).withdraw(sincAmt, usdcAmt, maxShares, msg.sender);
        uint256 owned = dexColShares[msg.sender];
        if (shares > owned) revert InsufficientShares(shares, owned);
        dexColShares[msg.sender] = owned - shares;
        totalDexColShares -= shares;
        emit DexWithdrawn(msg.sender, dex, sincAmt, usdcAmt, shares);
    }

    // ==================== STAGE 3 — SMART DEBT / VAULT (treasury only) ====================

    /// @notice Treasury borrows against smart-collateral LP. Debt itself is deployed
    ///         as LP liquidity (Fluid smart debt) once enabled on the pair.
    function borrowSmartDebt(uint256 shares, uint256 minToken0Borrow, uint256 minToken1Borrow, address to)
        external onlyGuardian nonReentrant whenNotPaused
    {
        address dex = fluidDex;
        if (dex == address(0)) revert DexNotSet();
        (uint256 a0, uint256 a1) = IFluidDexT1(dex).borrowPerfect(shares, minToken0Borrow, minToken1Borrow, to == address(0) ? treasury : to);
        totalDebtShares += shares;
        emit SmartDebtBorrowed(dex, a0, a1, shares, to);
    }

    /// @notice Repay smart debt. Pulls repayment tokens from the guardian caller.
    function paybackSmartDebt(uint256 token0Amt, uint256 token1Amt, uint256 minShares)
        external onlyGuardian nonReentrant returns (uint256 shares)
    {
        address dex = fluidDex;
        if (dex == address(0)) revert DexNotSet();
        if (token0Amt > 0) IERC20(IFluidDexT1(dex) == IFluidDexT1(dex) ? address(SINC) : address(SINC)).forceApprove(address(LIQUIDITY), type(uint256).max); // idempotent
        if (token0Amt > 0) SINC.safeTransferFrom(msg.sender, address(this), token0Amt);
        if (token1Amt > 0) USDC.safeTransferFrom(msg.sender, address(this), token1Amt);
        shares = IFluidDexT1(dex).payback(token0Amt, token1Amt, minShares, false);
        totalDebtShares = shares > totalDebtShares ? 0 : totalDebtShares - shares;
        emit SmartDebtRepaid(dex, token0Amt, token1Amt, shares);
    }

    /// @notice Direct Fluid Vault operate (VaultT2/T4 once listed). Unified entry:
    ///         nftId==0 opens a position; newCol/newDebt set target deltas.
    ///         Collateral pulls come from this adapter's balance (prefund via treasury).
    function vaultOperate(uint256 nftId, int256 newCol, int256 newDebt, address to)
        external onlyGuardian nonReentrant whenNotPaused returns (uint256 nftIdOut)
    {
        address v = smartVault;
        if (v == address(0)) revert VaultNotSet();
        (nftIdOut,,) = IFluidVaultT1(v).operate(nftId, newCol, newDebt, to == address(0) ? treasury : to);
        if (nftId == 0) lastNftId = nftIdOut;
        emit VaultOperated(v, nftIdOut, newCol, newDebt, to);
    }

    /// @notice Forward a deployDex request. WILL REVERT until Fluid governance grants
    ///         deployer access to guardian/treasury (isDeployer). Kept as the exact
    ///         call so the multisig can execute it the moment access is granted.
    function deploySincUsdcDex(address dexDeploymentLogic, bytes calldata deploymentData)
        external onlyGuardian returns (address dex)
    {
        dex = DEX_FACTORY.deployDex(dexDeploymentLogic, deploymentData);
        fluidDex = dex;
        emit FluidDexUpdated(dex);
    }

    // ==================== ADMIN ====================

    function setFluidDex(address dex) external onlyGuardian {
        fluidDex = dex;
        emit FluidDexUpdated(dex);
    }

    function setSmartVault(address v) external onlyGuardian {
        smartVault = v;
        emit SmartVaultUpdated(v);
    }

    function setTreasury(address t) external onlyGuardian {
        if (t == address(0)) revert ZeroAddress();
        treasury = t;
        emit TreasuryUpdated(t);
    }

    function pause() external onlyGuardian { _pause(); }
    function unpause() external onlyGuardian { _unpause(); }

    /// @notice Emergency exit path: pull any token balance to treasury. Does NOT touch
    ///         Fluid-internal positions (those unwind via withdrawFromDex / payback).
    function rescue(address token, uint256 amount) external onlyGuardian {
        IERC20(token).safeTransfer(treasury, amount);
        emit Rescued(token, treasury, amount);
    }
}
