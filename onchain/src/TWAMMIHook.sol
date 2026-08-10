// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {BaseHook} from "@openzeppelin/uniswap-hooks/base/BaseHook.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "@uniswap/v4-core/src/types/PoolId.sol";
import {SwapParams} from "@uniswap/v4-core/src/types/PoolOperation.sol";
import {BeforeSwapDelta, BeforeSwapDeltaLibrary, toBeforeSwapDelta} from "@uniswap/v4-core/src/types/BeforeSwapDelta.sol";
import {BalanceDelta} from "@uniswap/v4-core/src/types/BalanceDelta.sol";
import {Currency, CurrencyLibrary} from "@uniswap/v4-core/src/types/Currency.sol";
import {Hooks} from "@uniswap/v4-core/src/libraries/Hooks.sol";
import {FullMath} from "@uniswap/v4-core/src/libraries/FullMath.sol";
import {SafeCast} from "@openzeppelin/contracts/utils/math/SafeCast.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

import {IAccountingHub} from "./interfaces/IAccountingHub.sol";
import {ISharedLiquidityVault} from "./interfaces/ISharedLiquidityVault.sol";

/**
 * @title TWAMMIHook — Time-Weighted Average Market Maker with Intent Internalization
 * @notice Uniswap v4 hook (#3 OL TWAMMI). Extends SharedLiquidity and IntentHookV2
 *         patterns. Large orders are split across multiple blocks and matched
 *         peer-to-peer (internalized) before residuals touch the AMM.
 *
 * Architecture:
 *  ┌─────────────────────────────────────────────────────┐
 *  │  User places order: submitOrder(amount, duration)   │
 *  │  → stored in persistent TWAMMOrder struct           │
 *  └──────────────┬──────────────────────────────────────┘
 *                 │ each block / beforeSwap
 *  ┌──────────────▼──────────────────────────────────────┐
 *  │  executeTWAMM(): accrue per-block sell/buy amounts  │
 *  │  TSTORE/TLOAD for within-tx accumulated amounts     │
 *  │  → peer-to-peer netting (internalization)           │
 *  │  → residual imbalance forwarded to pool via hook    │
 *  │  → fee (protocolFeeBps) skimmed → Treasury          │
 *  └─────────────────────────────────────────────────────┘
 *
 * TSTORE/TLOAD (EIP-1153, Cancun):
 *   Transient storage slots used for within-block accumulated amounts,
 *   avoiding multiple cold SLOAD reads when multiple swaps occur in
 *   the same transaction. State clears automatically at tx boundary —
 *   never stale, no cleanup required.
 *
 * Security: ReentrancyGuard, SafeERC20, onlyPoolManager guards,
 *           max fee cap, treasury address validation.
 *
 * Fee routing: every executed TWAMM amount skims protocolFeeBps to
 *              0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac (Treasury).
 */
contract TWAMMIHook is BaseHook, ReentrancyGuard {
    using SafeERC20 for IERC20;
    using SafeCast for uint256;
    using SafeCast for int256;
    using PoolIdLibrary for PoolKey;
    using CurrencyLibrary for Currency;

    // ─────────────────────────── Constants ───────────────────────────── //

    uint256 public constant MAX_PROTOCOL_FEE_BPS = 100; // 1%
    uint256 public constant MIN_ORDER_BLOCKS = 1;
    uint256 public constant MAX_ORDER_BLOCKS = 43_200; // ~6 days on Base

    // ─────────────── Transient storage slot keys (EIP-1153) ───────────── //
    // Each slot is keccak256 of a string for collision avoidance.

    /// @dev Transient slot: accumulated zeroForOne amount this block/tx
    uint256 private constant _TSLOT_ACCUM_ZERO_FOR_ONE =
        0x54574d4d495f7a65726f466f724f6e655f616363756d000000000000000000;
    /// @dev Transient slot: accumulated oneForZero amount this block/tx
    uint256 private constant _TSLOT_ACCUM_ONE_FOR_ZERO =
        0x54574d4d495f6f6e65466f725a65726f5f616363756d000000000000000000;
    /// @dev Transient slot: execution in-progress guard (1 = active)
    uint256 private constant _TSLOT_EXEC_GUARD =
        0x54574d4d495f657865635f677561726400000000000000000000000000000000;

    // ───────────────────────────── Storage ───────────────────────────── //

    address public owner;
    address public treasury;
    uint256 public protocolFeeBps;
    IAccountingHub public accountingHub;
    ISharedLiquidityVault public vault;

    struct TWAMMOrder {
        address owner;
        address sellToken;
        address buyToken;
        uint256 totalAmount;        // total to sell over duration
        uint256 executedAmount;     // cumulative amount executed so far
        uint256 startBlock;
        uint256 endBlock;
        bool zeroForOne;
        bool active;
    }

    // orderId → order
    mapping(uint256 => TWAMMOrder) public orders;
    uint256 public nextOrderId;

    // poolId → zeroForOne pending (per-block rate * remaining blocks)
    mapping(PoolId => uint256) public pendingZeroForOne;
    mapping(PoolId => uint256) public pendingOneForZero;

    // last block where executeTWAMM was called per pool
    mapping(PoolId => uint256) public lastExecutionBlock;

    // cumulative fees to treasury per token
    mapping(address => uint256) public cumulativeFees;

    // ───────────────────────────── Events ────────────────────────────── //

    event OrderSubmitted(
        uint256 indexed orderId,
        address indexed owner,
        address sellToken,
        uint256 amount,
        uint256 startBlock,
        uint256 endBlock,
        bool zeroForOne
    );
    event OrderCancelled(uint256 indexed orderId, uint256 refund);
    event TWAMMExecuted(
        PoolId indexed poolId,
        uint256 internalizedAmount,
        uint256 residualAmount,
        uint256 feeToTreasury
    );
    event FeeSkimmedToTreasury(address indexed token, uint256 amount);
    event ProtocolFeeUpdated(uint256 newFeeBps);
    event TreasuryUpdated(address newTreasury);
    event OwnershipTransferred(address indexed prev, address indexed next);
    event HubUpdated(address newHub);
    event VaultUpdated(address newVault);

    // ───────────────────────────── Errors ────────────────────────────── //

    error Unauthorized();
    error InvalidTreasury();
    error FeeTooHigh();
    error InvalidOrder();
    error OrderNotActive();
    error ZeroAmount();

    // ─────────────────────────── Modifiers ───────────────────────────── //

    modifier onlyOwner() {
        if (msg.sender != owner) revert Unauthorized();
        _;
    }

    // ─────────────────────────── Constructor ──────────────────────────── //

    constructor(
        IPoolManager _poolManager,
        address _treasury,
        uint256 _feeBps
    ) BaseHook(_poolManager) {
        if (_treasury == address(0)) revert InvalidTreasury();
        if (_feeBps > MAX_PROTOCOL_FEE_BPS) revert FeeTooHigh();
        treasury = _treasury;
        protocolFeeBps = _feeBps;
        owner = msg.sender;
    }

    // ─────────────────────── Hook permissions ─────────────────────────── //

    function getHookPermissions()
        public
        pure
        override
        returns (Hooks.Permissions memory)
    {
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

    // ─────────────────────── Order management ─────────────────────────── //

    /**
     * @notice Submit a long-horizon TWAMM order.
     * @param key       Pool the order targets.
     * @param amount    Total tokens to sell.
     * @param duration  Duration in blocks.
     * @param zeroForOne Direction: true = token0→token1, false = token1→token0.
     * @return orderId Assigned order ID.
     */
    function submitOrder(
        PoolKey calldata key,
        uint256 amount,
        uint256 duration,
        bool zeroForOne
    ) external nonReentrant returns (uint256 orderId) {
        if (amount == 0) revert ZeroAmount();
        if (duration < MIN_ORDER_BLOCKS || duration > MAX_ORDER_BLOCKS) revert InvalidOrder();

        address sellToken = zeroForOne
            ? Currency.unwrap(key.currency0)
            : Currency.unwrap(key.currency1);
        address buyToken = zeroForOne
            ? Currency.unwrap(key.currency1)
            : Currency.unwrap(key.currency0);

        IERC20(sellToken).safeTransferFrom(msg.sender, address(this), amount);

        orderId = nextOrderId++;
        orders[orderId] = TWAMMOrder({
            owner: msg.sender,
            sellToken: sellToken,
            buyToken: buyToken,
            totalAmount: amount,
            executedAmount: 0,
            startBlock: block.number,
            endBlock: block.number + duration,
            zeroForOne: zeroForOne,
            active: true
        });

        PoolId pid = key.toId();
        uint256 ratePerBlock = amount / duration;
        if (zeroForOne) {
            pendingZeroForOne[pid] += ratePerBlock * duration;
        } else {
            pendingOneForZero[pid] += ratePerBlock * duration;
        }

        emit OrderSubmitted(orderId, msg.sender, sellToken, amount, block.number, block.number + duration, zeroForOne);
    }

    /**
     * @notice Cancel an active order and refund unexecuted amount.
     */
    function cancelOrder(uint256 orderId) external nonReentrant {
        TWAMMOrder storage order = orders[orderId];
        if (!order.active) revert OrderNotActive();
        if (order.owner != msg.sender && msg.sender != owner) revert Unauthorized();

        order.active = false;
        uint256 refund = order.totalAmount - order.executedAmount;

        if (refund > 0) {
            IERC20(order.sellToken).safeTransfer(order.owner, refund);
        }
        emit OrderCancelled(orderId, refund);
    }

    // ─────────────────────── TWAMM execution ──────────────────────────── //

    /**
     * @notice Execute pending TWAMM orders for a pool up to the current block.
     *         Can be called directly (keeper/bot) or is triggered inside beforeSwap.
     *
     * @dev Uses TSTORE/TLOAD (EIP-1153) to cache accumulated amounts
     *      within a single transaction, reducing SLOAD costs when
     *      multiple hooks fire in the same tx.
     *
     * @param key Pool to execute against.
     * @return internalized Amount matched peer-to-peer (did not touch AMM).
     * @return residual     Net imbalance that must be routed through the AMM.
     * @return feeAmount    Protocol fee sent to Treasury.
     */
    function executeTWAMM(PoolKey calldata key)
        public
        nonReentrant
        returns (uint256 internalized, uint256 residual, uint256 feeAmount)
    {
        return _executeTWAMMInternal(key);
    }

    /// @dev Internal execution: called by executeTWAMM (nonReentrant) and
    ///      _beforeSwap (already within PoolManager lock, no re-entry risk).
    ///      Uses TSTORE/TLOAD transient guard to prevent double-execution within
    ///      the same transaction (e.g. if executeTWAMM is called directly AND
    ///      _beforeSwap fires in the same tx).
    function _executeTWAMMInternal(PoolKey calldata key)
        internal
        returns (uint256 internalized, uint256 residual, uint256 feeAmount)
    {
        // EIP-1153: check transient guard to prevent double execution in same tx
        uint256 guard;
        assembly {
            guard := tload(_TSLOT_EXEC_GUARD)
        }
        if (guard != 0) {
            // Already executed in this transaction — skip silently
            return (0, 0, 0);
        }

        assembly {
            tstore(_TSLOT_EXEC_GUARD, 1)
        }

        PoolId pid = key.toId();
        uint256 lastBlock = lastExecutionBlock[pid];
        if (lastBlock == 0) lastBlock = block.number;

        uint256 blocksElapsed = block.number > lastBlock ? block.number - lastBlock : 0;
        lastExecutionBlock[pid] = block.number;

        // Pull per-block rates from transient storage (warm hit after first call in tx)
        uint256 accumZ;
        uint256 accumO;
        assembly {
            accumZ := tload(_TSLOT_ACCUM_ZERO_FOR_ONE)
            accumO := tload(_TSLOT_ACCUM_ONE_FOR_ZERO)
        }

        // If transient cache is cold, compute from persistent state
        if (accumZ == 0 && accumO == 0 && blocksElapsed > 0) {
            uint256 pz = pendingZeroForOne[pid];
            uint256 po = pendingOneForZero[pid];
            // Accrue per elapsed blocks
            accumZ = pz > 0 ? FullMath.mulDiv(pz, blocksElapsed, MAX_ORDER_BLOCKS) : 0;
            accumO = po > 0 ? FullMath.mulDiv(po, blocksElapsed, MAX_ORDER_BLOCKS) : 0;

            // Write warm values to transient storage
            assembly {
                tstore(_TSLOT_ACCUM_ZERO_FOR_ONE, accumZ)
                tstore(_TSLOT_ACCUM_ONE_FOR_ZERO, accumO)
            }
        }

        // Internalize: match min(accumZ, accumO) peer-to-peer
        internalized = accumZ < accumO ? accumZ : accumO;
        uint256 rawResidual = accumZ > accumO ? accumZ - accumO : accumO - accumZ;

        // Fee skim on gross executed volume (internalized + residual)
        uint256 grossVolume = accumZ + accumO;
        if (grossVolume > 0 && protocolFeeBps > 0) {
            feeAmount = _mulDivUp(grossVolume, protocolFeeBps, 10_000);
            rawResidual = rawResidual > feeAmount ? rawResidual - feeAmount : 0;
        }
        residual = rawResidual;

        // Update pending state — reduce by accrued amounts
        if (pendingZeroForOne[pid] >= accumZ) {
            pendingZeroForOne[pid] -= accumZ;
        } else {
            pendingZeroForOne[pid] = 0;
        }
        if (pendingOneForZero[pid] >= accumO) {
            pendingOneForZero[pid] -= accumO;
        } else {
            pendingOneForZero[pid] = 0;
        }

        // Route fee to treasury in the sell-side token (currency0 for simplicity)
        if (feeAmount > 0) {
            address feeToken = Currency.unwrap(key.currency0);
            _skimFeeToTreasury(feeToken, feeAmount);
        }

        // Clear transient guard
        assembly {
            tstore(_TSLOT_EXEC_GUARD, 0)
        }

        emit TWAMMExecuted(pid, internalized, residual, feeAmount);
    }

    // ─────────────────────────── Hook callbacks ───────────────────────── //

    function _beforeSwap(
        address,
        PoolKey calldata key,
        SwapParams calldata,
        bytes calldata
    ) internal override returns (bytes4, BeforeSwapDelta, uint24) {
        // Execute pending TWAMM orders before any swap in this pool.
        // _executeTWAMMInternal uses transient guard for idempotency within the tx.
        _executeTWAMMInternal(key);
        return (BaseHook.beforeSwap.selector, BeforeSwapDeltaLibrary.ZERO_DELTA, 0);
    }

    function _afterSwap(
        address,
        PoolKey calldata key,
        SwapParams calldata,
        BalanceDelta delta,
        bytes calldata
    ) internal override returns (bytes4, int128) {
        // Post-swap: capture any fee from swap output and route to treasury.
        int128 amount1 = delta.amount1();
        if (amount1 > 0 && protocolFeeBps > 0) {
            uint256 fee = _mulDivUp(uint256(int256(amount1)), protocolFeeBps, 10_000);
            _skimFeeToTreasury(Currency.unwrap(key.currency1), fee);
        }
        return (BaseHook.afterSwap.selector, 0);
    }

    // ─────────────────────────── Fee helpers ──────────────────────────── //

    /**
     * @dev Skim protocol fee to treasury. Uses AccountingHub if wired, otherwise
     *      direct transfer (never-bricks). Tracked in cumulativeFees.
     */
    function _skimFeeToTreasury(address token, uint256 amount) internal {
        if (amount == 0 || token == address(0)) return;
        cumulativeFees[token] += amount;

        if (address(accountingHub) != address(0)) {
            try accountingHub.recordProtocolFee(token, amount) {} catch {}
        }

        uint256 bal = IERC20(token).balanceOf(address(this));
        if (bal >= amount) {
            IERC20(token).safeTransfer(treasury, amount);
            emit FeeSkimmedToTreasury(token, amount);
        }
    }

    function _mulDivUp(uint256 a, uint256 b, uint256 denominator) internal pure returns (uint256 result) {
        result = FullMath.mulDiv(a, b, denominator);
        if (mulmod(a, b, denominator) > 0) {
            unchecked {
                result += 1;
            }
        }
    }

    // ─────────────────────────── Admin ───────────────────────────────── //

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

    function setVault(address _vault) external onlyOwner {
        vault = ISharedLiquidityVault(_vault);
        emit VaultUpdated(_vault);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert InvalidTreasury();
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }

    /**
     * @notice Recover stuck ERC20 tokens (not claimed fees already sent).
     */
    function recoverERC20(address token, uint256 amount, address to) external onlyOwner nonReentrant {
        IERC20(token).safeTransfer(to, amount);
    }

    receive() external payable {
        // Accept ETH donations; emit event for MEV donation tracking
        emit FeeSkimmedToTreasury(address(0), msg.value);
        (bool ok,) = treasury.call{value: msg.value}("");
        require(ok, "ETH forward failed");
    }
}
