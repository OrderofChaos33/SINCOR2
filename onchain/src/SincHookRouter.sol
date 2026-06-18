// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {IUnlockCallback} from "@uniswap/v4-core/src/interfaces/callback/IUnlockCallback.sol";
import {Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {BalanceDelta} from "@uniswap/v4-core/src/types/BalanceDelta.sol";
import {SwapParams} from "@uniswap/v4-core/src/types/PoolOperation.sol";
import {TickMath} from "@uniswap/v4-core/src/libraries/TickMath.sol";
import {CurrencySettler} from "@openzeppelin/uniswap-hooks/utils/CurrencySettler.sol";
import {FloorPriceMath} from "./FloorPriceMath.sol";

/// @title SincHookRouter
/// @notice Direct USDC→SINC swaps through the v4 hook pool — lifts treasury limit walls at declared prices.
/// @dev Bypasses shallow Kyber/AMM depth. Buyer swaps zeroForOne=true (USDC in, SINC out), crossing sell rungs.
contract SincHookRouter is IUnlockCallback {
    using SafeERC20 for IERC20;
    using CurrencySettler for Currency;

    IPoolManager public immutable poolManager;
    address public immutable usdc;
    address public immutable sinc;
    address public immutable hook;

    uint24 public constant POOL_FEE = 3000;
    int24 public constant TICK_SPACING = 60;
    /// @dev Non-negotiable public floor — swaps cannot cross below $1.50/SINC.
    uint256 public constant MIN_USD_PER_SINC_E18 = 15e17;
    uint256 public constant MIN_USDC_IN = 15e5; // 1.50 USDC (6 decimals)

    error NotPoolManager();
    error InsufficientOutput();
    error BelowFloorUsdc();
    error BelowFloorPrice();

    struct SwapRequest {
        address recipient;
        PoolKey key;
        uint256 usdcIn;
        uint256 minSincOut;
    }

    event BuySinc(address indexed buyer, uint256 usdcIn, uint256 sincOut);

    constructor(IPoolManager _poolManager, address _usdc, address _sinc, address _hook) {
        poolManager = _poolManager;
        usdc = _usdc;
        sinc = _sinc;
        hook = _hook;
    }

    function sincUsdcPoolKey() public view returns (PoolKey memory key) {
        key = PoolKey({
            currency0: Currency.wrap(usdc),
            currency1: Currency.wrap(sinc),
            fee: POOL_FEE,
            tickSpacing: TICK_SPACING,
            hooks: IHooks(hook)
        });
    }

    /// @notice Spend USDC to buy SINC through the hook pool (exact input).
    /// @param recipient Address receiving SINC (defaults to msg.sender when zero).
    function buySincExactIn(
        PoolKey calldata key,
        uint256 usdcIn,
        uint256 minSincOut,
        address recipient
    ) external returns (uint256 sincOut) {
        if (usdcIn < MIN_USDC_IN) revert BelowFloorUsdc();
        IERC20(usdc).safeTransferFrom(msg.sender, address(this), usdcIn);

        address to = recipient == address(0) ? msg.sender : recipient;

        BalanceDelta delta = abi.decode(
            poolManager.unlock(abi.encode(SwapRequest({recipient: to, key: key, usdcIn: usdcIn, minSincOut: minSincOut}))),
            (BalanceDelta)
        );

        sincOut = uint256(int256(delta.amount1()));
        if (sincOut < minSincOut) revert InsufficientOutput();
        _enforceFloorRate(usdcIn, sincOut);

        emit BuySinc(msg.sender, usdcIn, sincOut);
    }

    /// @notice Base mainnet SINC/USDC hook pool — single-arg entry for frontends.
    function buySincUsdcBase(uint256 usdcIn, uint256 minSincOut, address recipient)
        external
        returns (uint256 sincOut)
    {
        if (usdcIn < MIN_USDC_IN) revert BelowFloorUsdc();
        PoolKey memory key = sincUsdcPoolKey();
        IERC20(usdc).safeTransferFrom(msg.sender, address(this), usdcIn);
        address to = recipient == address(0) ? msg.sender : recipient;
        BalanceDelta delta = abi.decode(
            poolManager.unlock(abi.encode(SwapRequest({recipient: to, key: key, usdcIn: usdcIn, minSincOut: minSincOut}))),
            (BalanceDelta)
        );
        sincOut = uint256(int256(delta.amount1()));
        if (sincOut < minSincOut) revert InsufficientOutput();
        _enforceFloorRate(usdcIn, sincOut);
        emit BuySinc(msg.sender, usdcIn, sincOut);
    }

    function unlockCallback(bytes calldata rawData) external returns (bytes memory) {
        if (msg.sender != address(poolManager)) revert NotPoolManager();

        SwapRequest memory req = abi.decode(rawData, (SwapRequest));

        uint160 floorSqrt = FloorPriceMath.sqrtPriceX96FromUsd(MIN_USD_PER_SINC_E18);
        BalanceDelta delta = poolManager.swap(
            req.key,
            SwapParams({
                zeroForOne: true,
                amountSpecified: -int256(req.usdcIn),
                sqrtPriceLimitX96: floorSqrt
            }),
            ""
        );

        int128 amount0 = delta.amount0();
        int128 amount1 = delta.amount1();

        if (amount0 < 0) {
            req.key.currency0.settle(poolManager, address(this), uint256(uint128(-amount0)), false);
        }
        if (amount1 > 0) {
            req.key.currency1.take(poolManager, req.recipient, uint256(uint128(amount1)), false);
        }

        if (uint256(int256(amount1)) < req.minSincOut) revert InsufficientOutput();
        _enforceFloorRate(req.usdcIn, uint256(int256(amount1)));

        return abi.encode(delta);
    }

    /// @dev Effective fill must be at or above $1.50/SINC (USDC 6 dec / SINC 8 dec).
    function _enforceFloorRate(uint256 usdcIn, uint256 sincOut) internal pure {
        if (sincOut == 0) revert InsufficientOutput();
        // usdcIn * 1e8 * 1e18 >= sincOut * MIN_USD_PER_SINC_E18 * 1e6
        if (usdcIn * 1e8 * 1e18 < sincOut * MIN_USD_PER_SINC_E18 * 1e6) revert BelowFloorPrice();
    }
}