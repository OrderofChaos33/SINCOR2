// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {IUnlockCallback} from "v4-core/src/interfaces/callback/IUnlockCallback.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency, CurrencyLibrary} from "v4-core/src/types/Currency.sol";
import {BalanceDelta} from "v4-core/src/types/BalanceDelta.sol";
import {PoolId, PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {ModifyLiquidityParams} from "v4-core/src/types/PoolOperation.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuardTransient} from "@openzeppelin/contracts/utils/ReentrancyGuardTransient.sol";

/// @notice Strict interface searchers must implement for typed, contained execution
interface IMoebiusSearcher {
    function moebiusExecute(bytes calldata payload) external;
}

interface IPhantomCreditToken {
    function mintEphemeral(address to, uint256 amount) external;
    function burnEphemeral(address from, uint256 amount) external;
}

/// @title MoebiusMEVHook - "central bank" MEV capture for Uniswap v4
/// @notice v2 rewrite. Replaces the v1 honor-system tax (levied on the searcher's
///         SELF-DECLARED profit - gameable to dust) with a sealed-bid escrow:
///
///         1. Searcher escrows a hard bid in the pool's real asset UP FRONT
///            (transferFrom / msg.value). No bid, no credit.
///         2. Hook flash-mints pMEV working capital to the searcher.
///         3. Searcher runs its loop; must return (burn) 100% of the pMEV.
///         4. The escrowed bid is split deterministically:
///            80% donated to in-range LPs, 20% to protocol treasury.
///
///         Enforcement no longer sniffs contract balances (griefable by dusting)
///         and no longer trusts declared profit. The bid IS the tax. Competition
///         between searchers for the same flow is what pushes bids toward the
///         true arb value - a real auction.
///
///         minBidBps is the policy rate: a floor on bids proportional to the
///         flash amount, so LP compensation scales with capital consumed.
///
///         The hook carries ZERO hook-permission flags: it intercepts no swaps
///         and adds no gas to swappers. Its address must carry zero lower-14
///         hook-flag bits (mined via CREATE2 - see DeployMoebius.s.sol), and
///         pMEV pools must use the dynamic-fee flag, with the swap fee set
///         through setPoolSwapFee (policy lever #2).
contract MoebiusMEVHook is IUnlockCallback, ReentrancyGuardTransient {
    using CurrencyLibrary for Currency;
    using PoolIdLibrary for PoolKey;
    using SafeERC20 for IERC20;

    IPoolManager public immutable poolManager;
    IPhantomCreditToken public immutable pToken;

    address public owner;
    address public protocolTreasury;

    /// @notice Policy rate floor: bid >= flashAmount * minBidBps / 10_000
    uint256 public minBidBps;

    uint256 public constant TREASURY_SHARE_BIPS = 2000; // 20% of escrowed bid
    uint256 public constant MAX_MIN_BID_BPS = 1000; // floor itself capped at 10%

    bytes32 internal constant ACTION_MEV = keccak256("MOEBIUS_MEV");
    bytes32 internal constant ACTION_SEED = keccak256("MOEBIUS_SEED");

    event MEVLoopExecuted(
        address indexed searcher, uint256 flashAmount, uint256 bid, uint256 lpShare, uint256 protocolShare
    );
    event LiquiditySeeded(PoolId indexed poolId, uint256 pMevSeeded, uint256 realSeeded, uint256 liquidity);
    event PolicyRateUpdated(uint256 newMinBidBps);
    event PoolSwapFeeUpdated(PoolId indexed poolId, uint24 newFee);
    event TreasuryUpdated(address indexed newTreasury);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    error Unauthorized();
    error UnauthorizedCallback();
    error InvalidPoolKey();
    error BidBelowFloor(uint256 bid, uint256 floor);
    error BadEscrow();
    error ZeroAmount();
    error FeeOnTransferNotSupported();
    error RateTooHigh();

    modifier onlyOwner() {
        if (msg.sender != owner) revert Unauthorized();
        _;
    }

    constructor(IPoolManager _poolManager, address _pToken, address _treasury, uint256 _minBidBps) {
        require(address(_poolManager) != address(0) && _pToken != address(0) && _treasury != address(0), "Zero address");
        if (_minBidBps > MAX_MIN_BID_BPS) revert RateTooHigh();
        poolManager = _poolManager;
        pToken = IPhantomCreditToken(_pToken);
        protocolTreasury = _treasury;
        minBidBps = _minBidBps;
        owner = msg.sender;
    }

    // ==================== SEARCHER ENTRY ====================

    /// @notice Execute a MEV loop with flash-minted pMEV working capital.
    /// @param key         PoolKey of the pMEV/realAsset pool (hooks must be THIS contract)
    /// @param flashAmount Amount of pMEV to flash-mint (must be fully returned)
    /// @param bid         Escrowed tax bid, paid in the pool's real asset
    /// @param payload     Opaque searcher payload forwarded to moebiusExecute
    function executeMEV(PoolKey calldata key, uint256 flashAmount, uint256 bid, bytes calldata payload)
        external
        payable
        nonReentrant
    {
        if (flashAmount == 0) revert ZeroAmount();
        Currency realAsset = _validateKeyAndGetRealAsset(key);

        uint256 floor = (flashAmount * minBidBps) / 10_000;
        if (bid < floor) revert BidBelowFloor(bid, floor);

        _escrow(realAsset, msg.sender, bid);

        // unlock's return is the callback's return data ("" here); nothing to act on
        // slither-disable-next-line unused-return
        poolManager.unlock(abi.encode(ACTION_MEV, msg.sender, key, flashAmount, bid, payload));

        // Loop succeeded (else the whole tx reverted, escrow included).
        uint256 protocolShare = (bid * TREASURY_SHARE_BIPS) / 10_000;
        uint256 lpShare = bid - protocolShare;
        if (protocolShare > 0) _payout(realAsset, protocolTreasury, protocolShare);

        emit MEVLoopExecuted(msg.sender, flashAmount, bid, lpShare, protocolShare);
    }

    // ==================== CENTRAL BANK DESK ====================

    /// @notice Owner seeds the pMEV/realAsset pool: hook flash-mints pMEV inventory
    ///         and pairs it with the owner's real asset into an LP position owned
    ///         by the hook. This is the balance sheet that gives pMEV a price.
    /// @param liquidity  v4 liquidity delta (compute off-chain via LiquidityAmounts)
    /// @param maxReal    Max real asset to escrow (leftover is refunded)
    /// @param amountPmev pMEV to mint for seeding (unneeded remainder is burned)
    function seedLiquidity(
        PoolKey calldata key,
        int24 tickLower,
        int24 tickUpper,
        uint256 liquidity,
        uint256 maxReal,
        uint256 amountPmev
    ) external payable onlyOwner nonReentrant {
        if (liquidity == 0 || amountPmev == 0) revert ZeroAmount();
        Currency realAsset = _validateKeyAndGetRealAsset(key);

        _escrow(realAsset, msg.sender, maxReal);

        uint256 realBefore = _selfBalance(realAsset);
        // slither-disable-next-line unused-return
        poolManager.unlock(abi.encode(ACTION_SEED, key, tickLower, tickUpper, liquidity, amountPmev));
        uint256 realSpent = realBefore - _selfBalance(realAsset);

        // Refund unspent escrow
        uint256 leftover = _selfBalance(realAsset);
        if (leftover > 0) _payout(realAsset, msg.sender, leftover);

        emit LiquiditySeeded(key.toId(), amountPmev, realSpent, liquidity);
    }

    // ==================== V4 CALLBACK ====================

    function unlockCallback(bytes calldata data) external override returns (bytes memory) {
        if (msg.sender != address(poolManager)) revert UnauthorizedCallback();

        bytes32 action = abi.decode(data, (bytes32));

        if (action == ACTION_MEV) {
            (, address searcher, PoolKey memory key, uint256 flashAmount, uint256 bid, bytes memory payload) =
                abi.decode(data, (bytes32, address, PoolKey, uint256, uint256, bytes));
            _runMevLoop(searcher, key, flashAmount, bid, payload);
        } else if (action == ACTION_SEED) {
            (, PoolKey memory key, int24 tickLower, int24 tickUpper, uint256 liquidity, uint256 amountPmev) =
                abi.decode(data, (bytes32, PoolKey, int24, int24, uint256, uint256));
            _runSeed(key, tickLower, tickUpper, liquidity, amountPmev);
        } else {
            revert UnauthorizedCallback();
        }

        return "";
    }

    function _runMevLoop(address searcher, PoolKey memory key, uint256 flashAmount, uint256 bid, bytes memory payload)
        internal
    {
        // Defense in depth: never act on a malformed nested-unlock payload
        Currency realAsset = _validateKeyAndGetRealAsset(key);
        bool isZeroPToken = Currency.unwrap(key.currency0) == address(pToken);

        // 1. Flash-issue working capital
        pToken.mintEphemeral(searcher, flashAmount);

        // 2. Typed searcher execution (searcher settles its own deltas inside this frame)
        IMoebiusSearcher(searcher).moebiusExecute(payload);

        // 3. Full repayment enforced by the token itself: reverts on shortfall,
        //    which reverts the entire transaction INCLUDING the escrow.
        pToken.burnEphemeral(searcher, flashAmount);

        // 4. LP share of the escrowed bid goes to in-range LPs, settled from escrow
        uint256 protocolShare = (bid * TREASURY_SHARE_BIPS) / 10_000;
        uint256 lpShare = bid - protocolShare;
        if (lpShare > 0) {
            (uint256 amount0, uint256 amount1) = isZeroPToken ? (uint256(0), lpShare) : (lpShare, uint256(0));
            // donate's returned delta is settled on the next line; nothing to inspect
            // slither-disable-next-line unused-return
            poolManager.donate(key, amount0, amount1, "");
            _settleFromSelf(realAsset, lpShare);
        }
    }

    function _runSeed(PoolKey memory key, int24 tickLower, int24 tickUpper, uint256 liquidity, uint256 amountPmev)
        internal
    {
        pToken.mintEphemeral(address(this), amountPmev);

        // second return (hook delta) is always zero: this hook has no liquidity deltas
        // slither-disable-next-line unused-return
        (BalanceDelta delta,) = poolManager.modifyLiquidity(
            key,
            ModifyLiquidityParams({
                tickLower: tickLower,
                tickUpper: tickUpper,
                liquidityDelta: int256(liquidity),
                salt: bytes32(0)
            }),
            ""
        );

        // Settle what the position actually consumed
        _settleDeltaCurrency(key.currency0, delta.amount0());
        _settleDeltaCurrency(key.currency1, delta.amount1());

        // Burn any pMEV the position didn't absorb
        uint256 pMevLeft = IERC20(address(pToken)).balanceOf(address(this));
        if (pMevLeft > 0) pToken.burnEphemeral(address(this), pMevLeft);
    }

    // ==================== INTERNALS ====================

    function _validateKeyAndGetRealAsset(PoolKey memory key) internal view returns (Currency realAsset) {
        if (address(key.hooks) != address(this)) revert InvalidPoolKey();
        address c0 = Currency.unwrap(key.currency0);
        address c1 = Currency.unwrap(key.currency1);
        if (c0 == c1) revert InvalidPoolKey();
        if (c0 == address(pToken)) return key.currency1;
        if (c1 == address(pToken)) return key.currency0;
        revert InvalidPoolKey();
    }

    /// @dev Pull the bid/seed escrow into this contract. Native = msg.value, else transferFrom.
    function _escrow(Currency realAsset, address from, uint256 amount) internal {
        if (amount == 0) {
            if (msg.value != 0) revert BadEscrow();
            return;
        }
        if (realAsset.isAddressZero()) {
            if (msg.value != amount) revert BadEscrow();
        } else {
            if (msg.value != 0) revert BadEscrow();
            uint256 before = IERC20(Currency.unwrap(realAsset)).balanceOf(address(this));
            IERC20(Currency.unwrap(realAsset)).safeTransferFrom(from, address(this), amount);
            // Reject fee-on-transfer assets outright: escrow math must be exact
            if (IERC20(Currency.unwrap(realAsset)).balanceOf(address(this)) - before != amount) {
                revert FeeOnTransferNotSupported();
            }
        }
    }

    /// @dev Pay out tokens this contract holds (outside the PoolManager lock).
    ///      Reachable destinations are exactly: (1) protocolTreasury - a governed
    ///      storage sink set only by the owner; (2) msg.sender of the onlyOwner,
    ///      nonReentrant seedLiquidity entrypoint, refunding the owner's OWN
    ///      unspent escrow. No third-party-controlled destination exists.
    function _payout(Currency realAsset, address to, uint256 amount) internal {
        if (realAsset.isAddressZero()) {
            // adjudicated false positive: destinations restricted as documented above
            // slither-disable-next-line arbitrary-send-eth
            (bool ok,) = to.call{value: amount}("");
            require(ok, "Native payout failed");
        } else {
            IERC20(Currency.unwrap(realAsset)).safeTransfer(to, amount);
        }
    }

    /// @dev Settle a PoolManager debt using tokens this contract holds (inside the lock).
    function _settleFromSelf(Currency currency, uint256 amount) internal {
        if (currency.isAddressZero()) {
            // slither-disable-next-line unused-return
            poolManager.settle{value: amount}();
        } else {
            poolManager.sync(currency);
            IERC20(Currency.unwrap(currency)).safeTransfer(address(poolManager), amount);
            // slither-disable-next-line unused-return
            poolManager.settle();
        }
    }

    function _settleDeltaCurrency(Currency currency, int128 deltaAmount) internal {
        if (deltaAmount >= 0) return; // positive delta = PoolManager owes us; leave as claim
        _settleFromSelf(currency, uint256(uint128(-deltaAmount)));
    }

    function _selfBalance(Currency currency) internal view returns (uint256) {
        return currency.isAddressZero() ? address(this).balance : IERC20(Currency.unwrap(currency)).balanceOf(address(this));
    }

    // ==================== ADMIN (monetary policy) ====================

    function setMinBidBps(uint256 newMinBidBps) external onlyOwner {
        if (newMinBidBps > MAX_MIN_BID_BPS) revert RateTooHigh();
        minBidBps = newMinBidBps;
        emit PolicyRateUpdated(newMinBidBps);
    }

    /// @notice Policy lever #2: set the swap fee of a hook-owned dynamic-fee pool.
    ///         pMEV pools must be initialized with the dynamic fee flag (0x800000)
    ///         because this hook carries zero permission flags.
    function setPoolSwapFee(PoolKey calldata key, uint24 newFee) external onlyOwner {
        _validateKeyAndGetRealAsset(key);
        poolManager.updateDynamicLPFee(key, newFee);
        emit PoolSwapFeeUpdated(key.toId(), newFee);
    }

    function setTreasury(address newTreasury) external onlyOwner {
        require(newTreasury != address(0), "Zero address");
        protocolTreasury = newTreasury;
        emit TreasuryUpdated(newTreasury);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "Zero address");
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }

    /// @notice Recover stray tokens force-sent to the hook. Cannot touch an active
    ///         unlock frame (this is only callable outside one by definition).
    ///         Proceeds go ONLY to the governed treasury sink, never to an
    ///         arbitrary destination - an owner phishing/typo cannot redirect funds.
    function rescue(Currency currency, uint256 amount) external onlyOwner {
        _payout(currency, protocolTreasury, amount);
    }
}
