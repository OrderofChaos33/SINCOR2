// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";

import {ISincStrategyRouter, ISincStrategy} from "../interfaces/ISincVaultSystem.sol";
import {SincConstants} from "./SincConstants.sol";

contract SincStrategyRouter is ISincStrategyRouter, AccessControl, ReentrancyGuard, Pausable {
    using SafeERC20 for IERC20;

    bytes32 public constant STRATEGIST_ROLE = keccak256("STRATEGIST_ROLE");

    IERC20 public immutable assetToken;
    address public vault;

    uint16 public maxStrategyCount;

    address[] public strategies;
    mapping(address strategy => StrategyConfig) public strategyConfig;
    mapping(address strategy => uint256) public managedByStrategy;
    mapping(address strategy => uint256) public lastHarvestAt;

    uint256 internal _totalManagedAssets;

    error InvalidConfig();
    error Unauthorized();
    error StrategyExists();
    error StrategyMissing();
    error StrategyLimitReached();

    modifier onlyVault() {
        if (msg.sender != vault) revert Unauthorized();
        _;
    }

    constructor(address asset_, address admin_, uint16 maxStrategyCount_) {
        if (asset_ == address(0) || admin_ == address(0) || maxStrategyCount_ == 0) revert InvalidConfig();
        assetToken = IERC20(asset_);
        maxStrategyCount = maxStrategyCount_;

        _grantRole(DEFAULT_ADMIN_ROLE, admin_);
        _grantRole(STRATEGIST_ROLE, admin_);
    }

    function setVault(address vault_) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (vault_ == address(0)) revert InvalidConfig();
        vault = vault_;
    }

    function setMaxStrategyCount(uint16 newMax) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (newMax == 0) revert InvalidConfig();
        maxStrategyCount = newMax;
    }

    function setPaused(bool paused_) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (paused_) _pause();
        else _unpause();
    }

    function strategyCount() external view returns (uint256) {
        return strategies.length;
    }

    function addStrategy(address strategy, StrategyConfig calldata cfg) external onlyRole(STRATEGIST_ROLE) {
        if (strategy == address(0)) revert InvalidConfig();
        if (strategies.length >= maxStrategyCount) revert StrategyLimitReached();
        if (strategyConfig[strategy].debtCap != 0 || strategyConfig[strategy].enabled) revert StrategyExists();
        if (cfg.targetWeightBps > SincConstants.BPS_DENOM || cfg.maxDrawdownBps > SincConstants.BPS_DENOM) revert InvalidConfig();
        if (ISincStrategy(strategy).asset() != address(assetToken)) revert InvalidConfig();

        strategyConfig[strategy] = cfg;
        strategies.push(strategy);
        emit StrategyAdded(strategy, cfg);
    }

    function updateStrategy(address strategy, StrategyConfig calldata cfg) external onlyRole(STRATEGIST_ROLE) {
        if (!_exists(strategy)) revert StrategyMissing();
        if (cfg.targetWeightBps > SincConstants.BPS_DENOM || cfg.maxDrawdownBps > SincConstants.BPS_DENOM) revert InvalidConfig();
        strategyConfig[strategy] = cfg;
        emit StrategyUpdated(strategy, cfg);
    }

    function allocate(uint256 amount) external onlyVault nonReentrant whenNotPaused {
        if (amount == 0) return;

        uint256 enabledWeight = _enabledWeight();
        if (enabledWeight == 0) return;

        uint256 allocLeft = amount;
        for (uint256 i; i < strategies.length; ++i) {
            address strategy = strategies[i];
            StrategyConfig memory cfg = strategyConfig[strategy];
            if (!cfg.enabled || cfg.targetWeightBps == 0) continue;

            uint256 desired = Math.mulDiv(amount, cfg.targetWeightBps, enabledWeight);
            uint256 headroom = cfg.debtCap > managedByStrategy[strategy] ? cfg.debtCap - managedByStrategy[strategy] : 0;
            uint256 toAlloc = desired < headroom ? desired : headroom;
            if (toAlloc > allocLeft) toAlloc = allocLeft;
            if (toAlloc == 0) continue;

            assetToken.forceApprove(strategy, toAlloc);
            uint256 deployed = ISincStrategy(strategy).deposit(toAlloc);
            managedByStrategy[strategy] += deployed;
            _totalManagedAssets += deployed;
            allocLeft -= toAlloc;
            if (allocLeft == 0) break;
        }

        emit Rebalanced(block.timestamp);
    }

    function deallocate(uint256 amount) external onlyVault nonReentrant returns (uint256 returnedAmount) {
        if (amount == 0) return 0;

        for (uint256 i; i < strategies.length; ++i) {
            if (returnedAmount >= amount) break;
            address strategy = strategies[i];
            StrategyConfig memory cfg = strategyConfig[strategy];
            if (!cfg.enabled) continue;

            uint256 need = amount - returnedAmount;
            uint256 got = ISincStrategy(strategy).withdraw(need, address(this));
            if (got == 0) continue;

            uint256 managed = managedByStrategy[strategy];
            managedByStrategy[strategy] = got > managed ? 0 : managed - got;
            _totalManagedAssets = got > _totalManagedAssets ? 0 : _totalManagedAssets - got;
            returnedAmount += got;
        }

        if (returnedAmount > 0) {
            assetToken.safeTransfer(msg.sender, returnedAmount);
        }
    }

    function totalManagedAssets() external view returns (uint256) {
        return _totalManagedAssets;
    }

    function harvestStrategies() external onlyVault nonReentrant returns (uint256 grossYield, uint256 loss) {
        for (uint256 i; i < strategies.length; ++i) {
            address strategy = strategies[i];
            StrategyConfig memory cfg = strategyConfig[strategy];
            if (!cfg.enabled) continue;
            if (cfg.harvestCooldown != 0 && block.timestamp < lastHarvestAt[strategy] + cfg.harvestCooldown) continue;

            (uint256 gain, uint256 strategyLoss) = ISincStrategy(strategy).harvest();
            lastHarvestAt[strategy] = block.timestamp;

            if (gain > 0) grossYield += gain;
            if (strategyLoss > 0) {
                uint256 managed = managedByStrategy[strategy];
                uint256 boundedLoss = strategyLoss > managed ? managed : strategyLoss;
                managedByStrategy[strategy] = managed - boundedLoss;
                _totalManagedAssets -= boundedLoss;
                loss += boundedLoss;
            }

            uint256 observed = ISincStrategy(strategy).totalAssets();
            uint256 previous = managedByStrategy[strategy];
            if (observed >= previous) {
                uint256 delta = observed - previous;
                managedByStrategy[strategy] = observed;
                _totalManagedAssets += delta;
            } else {
                uint256 deltaLoss = previous - observed;
                managedByStrategy[strategy] = observed;
                _totalManagedAssets = deltaLoss > _totalManagedAssets ? 0 : _totalManagedAssets - deltaLoss;
                loss += deltaLoss;
            }
        }
    }

    function _enabledWeight() internal view returns (uint256 w) {
        for (uint256 i; i < strategies.length; ++i) {
            StrategyConfig memory cfg = strategyConfig[strategies[i]];
            if (cfg.enabled) w += cfg.targetWeightBps;
        }
    }

    function _exists(address strategy) internal view returns (bool) {
        return strategyConfig[strategy].debtCap != 0 || strategyConfig[strategy].enabled;
    }
}
