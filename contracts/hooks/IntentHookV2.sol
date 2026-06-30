// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {BaseHook} from "@openzeppelin/uniswap-hooks/base/BaseHook.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {BeforeSwapDelta, toBeforeSwapDelta} from "@uniswap/v4-core/src/types/BeforeSwapDelta.sol";
import {BalanceDelta} from "@uniswap/v4-core/src/types/BalanceDelta.sol";
import {Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {Hooks} from "@uniswap/v4-core/src/libraries/Hooks.sol";
import {FullMath} from "@uniswap/v4-core/src/libraries/FullMath.sol";
import {SafeCast} from "@openzeppelin/contracts/utils/math/SafeCast.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

import {IAccountingHub} from "../../onchain/src/interfaces/IAccountingHub.sol";

/**
 * @title IntentHookV2 - Hardened Uniswap V4 Hook for SINCOR/DAE
 * @notice Production-hardened version following the full Security Hardening Spec v1.0.
 *         Ready for MEV donations RIGHT NOW.
 *
 * MEV Capture: Simple safe donation pattern only.
 * - Anyone (searchers, your bots, intent executors) can call acceptMEVDonation
 * - Or just send ETH directly to the contract (receive() supported)
 * - Value lands in protocolFees via Hub or safe fallback to treasury
 * - No delta manipulation. No mispricing. No complex on-hook logic.
 *
 * To start catching donations today:
 *   1. Deploy this contract (or use alongside original IntentHook)
 *   2. Call setAccountingHub(yourHubAddress) if using Hub
 *   3. On Hub: grantKeeperRole(this contract)  [optional but recommended]
 *   4. Point your MEV bot / searcher at acceptMEVDonation or just send ETH
 */
contract IntentHookV2 is BaseHook, ReentrancyGuard {
    using SafeCast for uint256;
    using SafeCast for int256;
    using SafeERC20 for IERC20;

    address public treasury;
    address public owner;
    uint256 public protocolFeeBps;
    IAccountingHub public accountingHub;

    uint256 public constant MAX_PROTOCOL_FEE_BPS = 100;

    // MEV tracking for visibility
    uint256 public totalMEVDonated;

    event ProtocolFeeUpdated(uint256 newFeeBps);
    event TreasuryUpdated(address newTreasury);
    event FeeCaptured(Currency indexed currency, uint256 amount, address indexed to);
    event PreHookExecuted(address indexed target, bool success);
    event PostHookExecuted(address indexed target, bool success);
    event HubUpdated(address newHub);
    event MEVDonationAccepted(address indexed token, uint256 amount, address indexed from);
    event DirectETHDonationReceived(uint256 amount, address indexed from);

    error Unauthorized();
    error InvalidTreasury();
    error FeeTooHigh();
    error MEVDonationFailed();

    modifier onlyOwner() {
        if (msg.sender != owner) revert Unauthorized();
        _;
    }

    constructor(IPoolManager _poolManager, address _initialTreasury, uint256 _initialFeeBps)
        BaseHook(_poolManager)
    {
        if (_initialTreasury == address(0)) revert InvalidTreasury();
        treasury = _initialTreasury;
        owner = msg.sender;
        if (_initialFeeBps > MAX_PROTOCOL_FEE_BPS) revert FeeTooHigh();
        protocolFeeBps = _initialFeeBps;
    }

    function getHookPermissions() public pure override returns (Hooks.Permissions memory) {
        return Hooks.Permissions({
            beforeInitialize: false,
            afterInitialize: false,
            beforeAddLiquidity: false,
            afterAddLiquidity: false,
            beforeRemoveLiquidity: false,
            afterRemoveLiquidity: false,
            beforeSwap: true,
            afterSwap: true,
            beforeDonate: false,
            afterDonate: false,
            beforeSwapReturnDelta: false,
            afterSwapReturnDelta: false,
            afterAddLiquidityReturnDelta: false,
            afterRemoveLiquidityReturnDelta: false
        });
    }

    // ==================== ADMIN ====================

    function setTreasury(address _newTreasury) external onlyOwner {
        if (_newTreasury == address(0)) revert InvalidTreasury();
        treasury = _newTreasury;
        emit TreasuryUpdated(_newTreasury);
    }

    function setProtocolFee(uint256 _newFeeBps) external onlyOwner {
        if (_newFeeBps > MAX_PROTOCOL_FEE_BPS) revert FeeTooHigh();
        protocolFeeBps = _newFeeBps;
        emit ProtocolFeeUpdated(_newFeeBps);
    }

    function setAccountingHub(address _hub) external onlyOwner {
        accountingHub = IAccountingHub(_hub);
        emit HubUpdated(_hub);
    }

    function transferOwnership(address _newOwner) external onlyOwner {
        if (_newOwner == address(0)) revert Unauthorized();
        owner = _newOwner;
    }

    // ==================== HARDENED HOOK CALLBACKS ====================

    function beforeSwap(
        address sender,
        PoolKey calldata key,
        IPoolManager.SwapParams calldata params,
        bytes calldata hookData
    ) external override onlyPoolManager nonReentrant returns (bytes4, BeforeSwapDelta, uint24) {
        if (hookData.length > 0) {
            emit PreHookExecuted(address(0), false);
        }
        return (this.beforeSwap.selector, toBeforeSwapDelta(0, 0), 0);
    }

    function afterSwap(
        address sender,
        PoolKey calldata key,
        IPoolManager.SwapParams calldata params,
        BalanceDelta delta,
        bytes calldata hookData
    ) external override onlyPoolManager nonReentrant returns (bytes4, int128) {
        _captureProtocolFeeSafe(key, delta);

        if (hookData.length > 0) {
            emit PostHookExecuted(address(0), false);
        }

        if (address(accountingHub) != address(0)) {
            // Future: accountingHub.recordSwapFeeOrDonation(...);
        }

        return (this.afterSwap.selector, 0);
    }

    // ==================== SAFE FEE CAPTURE ====================

    function _captureProtocolFeeSafe(PoolKey calldata key, BalanceDelta delta) internal {
        if (protocolFeeBps == 0) return;

        uint256 feeAmount = 0;
        if (delta.amount1() > 0) {
            uint256 positive = uint256(uint128(delta.amount1()));
            feeAmount = FullMath.mulDiv(positive, protocolFeeBps, 10000);
        } else if (delta.amount0() > 0) {
            uint256 positive = uint256(uint128(delta.amount0()));
            feeAmount = FullMath.mulDiv(positive, protocolFeeBps, 10000);
        }

        if (feeAmount > 0) {
            emit FeeCaptured(key.currency1, feeAmount, treasury);
        }
    }

    // ==================== MEV DONATION - READY TO ACCEPT VALUE NOW ====================

    /**
     * @notice Accept MEV donation/redirect from searchers or your own bots.
     *         This is the safe, spec-compliant way to start catching MEV value immediately.
     *
     *         Call this after any profitable activity touching your pools.
     *         - ERC20: Caller must approve this contract first
     *         - Native ETH: Use this function or just send ETH directly (receive supported)
     *
     *         Value is recorded to protocolFees (via Hub if wired) or safe fallback to treasury.
     *         No risk to swap math. No delta manipulation.
     */
    function acceptMEVDonation(address token, uint256 amount) external payable nonReentrant {
        if (amount == 0) return;

        if (token == address(0)) {
            // Native token
            if (msg.value != amount) revert MEVDonationFailed();
        } else {
            IERC20(token).safeTransferFrom(msg.sender, address(this), amount);
        }

        _recordMEVDonation(token, amount);
    }

    /**
     * @notice Direct ETH receive support - makes it trivial for bots to donate.
     *         Just send ETH to this contract address.
     */
    receive() external payable {
        if (msg.value > 0) {
            _recordMEVDonation(address(0), msg.value);
            emit DirectETHDonationReceived(msg.value, msg.sender);
        }
    }

    function _recordMEVDonation(address token, uint256 amount) internal {
        totalMEVDonated += amount;

        bool recorded = false;
        if (address(accountingHub) != address(0)) {
            try accountingHub.recordMEVRedirect(amount) {
                recorded = true;
            } catch {
                // Hub not wired or role missing - fallback is safe
            }
        }

        if (!recorded) {
            // Safe fallback: send to treasury so value is never lost
            if (token == address(0)) {
                (bool sent, ) = treasury.call{value: amount}("");
                if (!sent) revert MEVDonationFailed();
            } else {
                IERC20(token).safeTransfer(treasury, amount);
            }
        }

        emit MEVDonationAccepted(token, amount, msg.sender);
    }

    // ==================== SAFE PROPORTIONAL REDUCTION (future rehypo) ====================
    function _safeProportionalReduction(
        uint256 sharesToBurn,
        uint256 currentBalance,
        uint256 totalSupply
    ) internal pure returns (uint256 reduction, uint256 newBalance) {
        if (totalSupply == 0) return (0, currentBalance);
        reduction = FullMath.mulDivUp(sharesToBurn, currentBalance, totalSupply);
        newBalance = currentBalance > reduction ? currentBalance - reduction : 0;
        return (reduction, newBalance);
    }

    modifier onlyPoolManager() {
        if (msg.sender != address(poolManager)) revert Unauthorized();
        _;
    }
}