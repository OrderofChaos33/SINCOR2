// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {Hooks} from "v4-core/src/libraries/Hooks.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {Currency, CurrencyLibrary} from "v4-core/src/types/Currency.sol";
import {BalanceDelta} from "v4-core/src/types/BalanceDelta.sol";
import {ModifyLiquidityParams, SwapParams} from "v4-core/src/types/PoolOperation.sol";
import {BeforeSwapDelta} from "v4-core/src/types/BeforeSwapDelta.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

/// @title AutoCapitalizeMonetizeHook (v2, standalone)
/// @notice Replaces the non-compiling v1 sketch. Captures a protocol fee on every
///         swap via the afterSwap return-delta, banks it as real tokens held by
///         this contract, and splits swept balances per configurable bps:
///
///           treasuryBps  -> protocol treasury (hard revenue)
///           reserveBps   -> retained on-contract as the buyback / LP-deepen
///                           reserve, accounted per currency for a keeper to act on
///
///         Fee base is the UNSPECIFIED side of the swap (output for exact-in,
///         input for exact-out), the same convention as Uniswap's reference
///         fee-taking hooks.
///
///         Permissions: afterSwap + afterSwapReturnDelta. Deploy address MUST
///         carry exactly those flag bits (mine via CREATE2, see test).
///         NOT a reserve-holding contract for user funds - fees only.
contract AutoCapitalizeMonetizeHook is IHooks {
    using PoolIdLibrary for PoolKey;
    using SafeERC20 for IERC20;

    IPoolManager public immutable poolManager;

    address public owner;
    address public treasury;

    uint256 public feeBps; // protocol fee on unspecified side, cap 10%
    uint256 public treasuryBps; // share of swept fees -> treasury; rest -> reserve
    uint256 public constant MAX_FEE_BPS = 1000;

    /// @notice Fees banked per currency (lifetime accounting; reserve = balance - swept)
    mapping(Currency => uint256) public totalCaptured;
    mapping(Currency => uint256) public totalSwept;

    event FeeCaptured(PoolId indexed poolId, Currency indexed currency, uint256 amount);
    event Swept(Currency indexed currency, uint256 toTreasury, uint256 toReserve);
    event FeeBpsUpdated(uint256 newFeeBps);
    event TreasuryBpsUpdated(uint256 newTreasuryBps);
    event TreasuryUpdated(address indexed newTreasury);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    error Unauthorized();
    error UnauthorizedCallback();
    error FeeTooHigh();

    modifier onlyOwner() {
        if (msg.sender != owner) revert Unauthorized();
        _;
    }

    modifier onlyPoolManager() {
        if (msg.sender != address(poolManager)) revert UnauthorizedCallback();
        _;
    }

    constructor(IPoolManager _poolManager, address _treasury, uint256 _feeBps, uint256 _treasuryBps) {
        require(address(_poolManager) != address(0) && _treasury != address(0), "Zero address");
        if (_feeBps > MAX_FEE_BPS || _treasuryBps > 10_000) revert FeeTooHigh();
        poolManager = _poolManager;
        treasury = _treasury;
        feeBps = _feeBps;
        treasuryBps = _treasuryBps;
        owner = msg.sender;
    }

    // ==================== IHooks surface (only afterSwap is live) ====================

    function getHookPermissions() public pure returns (Hooks.Permissions memory) {
        return Hooks.Permissions({
            beforeInitialize: false,
            afterInitialize: false,
            beforeAddLiquidity: false,
            afterAddLiquidity: false,
            beforeRemoveLiquidity: false,
            afterRemoveLiquidity: false,
            beforeSwap: false,
            afterSwap: true,
            beforeDonate: false,
            afterDonate: false,
            beforeSwapReturnDelta: false,
            afterSwapReturnDelta: true,
            afterAddLiquidityReturnDelta: false,
            afterRemoveLiquidityReturnDelta: false
        });
    }

    function beforeSwap(address, PoolKey calldata, SwapParams calldata, bytes calldata)
        external
        pure
        returns (bytes4, BeforeSwapDelta, uint24)
    {
        revert UnauthorizedCallback();
    }

    function afterSwap(
        address,
        PoolKey calldata key,
        SwapParams calldata params,
        BalanceDelta delta,
        bytes calldata
    ) external onlyPoolManager returns (bytes4, int128) {
        if (feeBps == 0) return (this.afterSwap.selector, 0);

        // Unspecified side: exact-in -> output currency, exact-out -> input currency
        bool exactInput = params.amountSpecified < 0;
        bool feeOnCurrency0 = exactInput ? !params.zeroForOne : params.zeroForOne;
        int128 side = feeOnCurrency0 ? delta.amount0() : delta.amount1();
        uint256 base = side < 0 ? uint256(uint128(-side)) : uint256(uint128(side));

        uint256 feeAmount = (base * feeBps) / 10_000;
        if (feeAmount == 0) return (this.afterSwap.selector, 0);

        Currency feeCurrency = feeOnCurrency0 ? key.currency0 : key.currency1;
        // CEI: accounting write precedes the external PoolManager call
        totalCaptured[feeCurrency] += feeAmount;
        poolManager.take(feeCurrency, address(this), feeAmount);

        emit FeeCaptured(key.toId(), feeCurrency, feeAmount);
        return (this.afterSwap.selector, int128(int256(feeAmount)));
    }

    // ==================== Remaining IHooks stubs (never called: no flags) ====================

    function beforeInitialize(address, PoolKey calldata, uint160) external pure returns (bytes4) {
        revert UnauthorizedCallback();
    }

    function afterInitialize(address, PoolKey calldata, uint160, int24) external pure returns (bytes4) {
        revert UnauthorizedCallback();
    }

    function beforeAddLiquidity(address, PoolKey calldata, ModifyLiquidityParams calldata, bytes calldata)
        external
        pure
        returns (bytes4)
    {
        revert UnauthorizedCallback();
    }

    function afterAddLiquidity(
        address,
        PoolKey calldata,
        ModifyLiquidityParams calldata,
        BalanceDelta,
        BalanceDelta,
        bytes calldata
    ) external pure returns (bytes4, BalanceDelta) {
        revert UnauthorizedCallback();
    }

    function beforeRemoveLiquidity(address, PoolKey calldata, ModifyLiquidityParams calldata, bytes calldata)
        external
        pure
        returns (bytes4)
    {
        revert UnauthorizedCallback();
    }

    function afterRemoveLiquidity(
        address,
        PoolKey calldata,
        ModifyLiquidityParams calldata,
        BalanceDelta,
        BalanceDelta,
        bytes calldata
    ) external pure returns (bytes4, BalanceDelta) {
        revert UnauthorizedCallback();
    }

    function beforeDonate(address, PoolKey calldata, uint256, uint256, bytes calldata)
        external
        pure
        returns (bytes4)
    {
        revert UnauthorizedCallback();
    }

    function afterDonate(address, PoolKey calldata, uint256, uint256, bytes calldata)
        external
        pure
        returns (bytes4)
    {
        revert UnauthorizedCallback();
    }

    // ==================== MONETIZATION ====================

    /// @notice Sweep banked fees: treasuryBps to treasury, remainder stays as the
    ///         on-contract buyback / LP-deepen reserve. Keeper-compoundable.
    ///         Handles both ERC20 and native-ETH fee currencies (ETH pools are
    ///         the common case on Base).
    function sweep(Currency currency) external {
        uint256 balance = _selfBalance(currency);
        uint256 captured = totalCaptured[currency];
        uint256 swept = totalSwept[currency];
        uint256 available = balance < captured - swept ? balance : captured - swept;
        if (available == 0) return;

        uint256 toTreasury = (available * treasuryBps) / 10_000;
        uint256 toReserve = available - toTreasury;

        totalSwept[currency] = swept + available;
        if (toTreasury > 0) _payout(currency, treasury, toTreasury);

        emit Swept(currency, toTreasury, toReserve);
    }

    /// @notice Release reserve for buyback / LP-deepen. Owner-gated; the keeper
    ///         pattern. Pays ONLY the governed treasury sink - the owner deploys
    ///         the capital from there, so no arbitrary destination exists on-contract.
    function releaseReserve(Currency currency, uint256 amount) external onlyOwner {
        _payout(currency, treasury, amount);
    }

    // ==================== INTERNALS ====================

    function _selfBalance(Currency currency) internal view returns (uint256) {
        return currency.isAddressZero()
            ? address(this).balance
            : IERC20(Currency.unwrap(currency)).balanceOf(address(this));
    }

    function _payout(Currency currency, address to, uint256 amount) internal {
        if (currency.isAddressZero()) {
            (bool ok,) = to.call{value: amount}("");
            require(ok, "Native payout failed");
        } else {
            IERC20(Currency.unwrap(currency)).safeTransfer(to, amount);
        }
    }

    /// @dev Accept native ETH from PoolManager.take on native-currency pools.
    receive() external payable {}

    // ==================== ADMIN ====================

    function setFeeBps(uint256 newFeeBps) external onlyOwner {
        if (newFeeBps > MAX_FEE_BPS) revert FeeTooHigh();
        feeBps = newFeeBps;
        emit FeeBpsUpdated(newFeeBps);
    }

    function setTreasuryBps(uint256 newTreasuryBps) external onlyOwner {
        if (newTreasuryBps > 10_000) revert FeeTooHigh();
        treasuryBps = newTreasuryBps;
        emit TreasuryBpsUpdated(newTreasuryBps);
    }

    function setTreasury(address newTreasury) external onlyOwner {
        require(newTreasury != address(0), "Zero address");
        treasury = newTreasury;
        emit TreasuryUpdated(newTreasury);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "Zero address");
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }
}
