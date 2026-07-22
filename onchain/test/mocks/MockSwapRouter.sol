// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {IUnlockCallback} from "v4-core/src/interfaces/callback/IUnlockCallback.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency, CurrencyLibrary} from "v4-core/src/types/Currency.sol";
import {BalanceDelta} from "v4-core/src/types/BalanceDelta.sol";
import {ModifyLiquidityParams, SwapParams} from "v4-core/src/types/PoolOperation.sol";
import {TickMath} from "v4-core/src/libraries/TickMath.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

/// @notice Minimal unlock router for tests: add liquidity and swap through a PoolManager.
contract MockSwapRouter is IUnlockCallback {
    using CurrencyLibrary for Currency;

    IPoolManager public immutable poolManager;

    bytes32 constant ACTION_LIQ = keccak256("LIQ");
    bytes32 constant ACTION_SWAP = keccak256("SWAP");

    constructor(address _poolManager) {
        poolManager = IPoolManager(_poolManager);
    }

    /// @dev Accept native ETH for native-currency pools (input side / liquidity).
    receive() external payable {}

    function addLiquidity(PoolKey calldata key, int24 tickLower, int24 tickUpper, uint256 liquidity)
        external
        payable
    {
        poolManager.unlock(abi.encode(ACTION_LIQ, key, tickLower, tickUpper, liquidity, msg.sender));
        _refundNative();
    }

    function swapExactIn(PoolKey calldata key, bool zeroForOne, uint256 amountIn)
        external
        payable
        returns (uint256 amountOut)
    {
        bytes memory result =
            poolManager.unlock(abi.encode(ACTION_SWAP, key, zeroForOne, amountIn, msg.sender));
        _refundNative();
        return abi.decode(result, (uint256));
    }

    function unlockCallback(bytes calldata data) external returns (bytes memory) {
        require(msg.sender == address(poolManager), "only PM");
        bytes32 action = abi.decode(data, (bytes32));

        if (action == ACTION_LIQ) {
            (, PoolKey memory key, int24 tickLower, int24 tickUpper, uint256 liquidity, address payer) =
                abi.decode(data, (bytes32, PoolKey, int24, int24, uint256, address));
            (BalanceDelta delta,) = poolManager.modifyLiquidity(
                key,
                ModifyLiquidityParams({
                    tickLower: tickLower, tickUpper: tickUpper, liquidityDelta: int256(liquidity), salt: bytes32(0)
                }),
                ""
            );
            _settleDelta(key.currency0, delta.amount0(), payer);
            _settleDelta(key.currency1, delta.amount1(), payer);
            return "";
        }

        // SWAP
        (, PoolKey memory key, bool zeroForOne, uint256 amountIn, address payer) =
            abi.decode(data, (bytes32, PoolKey, bool, uint256, address));
        BalanceDelta delta = poolManager.swap(
            key,
            SwapParams({
                zeroForOne: zeroForOne,
                amountSpecified: -int256(amountIn),
                sqrtPriceLimitX96: zeroForOne ? TickMath.MIN_SQRT_PRICE + 1 : TickMath.MAX_SQRT_PRICE - 1
            }),
            ""
        );
        Currency input = zeroForOne ? key.currency0 : key.currency1;
        Currency output = zeroForOne ? key.currency1 : key.currency0;
        int128 outAmt = zeroForOne ? delta.amount1() : delta.amount0();

        _pay(input, uint256(uint128(zeroForOne ? -delta.amount0() : -delta.amount1())), payer);
        poolManager.take(output, payer, uint256(uint128(outAmt)));

        return abi.encode(uint256(uint128(outAmt)));
    }

    function _settleDelta(Currency currency, int128 deltaAmount, address payer) internal {
        if (deltaAmount >= 0) return;
        _pay(currency, uint256(uint128(-deltaAmount)), payer);
    }

    function _pay(Currency currency, uint256 amount, address payer) internal {
        if (currency.isAddressZero()) {
            // Native: settle directly with value (sync is ERC20-only)
            poolManager.settle{value: amount}();
            return;
        }
        poolManager.sync(currency);
        IERC20(Currency.unwrap(currency)).transferFrom(payer, address(poolManager), amount);
        poolManager.settle();
    }

    /// @dev Return unspent native to the original caller after the unlock frame.
    function _refundNative() internal {
        uint256 leftover = address(this).balance;
        if (leftover > 0) {
            (bool ok,) = msg.sender.call{value: leftover}("");
            require(ok, "refund failed");
        }
    }
}
