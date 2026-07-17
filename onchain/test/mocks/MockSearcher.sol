// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {SwapParams} from "v4-core/src/types/PoolOperation.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency, CurrencyLibrary} from "v4-core/src/types/Currency.sol";
import {BalanceDelta} from "v4-core/src/types/BalanceDelta.sol";
import {TickMath} from "v4-core/src/libraries/TickMath.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

interface IMoebiusHook {
    function executeMEV(PoolKey calldata key, uint256 flashAmount, uint256 bid, bytes calldata payload) external payable;
}

/// @notice Reference searcher. Modes (payload[0]):
///   0 = HOLD:   keep the pMEV, return it intact (tests pure mint/burn/escrow)
///   1 = CYCLE:  sell all pMEV into the pool, take real asset, rebuy exact pMEV
///   2 = EVIL:   attempt reentrant executeMEV (must be blocked)
///   3 = BURN:   send pMEV to a dead address so repayment fails
contract MockSearcher {
    using CurrencyLibrary for Currency;

    IPoolManager public immutable poolManager;
    IMoebiusHook public immutable hook;

    constructor(address _poolManager, address _hook) {
        poolManager = IPoolManager(_poolManager);
        hook = IMoebiusHook(_hook);
    }

    function moebiusExecute(bytes calldata payload) external {
        (uint8 mode, PoolKey memory key, uint256 flashAmount, bool zeroIsPToken) =
            abi.decode(payload, (uint8, PoolKey, uint256, bool));

        if (mode == 0) return;

        if (mode == 2) {
            // Reentrancy attempt - should revert the whole call chain
            hook.executeMEV(key, flashAmount, 0, payload);
            return;
        }

        Currency pmev = zeroIsPToken ? key.currency0 : key.currency1;

        if (mode == 3) {
            // Send the flash credit to a dead end - repayment must fail
            IERC20(Currency.unwrap(pmev)).transfer(address(0xdead), flashAmount);
            return;
        }

        // mode == 1: full cycle
        // Sell all pMEV -> real
        _swapExactIn(key, zeroIsPToken, flashAmount);
        // Rebuy exactly flashAmount pMEV (uses own pre-funded real asset as "external arb profit")
        _swapExactOut(key, !zeroIsPToken, flashAmount);
    }

    function _swapExactIn(PoolKey memory key, bool zeroForOne, uint256 amountIn) internal {
        Currency input = zeroForOne ? key.currency0 : key.currency1;
        Currency output = zeroForOne ? key.currency1 : key.currency0;

        BalanceDelta delta = poolManager.swap(
            key,
            SwapParams({
                zeroForOne: zeroForOne,
                amountSpecified: -int256(amountIn),
                sqrtPriceLimitX96: zeroForOne ? TickMath.MIN_SQRT_PRICE + 1 : TickMath.MAX_SQRT_PRICE - 1
            }),
            ""
        );

        // settle input
        poolManager.sync(input);
        IERC20(Currency.unwrap(input)).transfer(address(poolManager), amountIn);
        poolManager.settle();

        // take output
        int128 outAmt = zeroForOne ? delta.amount1() : delta.amount0();
        require(outAmt > 0, "no output");
        poolManager.take(output, address(this), uint256(uint128(outAmt)));
    }

    function _swapExactOut(PoolKey memory key, bool zeroForOne, uint256 amountOut) internal {
        // zeroForOne=true: pay currency0, receive currency1; false: pay currency1, receive currency0
        Currency input = zeroForOne ? key.currency0 : key.currency1;
        Currency output = zeroForOne ? key.currency1 : key.currency0;

        BalanceDelta delta = poolManager.swap(
            key,
            SwapParams({
                zeroForOne: zeroForOne,
                amountSpecified: int256(amountOut),
                sqrtPriceLimitX96: zeroForOne ? TickMath.MIN_SQRT_PRICE + 1 : TickMath.MAX_SQRT_PRICE - 1
            }),
            ""
        );

        int128 inAmt = zeroForOne ? delta.amount0() : delta.amount1();
        require(inAmt < 0, "no input owed");
        uint256 owed = uint256(uint128(-inAmt));

        poolManager.sync(input);
        IERC20(Currency.unwrap(input)).transfer(address(poolManager), owed);
        poolManager.settle();

        poolManager.take(output, address(this), amountOut);
    }
}
