// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {ERC4626} from "@openzeppelin/contracts/token/ERC20/extensions/ERC4626.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";

import {ISinc4626Vault, ISincStrategyRouter, ISincFeeFlywheel} from "../interfaces/ISincVaultSystem.sol";
import {SincConstants} from "./SincConstants.sol";

contract Sinc4626Vault is ERC4626, AccessControl, ReentrancyGuard, ISinc4626Vault {
    using SafeERC20 for IERC20;

    bytes32 public constant KEEPER_ROLE = keccak256("KEEPER_ROLE");
    bytes32 public constant STRATEGIST_ROLE = keccak256("STRATEGIST_ROLE");

    ISincStrategyRouter public strategyRouter;
    ISincFeeFlywheel public feeFlywheel;

    uint16 public idleBufferBps;
    uint16 public performanceFeeBps;
    uint256 public realizedLosses;
    address public preferredGauge;

    /// @notice P18 fee-rule switch. DEFAULT OFF: while `feesEnabled` is
    ///         false, harvest() charges no performance fee and no
    ///         management fee — the vault's standing no-fee reality for
    ///         outside depositors. Flipping it on is an admin act; the
    ///         fee clock settles at the flip (see setFeesEnabled), so a
    ///         disabled period is never back-charged.
    bool public feesEnabled;
    /// @notice Management fee, bps of AUM per year (P18 catalog price:
    ///         1% = 100 bps), accrued pro-rata over time while enabled.
    uint16 public managementFeeBps = 100;
    /// @notice When the management-fee clock last settled. Advances on
    ///         every harvest and on every enable/disable flip.
    uint256 public lastFeeTimestamp;

    event Harvested(uint256 yieldAmount, uint256 feeAmount, uint256 lossAmount);
    event FeesEnabledSet(bool enabled);
    event ManagementFeeBpsSet(uint16 bps);
    event ManagementFeeAccrued(uint256 feeAmount, uint256 elapsedSeconds);

    error InvalidConfig();

    constructor(
        address asset_,
        string memory name_,
        string memory symbol_,
        address admin_,
        address router_,
        address flywheel_,
        uint16 idleBufferBps_,
        uint16 performanceFeeBps_,
        address preferredGauge_
    ) ERC20(name_, symbol_) ERC4626(IERC20(asset_)) {
        if (
            asset_ == address(0) || admin_ == address(0) || router_ == address(0) || flywheel_ == address(0)
                || idleBufferBps_ > SincConstants.BPS_DENOM || performanceFeeBps_ > SincConstants.BPS_DENOM
        ) revert InvalidConfig();

        strategyRouter = ISincStrategyRouter(router_);
        feeFlywheel = ISincFeeFlywheel(flywheel_);
        idleBufferBps = idleBufferBps_;
        performanceFeeBps = performanceFeeBps_;
        preferredGauge = preferredGauge_;
        lastFeeTimestamp = block.timestamp;

        _grantRole(DEFAULT_ADMIN_ROLE, admin_);
        _grantRole(KEEPER_ROLE, admin_);
        _grantRole(STRATEGIST_ROLE, admin_);
    }

    function router() external view returns (address) {
        return address(strategyRouter);
    }

    /// @dev ERC4626 and ISinc4626Vault both define asset(); the explicit
    ///      override is required for the contract to compile (it simply
    ///      delegates to the ERC4626 implementation).
    function asset() public view override(ERC4626, ISinc4626Vault) returns (address) {
        return ERC4626.asset();
    }

    function totalAssets() public view override(ERC4626, ISinc4626Vault) returns (uint256) {
        uint256 idleAssets = IERC20(asset()).balanceOf(address(this));
        uint256 managedAssets = strategyRouter.totalManagedAssets();
        uint256 grossAssets = idleAssets + managedAssets;
        if (realizedLosses >= grossAssets) return 0;
        return grossAssets - realizedLosses;
    }

    function setIdleBufferBps(uint16 bps) external onlyRole(STRATEGIST_ROLE) {
        if (bps > SincConstants.BPS_DENOM) revert InvalidConfig();
        idleBufferBps = bps;
    }

    function setPerformanceFeeBps(uint16 bps) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (bps > SincConstants.BPS_DENOM) revert InvalidConfig();
        performanceFeeBps = bps;
    }

    /// @notice Flip the P18 fee-rule switch. The fee clock settles at the
    ///         flip: time spent disabled never accrues, and time spent
    ///         enabled is measured from the flip, not from deployment.
    function setFeesEnabled(bool enabled) external onlyRole(DEFAULT_ADMIN_ROLE) {
        lastFeeTimestamp = block.timestamp;
        feesEnabled = enabled;
        emit FeesEnabledSet(enabled);
    }

    function setManagementFeeBps(uint16 bps) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (bps > SincConstants.BPS_DENOM) revert InvalidConfig();
        managementFeeBps = bps;
        emit ManagementFeeBpsSet(bps);
    }

    /// @notice Management fee accrued since the fee clock last settled,
    ///         at the current totalAssets. Zero while the switch is off.
    function accruedManagementFee() public view returns (uint256) {
        if (!feesEnabled || managementFeeBps == 0) return 0;
        uint256 elapsed = block.timestamp - lastFeeTimestamp;
        if (elapsed == 0) return 0;
        return Math.mulDiv(
            totalAssets(), uint256(managementFeeBps) * elapsed, uint256(SincConstants.BPS_DENOM) * 365 days
        );
    }

    function setPreferredGauge(address gauge) external onlyRole(STRATEGIST_ROLE) {
        preferredGauge = gauge;
    }

    function rebalance() external onlyRole(KEEPER_ROLE) {
        _deployIdle();
    }

    function deposit(uint256 assets, address receiver) public override nonReentrant returns (uint256 shares) {
        shares = super.deposit(assets, receiver);
        _deployIdle();
    }

    function mint(uint256 shares, address receiver) public override nonReentrant returns (uint256 assets) {
        assets = super.mint(shares, receiver);
        _deployIdle();
    }

    function withdraw(uint256 assets, address receiver, address owner)
        public
        override
        nonReentrant
        returns (uint256 shares)
    {
        _ensureIdle(assets);
        shares = super.withdraw(assets, receiver, owner);
    }

    function redeem(uint256 shares, address receiver, address owner)
        public
        override
        nonReentrant
        returns (uint256 assets)
    {
        assets = previewRedeem(shares);
        _ensureIdle(assets);
        assets = super.redeem(shares, receiver, owner);
    }

    function harvest() external onlyRole(KEEPER_ROLE) nonReentrant returns (uint256 realizedYield, uint256 feeAmount) {
        // P18: settle the management-fee clock against the pre-harvest
        // asset base, then advance it. Every harvest settles the clock
        // whether or not fees are enabled, so disabled time can never
        // accrue retroactively when the switch is flipped on later.
        uint256 mgmtFee = accruedManagementFee();
        uint256 feeElapsed = block.timestamp - lastFeeTimestamp;
        lastFeeTimestamp = block.timestamp;

        uint256 preAssets = totalAssets();
        (, uint256 loss) = strategyRouter.harvestStrategies();

        if (loss > 0) {
            realizedLosses += loss;
        }

        uint256 postAssets = totalAssets();
        realizedYield = postAssets > preAssets ? postAssets - preAssets : 0;
        // The performance fee exists only while the switch is on.
        uint256 perfFee =
            feesEnabled ? Math.mulDiv(realizedYield, performanceFeeBps, SincConstants.BPS_DENOM) : 0;

        // The flywheel pulls fees from the vault's idle balance. Cap the
        // total at what is actually idle — performance leg first (it is
        // backed by the yield just harvested), management leg takes the
        // remainder — so a harvest can never revert on a fully-deployed
        // vault. Any uncollected management remainder is forgiven: the
        // clock has settled either way.
        uint256 idle = IERC20(asset()).balanceOf(address(this));
        feeAmount = perfFee + mgmtFee;
        if (feeAmount > idle) {
            mgmtFee = idle > perfFee ? idle - perfFee : 0;
            feeAmount = perfFee + mgmtFee;
        }

        if (feeAmount > 0) {
            IERC20(asset()).forceApprove(address(feeFlywheel), feeAmount);
            feeFlywheel.processFees(asset(), feeAmount, preferredGauge);
        }

        _deployIdle();
        emit Harvested(realizedYield, feeAmount, loss);
        if (mgmtFee > 0) emit ManagementFeeAccrued(mgmtFee, feeElapsed);
    }

    function _ensureIdle(uint256 needed) internal {
        uint256 idle = IERC20(asset()).balanceOf(address(this));
        if (idle >= needed) return;

        uint256 toPull = needed - idle;
        strategyRouter.deallocate(toPull);
    }

    function _deployIdle() internal {
        uint256 idle = IERC20(asset()).balanceOf(address(this));
        if (idle == 0) return;

        uint256 targetIdle = Math.mulDiv(totalAssets(), idleBufferBps, SincConstants.BPS_DENOM);
        if (idle <= targetIdle) return;

        uint256 toAllocate = idle - targetIdle;
        IERC20(asset()).safeTransfer(address(strategyRouter), toAllocate);
        strategyRouter.allocate(toAllocate);
    }
}
