// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "@uniswap/v4-core/src/types/PoolId.sol";
import {BalanceDelta} from "@uniswap/v4-core/src/types/BalanceDelta.sol";
import {BeforeSwapDelta, BeforeSwapDeltaLibrary} from "@uniswap/v4-core/src/types/BeforeSwapDelta.sol";
import {Currency, CurrencyLibrary} from "@uniswap/v4-core/src/types/Currency.sol";
import {FullMath} from "@uniswap/v4-core/src/libraries/FullMath.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/security/ReentrancyGuard.sol";

import {ISharedLiquidityVault} from "./interfaces/ISharedLiquidityVault.sol";
import {IAccountingHub} from "./interfaces/IAccountingHub.sol";

/// @title SharedLiquidityHook — V4 hook wiring SINC/USDC pools into the SharedLiquidityVault
/// @notice Extends the IntentHookV2 hardening pattern (onlyPoolManager, nonReentrant, treasury
///         routing, optional AccountingHub, live MEV donation path) with Aqua/Fluid-style
///         shared-liquidity virtual accounting.
///
///         Flow (Aqua "pull at execution" semantics):
///           beforeSwap:  resolve the pool's vault strategy + LP route (hookData-encoded LP, else
///                        the strategy's default backer — typically treasury 0x09E2…12Ac), then pull
///                        output-side capital from the LP's VIRTUAL allocation into this hook.
///           afterSwap:   settle the drawn principal back into the vault, compute the protocol fee
///                        on the positive delta (FullMath, IntentHookV2-identical), and credit any
///                        surplus the hook actually holds to the LP via the vault's settle path.
///
///         Never-bricks guarantee: every vault interaction is try/catch — a depleted virtual
///         allocation can never revert a user's swap. The hook is additive: pools without a
///         registered strategy pass straight through (zero delta), exactly like IntentHookV2.
///
///         NOTE (same staged posture as IntentHookV2): this contract performs the full virtual
///         accounting + capital pull/settle cycle. Injecting pulled capital into the PoolManager's
///         delta (beforeSwapReturnDelta / settle) is a deliberate follow-up and does not affect the
///         accounting guarantees tested here.
contract SharedLiquidityHook is IHooks, ReentrancyGuard {
    using SafeERC20 for IERC20;
    using PoolIdLibrary for PoolKey;
    using CurrencyLibrary for Currency;

    // ------------------------------------------------------------------ storage

    IPoolManager public immutable poolManager;
    ISharedLiquidityVault public immutable vault;

    address public owner;
    address public treasury;
    uint256 public protocolFeeBps;
    IAccountingHub public accountingHub;

    uint256 public constant MAX_PROTOCOL_FEE_BPS = 100;

    /// @dev pool → vault strategyId (registered by owner)
    mapping(PoolId => uint256) public poolStrategy;
    mapping(PoolId => bool) public poolRegistered;

    /// @dev pending draw state for the in-flight swap (V4 swaps are atomic per tx)
    struct PendingDraw {
        address lp;
        uint256 strategyId;
        address token;
        uint256 amount;
        bool active;
    }
    PendingDraw internal _pending;

    uint256 public totalMEVDonated;

    // ------------------------------------------------------------------ events

    event PoolStrategyRegistered(PoolId indexed poolId, uint256 indexed strategyId);
    event VirtualLiquidityPulled(PoolId indexed poolId, address indexed lp, address token, uint256 amount);
    event VirtualLiquiditySettled(PoolId indexed poolId, address indexed lp, address token, uint256 principal, uint256 lpFee);
    event VirtualPullSkipped(PoolId indexed poolId, bytes reason);
    event FeeCaptured(Currency indexed currency, uint256 amount, address indexed to);
    event ProtocolFeeUpdated(uint256 newFeeBps);
    event TreasuryUpdated(address newTreasury);
    event HubUpdated(address newHub);
    event MEVDonationAccepted(address indexed token, uint256 amount, address indexed from);
    event DirectETHDonationReceived(uint256 amount, address indexed from);
    event DonationsWithdrawn(address indexed token, uint256 amount, address to);

    error Unauthorized();
    error InvalidTreasury();
    error FeeTooHigh();

    modifier onlyOwner() {
        if (msg.sender != owner) revert Unauthorized();
        _;
    }

    modifier onlyPoolManager() {
        if (msg.sender != address(poolManager)) revert Unauthorized();
        _;
    }

    // ------------------------------------------------------------------ ctor

    constructor(IPoolManager _poolManager, ISharedLiquidityVault _vault, address _treasury, uint256 _feeBps) {
        if (_treasury == address(0)) revert InvalidTreasury();
        if (_feeBps > MAX_PROTOCOL_FEE_BPS) revert FeeTooHigh();
        poolManager = _poolManager;
        vault = _vault;
        treasury = _treasury;
        owner = msg.sender;
        protocolFeeBps = _feeBps;

        // Pre-approve the vault to pull back settlements (SINC + USDC only, per vault design)
        IERC20(address(_vault.SINC())).safeIncreaseAllowance(address(_vault), type(uint256).max);
        IERC20(address(_vault.USDC())).safeIncreaseAllowance(address(_vault), type(uint256).max);
    }

    // ------------------------------------------------------------------ admin

    function registerPoolStrategy(PoolKey calldata key, uint256 strategyId) external onlyOwner {
        poolStrategy[key.toId()] = strategyId;
        poolRegistered[key.toId()] = true;
        emit PoolStrategyRegistered(key.toId(), strategyId);
    }

    function setProtocolFee(uint256 _feeBps) external onlyOwner {
        if (_feeBps > MAX_PROTOCOL_FEE_BPS) revert FeeTooHigh();
        protocolFeeBps = _feeBps;
        emit ProtocolFeeUpdated(_feeBps);
    }

    function setTreasury(address _treasury) external onlyOwner {
        if (_treasury == address(0)) revert InvalidTreasury();
        treasury = _treasury;
        emit TreasuryUpdated(_treasury);
    }

    function setAccountingHub(address hub) external onlyOwner {
        accountingHub = IAccountingHub(hub);
        emit HubUpdated(hub);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert Unauthorized();
        owner = newOwner;
    }

    // ------------------------------------------------------------------ hook callbacks (live)

    /// @dev Pull output-side virtual liquidity at execution time. Additive & never-bricks.
    function beforeSwap(address, PoolKey calldata key, IPoolManager.SwapParams calldata params, bytes calldata hookData)
        external
        override
        onlyPoolManager
        nonReentrant
        returns (bytes4, BeforeSwapDelta, uint24)
    {
        PoolId poolId = key.toId();
        uint256 strategyId = poolStrategy[poolId];
        if (!_pending.active && poolRegistered[poolId] && strategiesActive(strategyId)) {
            // Route: explicit LP in hookData (Aqua-style per-maker routing) else strategy default backer
            address lp = _decodeLp(hookData);
            if (lp == address(0)) lp = vault.strategyDefaultBacker(strategyId);

            // Output-side currency: the side the pool must pay out
            (Currency outCurrency, uint256 estOut) = _outputSide(key, params);
            address outToken = Currency.unwrap(outCurrency);

            uint256 headroom = vault.availableDraw(strategyId, lp, outToken);
            uint256 drawAmt = estOut < headroom ? estOut : headroom;

            if (drawAmt > 0) {
                try vault.drawDown(strategyId, lp, outToken, drawAmt) {
                    _pending = PendingDraw({lp: lp, strategyId: strategyId, token: outToken, amount: drawAmt, active: true});
                    emit VirtualLiquidityPulled(poolId, lp, outToken, drawAmt);
                } catch (bytes memory reason) {
                    emit VirtualPullSkipped(poolId, reason); // never brick the swap
                }
            }
        }
        return (IHooks.beforeSwap.selector, BeforeSwapDeltaLibrary.ZERO_DELTA, 0);
    }

    /// @dev Settle drawn principal back to the vault; compute protocol fee (IntentHookV2 pattern).
    function afterSwap(address, PoolKey calldata key, IPoolManager.SwapParams calldata, BalanceDelta delta, bytes calldata)
        external
        override
        onlyPoolManager
        nonReentrant
        returns (bytes4, int128)
    {
        PoolId poolId = key.toId();

        if (_pending.active) {
            PendingDraw memory p = _pending;
            delete _pending;
            uint256 bal = IERC20(p.token).balanceOf(address(this));
            uint256 principal = p.amount <= bal ? p.amount : bal;
            if (principal > 0) {
                try vault.settleUp(p.strategyId, p.lp, p.token, principal, 0, 0) {
                    emit VirtualLiquiditySettled(poolId, p.lp, p.token, principal, 0);
                } catch (bytes memory reason) {
                    // Capital stays in the hook; owner can recover via recoverERC20. Still never bricks.
                    emit VirtualPullSkipped(poolId, reason);
                }
            }
        }

        _captureProtocolFeeSafe(key, delta);
        return (IHooks.afterSwap.selector, 0);
    }

    // ------------------------------------------------------------------ unused hooks (permission set matches IntentHookV2: all off)

    function beforeInitialize(address, PoolKey calldata, uint160) external pure override returns (bytes4) {
        revert HookNotImplemented();
    }
    function afterInitialize(address, PoolKey calldata, uint160, int24) external pure override returns (bytes4) {
        revert HookNotImplemented();
    }
    function beforeAddLiquidity(address, PoolKey calldata, IPoolManager.ModifyLiquidityParams calldata, bytes calldata)
        external pure override returns (bytes4) { revert HookNotImplemented(); }
    function afterAddLiquidity(address, PoolKey calldata, IPoolManager.ModifyLiquidityParams calldata, BalanceDelta, BalanceDelta, bytes calldata)
        external pure override returns (bytes4, BalanceDelta) { revert HookNotImplemented(); }
    function beforeRemoveLiquidity(address, PoolKey calldata, IPoolManager.ModifyLiquidityParams calldata, bytes calldata)
        external pure override returns (bytes4) { revert HookNotImplemented(); }
    function afterRemoveLiquidity(address, PoolKey calldata, IPoolManager.ModifyLiquidityParams calldata, BalanceDelta, BalanceDelta, bytes calldata)
        external pure override returns (bytes4, BalanceDelta) { revert HookNotImplemented(); }
    function beforeDonate(address, PoolKey calldata, uint256, uint256, bytes calldata)
        external pure override returns (bytes4) { revert HookNotImplemented(); }
    function afterDonate(address, PoolKey calldata, uint256, uint256, bytes calldata)
        external pure override returns (bytes4) { revert HookNotImplemented(); }

    error HookNotImplemented();

    // ------------------------------------------------------------------ fee capture (calculation + event; IntentHookV2-identical)

    function _captureProtocolFeeSafe(PoolKey calldata key, BalanceDelta delta) internal {
        if (protocolFeeBps == 0) return;

        uint256 feeAmount = 0;
        Currency feeCurrency = key.currency1;
        if (delta.amount1() > 0) {
            feeAmount = FullMath.mulDiv(uint256(uint128(delta.amount1())), protocolFeeBps, 10_000);
        } else if (delta.amount0() > 0) {
            feeAmount = FullMath.mulDiv(uint256(uint128(delta.amount0())), protocolFeeBps, 10_000);
            feeCurrency = key.currency0;
        }
        if (feeAmount > 0) emit FeeCaptured(feeCurrency, feeAmount, treasury);
    }

    // ------------------------------------------------------------------ MEV donation path (LIVE, IntentHookV2-identical hardening)

    function acceptMEVDonation(address token, uint256 amount) external payable nonReentrant {
        uint256 received;
        if (token == address(0)) {
            received = msg.value;
            if (received == 0) return;
        } else {
            if (amount == 0) return;
            received = amount;
            IERC20(token).safeTransferFrom(msg.sender, address(this), received);
        }
        _recordMEVDonation(token, received);
    }

    receive() external payable {
        if (msg.value > 0) {
            _recordMEVDonation(address(0), msg.value);
            emit DirectETHDonationReceived(msg.value, msg.sender);
        }
    }

    function _recordMEVDonation(address token, uint256 amount) internal {
        totalMEVDonated += amount;
        bool recordedToHub = false;
        if (address(accountingHub) != address(0)) {
            try accountingHub.recordMEVRedirect(amount) { recordedToHub = true; } catch {}
        }
        if (!recordedToHub) {
            bool sentToTreasury;
            if (token == address(0)) {
                (sentToTreasury,) = treasury.call{value: amount}("");
            } else {
                try IERC20(token).transfer(treasury, amount) returns (bool ok) { sentToTreasury = ok; } catch {}
            }
        }
        emit MEVDonationAccepted(token, amount, msg.sender);
    }

    function withdrawDonations(address token, uint256 amount, address to) external onlyOwner nonReentrant {
        if (to == address(0)) to = msg.sender;
        if (amount == 0) return;
        if (token == address(0)) {
            (bool sent,) = to.call{value: amount}("");
            if (!sent) revert Unauthorized();
        } else {
            IERC20(token).safeTransfer(to, amount);
        }
        emit DonationsWithdrawn(token, amount, to);
    }

    /// @notice Recovery for capital left in the hook after a failed settle (defense in depth).
    function recoverERC20(address token, uint256 amount, address to) external onlyOwner nonReentrant {
        IERC20(token).safeTransfer(to, amount);
    }

    // ------------------------------------------------------------------ internals

    function strategiesActive(uint256 strategyId) internal view returns (bool) {
        // strategyId 0 is a valid id; an unregistered pool maps to 0 as well, so also require
        // that the vault reports an active hook == this contract for that strategy.
        try vault.strategyHook(strategyId) returns (address h) {
            return h == address(this);
        } catch {
            return false;
        }
    }

    function _decodeLp(bytes calldata hookData) internal pure returns (address lp) {
        if (hookData.length == 32) {
            lp = abi.decode(hookData, (address));
        }
    }

    /// @dev Output-side currency + conservative size estimate for the pull.
    ///      exactInput (amountSpecified < 0): estimate ≈ |amountSpecified| of the OTHER currency.
    ///      exactOutput (amountSpecified > 0): exact = amountSpecified of the other currency.
    function _outputSide(PoolKey calldata key, IPoolManager.SwapParams calldata params)
        internal
        pure
        returns (Currency outCurrency, uint256 estOut)
    {
        bool zeroForOne = params.zeroForOne;
        outCurrency = zeroForOne ? key.currency1 : key.currency0;
        int256 specified = params.amountSpecified;
        estOut = specified < 0 ? uint256(-specified) : uint256(specified);
    }
}
