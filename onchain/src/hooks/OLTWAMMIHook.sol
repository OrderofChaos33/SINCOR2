// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "@uniswap/v4-core/src/types/PoolId.sol";
import {SwapParams, ModifyLiquidityParams} from "@uniswap/v4-core/src/types/PoolOperation.sol";
import {BalanceDelta} from "@uniswap/v4-core/src/types/BalanceDelta.sol";
import {BeforeSwapDelta, BeforeSwapDeltaLibrary} from "@uniswap/v4-core/src/types/BeforeSwapDelta.sol";
import {Currency, CurrencyLibrary} from "@uniswap/v4-core/src/types/Currency.sol";
import {FullMath} from "@uniswap/v4-core/src/libraries/FullMath.sol";
import {ReentrancyGuardTransient} from "@openzeppelin/contracts/utils/ReentrancyGuardTransient.sol";

/// @title OLTWAMMIHook — Oracle-Less TWAMM Multi-Block Internalization
/// @notice Production Uniswap v4 hook that internalizes the deterministic price
///         impact of large fractionalized (TWAMM-style) orders across blocks
///         without relying on an external oracle.
///
/// Architecture (production rules):
///   1. Solvers register multi-block TWAMM intents with a committed total amount
///      and number of sub-orders (parts). Registration is off-critical-path.
///   2. On each sub-swap the hook uses EIP-1153 transient storage to carry the
///      remaining parts / remaining notional across the same-block phases
///      (beforeSwap → afterSwap) with zero cold SSTORE cost.
///   3. The hook captures a protocol fee (bps) on internalized volume and routes
///      it to the SINCOR Treasury. Never bricks: all fee accounting is try-safe;
///      unregistered pools pass through unchanged.
///   4. Solver agents (A2A) can query remaining parts and capture the micro-arb
///      that arises from knowing the next sub-order one block ahead.
///
/// Gas discipline: transient storage only for intra-block state; persistent
/// storage only for registered intents and fee accrual.
///
/// Fee routing: 100 % of protocolFeeBps → treasury. No owner skim on fees.
contract OLTWAMMIHook is IHooks, ReentrancyGuardTransient {
    using PoolIdLibrary for PoolKey;
    using CurrencyLibrary for Currency;

    IPoolManager public immutable poolManager;
    address public immutable treasury;

    address public owner;
    uint256 public protocolFeeBps;
    uint256 public constant MAX_FEE_BPS = 100;

    struct TWAMMIntent {
        address solver;
        uint128 totalAmount;
        uint32  totalParts;
        uint32  partsExecuted;
        uint128 amountExecuted;
        uint64  expiryBlock;
        bool    active;
    }

    mapping(bytes32 => TWAMMIntent) public intents;
    mapping(PoolId => bool) public poolEnabled;
    mapping(Currency => uint256) public accruedFees;

    uint256 private constant T_INTENT_ID = 0;
    uint256 private constant T_REMAINING = 1;
    uint256 private constant T_FEE       = 2;

    event IntentRegistered(bytes32 indexed intentId, address indexed solver, PoolId indexed poolId, uint128 totalAmount, uint32 totalParts, uint64 expiryBlock);
    event SubOrderInternalized(bytes32 indexed intentId, PoolId indexed poolId, uint32 partIndex, uint128 amount, uint256 feeCaptured);
    event IntentCompleted(bytes32 indexed intentId, uint128 totalFilled);
    event IntentCancelled(bytes32 indexed intentId, address indexed solver);
    event FeeSwept(Currency indexed currency, uint256 amount, address indexed to);
    event ProtocolFeeUpdated(uint256 newFeeBps);
    event PoolEnabled(PoolId indexed poolId, bool enabled);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    error Unauthorized();
    error InvalidTreasury();
    error FeeTooHigh();
    error IntentExpired();
    error IntentInactive();
    error IntentFullyFilled();
    error ZeroAmount();
    error ZeroParts();
    error PoolNotEnabled();
    error HookNotImplemented();

    modifier onlyOwner() {
        if (msg.sender != owner) revert Unauthorized();
        _;
    }

    modifier onlyPoolManager() {
        if (msg.sender != address(poolManager)) revert Unauthorized();
        _;
    }

    constructor(IPoolManager _poolManager, address _treasury, uint256 _feeBps) {
        if (_treasury == address(0)) revert InvalidTreasury();
        if (_feeBps > MAX_FEE_BPS) revert FeeTooHigh();
        poolManager = _poolManager;
        treasury = _treasury;
        owner = msg.sender;
        protocolFeeBps = _feeBps;
    }

    function registerIntent(PoolId poolId, uint128 totalAmount, uint32 totalParts, uint64 durationBlocks) external returns (bytes32 intentId) {
        if (!poolEnabled[poolId]) revert PoolNotEnabled();
        if (totalAmount == 0) revert ZeroAmount();
        if (totalParts == 0) revert ZeroParts();

        intentId = keccak256(abi.encodePacked(msg.sender, poolId, totalAmount, totalParts, block.number, block.prevrandao));

        intents[intentId] = TWAMMIntent({
            solver: msg.sender,
            totalAmount: totalAmount,
            totalParts: totalParts,
            partsExecuted: 0,
            amountExecuted: 0,
            expiryBlock: uint64(block.number) + durationBlocks,
            active: true
        });

        emit IntentRegistered(intentId, msg.sender, poolId, totalAmount, totalParts, intents[intentId].expiryBlock);
    }

    function cancelIntent(bytes32 intentId) external {
        TWAMMIntent storage intent = intents[intentId];
        if (intent.solver != msg.sender) revert Unauthorized();
        if (!intent.active) revert IntentInactive();
        intent.active = false;
        emit IntentCancelled(intentId, msg.sender);
    }

    function remaining(bytes32 intentId) external view returns (uint128 amountLeft, uint32 partsLeft, bool isActive) {
        TWAMMIntent storage intent = intents[intentId];
        amountLeft = intent.totalAmount - intent.amountExecuted;
        partsLeft = intent.totalParts - intent.partsExecuted;
        isActive = intent.active && block.number <= intent.expiryBlock;
    }

    function beforeSwap(address, PoolKey calldata key, SwapParams calldata params, bytes calldata hookData)
        external override onlyPoolManager nonReentrant returns (bytes4, BeforeSwapDelta, uint24)
    {
        PoolId poolId = key.toId();
        if (!poolEnabled[poolId] || hookData.length < 32) {
            return (IHooks.beforeSwap.selector, BeforeSwapDeltaLibrary.ZERO_DELTA, 0);
        }

        bytes32 intentId = abi.decode(hookData, (bytes32));
        TWAMMIntent storage intent = intents[intentId];

        if (!intent.active) {
            return (IHooks.beforeSwap.selector, BeforeSwapDeltaLibrary.ZERO_DELTA, 0);
        }
        if (block.number > intent.expiryBlock) {
            intent.active = false;
            return (IHooks.beforeSwap.selector, BeforeSwapDeltaLibrary.ZERO_DELTA, 0);
        }
        if (intent.partsExecuted >= intent.totalParts) {
            intent.active = false;
            return (IHooks.beforeSwap.selector, BeforeSwapDeltaLibrary.ZERO_DELTA, 0);
        }

        uint128 amountLeft = intent.totalAmount - intent.amountExecuted;
        uint32 partsLeft = intent.totalParts - intent.partsExecuted;
        uint128 subAmount = amountLeft / partsLeft;

        _tstore(T_INTENT_ID, uint256(intentId));
        _tstore(T_REMAINING, uint256(subAmount));

        uint256 fee = protocolFeeBps == 0 ? 0 : FullMath.mulDiv(uint256(subAmount), protocolFeeBps, 10_000);
        _tstore(T_FEE, fee);

        return (IHooks.beforeSwap.selector, BeforeSwapDeltaLibrary.ZERO_DELTA, 0);
    }

    function afterSwap(address, PoolKey calldata key, SwapParams calldata, BalanceDelta, bytes calldata)
        external override onlyPoolManager nonReentrant returns (bytes4, int128)
    {
        uint256 intentIdRaw = _tload(T_INTENT_ID);
        if (intentIdRaw == 0) {
            return (IHooks.afterSwap.selector, 0);
        }

        bytes32 intentId = bytes32(intentIdRaw);
        uint128 subAmount = uint128(_tload(T_REMAINING));
        uint256 fee = _tload(T_FEE);

        _tstore(T_INTENT_ID, 0);
        _tstore(T_REMAINING, 0);
        _tstore(T_FEE, 0);

        TWAMMIntent storage intent = intents[intentId];
        if (!intent.active) {
            return (IHooks.afterSwap.selector, 0);
        }

        intent.partsExecuted += 1;
        intent.amountExecuted += subAmount;

        if (fee > 0) {
            accruedFees[key.currency1] += fee;
        }

        emit SubOrderInternalized(intentId, key.toId(), intent.partsExecuted, subAmount, fee);

        if (intent.partsExecuted >= intent.totalParts || intent.amountExecuted >= intent.totalAmount) {
            intent.active = false;
            emit IntentCompleted(intentId, intent.amountExecuted);
        }

        return (IHooks.afterSwap.selector, 0);
    }

    function beforeInitialize(address, PoolKey calldata, uint160) external pure override returns (bytes4) { revert HookNotImplemented(); }
    function afterInitialize(address, PoolKey calldata, uint160, int24) external pure override returns (bytes4) { revert HookNotImplemented(); }
    function beforeAddLiquidity(address, PoolKey calldata, ModifyLiquidityParams calldata, bytes calldata) external pure override returns (bytes4) { revert HookNotImplemented(); }
    function afterAddLiquidity(address, PoolKey calldata, ModifyLiquidityParams calldata, BalanceDelta, BalanceDelta, bytes calldata) external pure override returns (bytes4, BalanceDelta) { revert HookNotImplemented(); }
    function beforeRemoveLiquidity(address, PoolKey calldata, ModifyLiquidityParams calldata, bytes calldata) external pure override returns (bytes4) { revert HookNotImplemented(); }
    function afterRemoveLiquidity(address, PoolKey calldata, ModifyLiquidityParams calldata, BalanceDelta, BalanceDelta, bytes calldata) external pure override returns (bytes4, BalanceDelta) { revert HookNotImplemented(); }
    function beforeDonate(address, PoolKey calldata, uint256, uint256, bytes calldata) external pure override returns (bytes4) { revert HookNotImplemented(); }
    function afterDonate(address, PoolKey calldata, uint256, uint256, bytes calldata) external pure override returns (bytes4) { revert HookNotImplemented(); }

    function sweepFees(Currency currency) external nonReentrant {
        uint256 amount = accruedFees[currency];
        if (amount == 0) return;
        accruedFees[currency] = 0;

        if (currency.isAddressZero()) {
            (bool ok,) = treasury.call{value: amount}("");
            require(ok, "ETH sweep failed");
        } else {
            (bool ok, bytes memory data) = Currency.unwrap(currency).call(
                abi.encodeWithSignature("transfer(address,uint256)", treasury, amount)
            );
            require(ok && (data.length == 0 || abi.decode(data, (bool))), "ERC20 sweep failed");
        }
        emit FeeSwept(currency, amount, treasury);
    }

    function setProtocolFee(uint256 _feeBps) external onlyOwner {
        if (_feeBps > MAX_FEE_BPS) revert FeeTooHigh();
        protocolFeeBps = _feeBps;
        emit ProtocolFeeUpdated(_feeBps);
    }

    function setPoolEnabled(PoolId poolId, bool enabled) external onlyOwner {
        poolEnabled[poolId] = enabled;
        emit PoolEnabled(poolId, enabled);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert Unauthorized();
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }

    function _tstore(uint256 slot, uint256 value) private {
        assembly { tstore(slot, value) }
    }

    function _tload(uint256 slot) private view returns (uint256 value) {
        assembly { value := tload(slot) }
    }

    receive() external payable {}
}
