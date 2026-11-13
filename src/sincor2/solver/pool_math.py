"""
Pure integer pool math for the SINCOR solver quoting engine.

Implements the public Uniswap V2 constant-product formula and the public
Uniswap V3 concentrated-liquidity math (TickMath / SqrtPriceMath /
SwapMath from the open-source uniswap-v3-core) with Python big integers.
No floats anywhere in the quoting path; every rounding direction follows
the canonical on-chain convention (amounts owed TO a pool round up,
amounts paid OUT of a pool round down), so quotes never over-promise.

This module is pure: no I/O, no RPC, no state. Pool state and tick data
are passed in by the caller.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

Q96 = 1 << 96
MIN_TICK = -887272
MAX_TICK = 887272
MIN_SQRT_RATIO = 4295128739
MAX_SQRT_RATIO = 1461446703485210103287273052203988822378723970342
FEE_DENOMINATOR_PIPS = 1_000_000  # v3 fees are in hundredths of a bip


class InsufficientLiquidity(Exception):
    """The pool cannot fill the requested amount within known state."""


class InsufficientTickData(Exception):
    """The swap walks past the tick range the caller supplied."""


# --------------------------------------------------------------------------
# FullMath helpers (mulDiv is exact with Python big ints)
# --------------------------------------------------------------------------

def mul_div(a: int, b: int, denominator: int) -> int:
    return (a * b) // denominator


def mul_div_rounding_up(a: int, b: int, denominator: int) -> int:
    result = (a * b) // denominator
    if (a * b) % denominator != 0:
        result += 1
    return result


def div_rounding_up(numerator: int, denominator: int) -> int:
    q, r = divmod(numerator, denominator)
    return q + (1 if r else 0)


# --------------------------------------------------------------------------
# Constant product (Uniswap V2 / Aerodrome volatile)
# --------------------------------------------------------------------------

def v2_amount_out(amount_in: int, reserve_in: int, reserve_out: int,
                  fee_bps: int) -> int:
    """Exact output for a constant-product swap, fee taken on input.

    Canonical Uniswap V2 formula:
        out = (in * (B - fee) * reserveOut) / (reserveIn * B + in * (B - fee))
    with B = 10_000 and fee in bps (30 for Uniswap V2).
    """
    if amount_in <= 0:
        return 0
    if reserve_in <= 0 or reserve_out <= 0:
        raise InsufficientLiquidity("empty constant-product pool")
    if not 0 <= fee_bps < 10_000:
        raise ValueError("fee_bps out of range")
    amount_in_with_fee = amount_in * (10_000 - fee_bps)
    numerator = amount_in_with_fee * reserve_out
    denominator = reserve_in * 10_000 + amount_in_with_fee
    return numerator // denominator


def v2_spot_price(reserve_in: int, reserve_out: int) -> Tuple[int, int]:
    """Spot price as an exact (numerator, denominator) = reserveOut/reserveIn."""
    if reserve_in <= 0:
        raise InsufficientLiquidity("empty constant-product pool")
    return reserve_out, reserve_in


# --------------------------------------------------------------------------
# TickMath (port of uniswap-v3-core TickMath; constants are from the
# public source and are the canonical Q128 magic numbers)
# --------------------------------------------------------------------------

_TICK_BIT_CONSTANTS = (
    (0x2, 0xfff97272373d413259a46990580e213a),
    (0x4, 0xfff2e50f5f656932ef12357cf3c7fdcc),
    (0x8, 0xffe5caca7e10e4e61c3624eaa0941cd0),
    (0x10, 0xffcb9843d60f6159c9db58835c926644),
    (0x20, 0xff973b41fa98c081472e6896dfb254c0),
    (0x40, 0xff2ea16466c96a3843ec78b326b52861),
    (0x80, 0xfe5dee046a99a2a811c461f1969c3053),
    (0x100, 0xfcbe86c7900a88aedcffc83b479aa3a4),
    (0x200, 0xf987a7253ac413176f2b074cf7815e54),
    (0x400, 0xf3392b0822b70005940c7a398e4b70f3),
    (0x800, 0xe7159475a2c29b7443b29c7fa6e889d9),
    (0x1000, 0xd097f3bdfd2022b8845ad8f792aa5825),
    (0x2000, 0xa9f746462d870fdf8a65dc1f90e061e5),
    (0x4000, 0x70d869a156d2a1b890bb3df62baf32f7),
    (0x8000, 0x31be135f97d08fd981231505542fcfa6),
    (0x10000, 0x9aa508b5b7a84e1c677de54f3e99bc9),
    (0x20000, 0x5d6af8dedb81196699c329225ee604),
    (0x40000, 0x2216e584f5fa1ea926041bedfe98),
    (0x80000, 0x48a170391f7dc42444e8fa2),
)


def get_sqrt_ratio_at_tick(tick: int) -> int:
    """sqrt(1.0001**tick) as a Q64.96 fixed point (canonical TickMath)."""
    if not MIN_TICK <= tick <= MAX_TICK:
        raise ValueError("tick out of range")
    abs_tick = abs(tick)
    if abs_tick & 0x1:
        ratio = 0xfffcb933bd6fad37aa2d162d1a594001
    else:
        ratio = 0x100000000000000000000000000000000
    for bit, constant in _TICK_BIT_CONSTANTS:
        if abs_tick & bit:
            ratio = (ratio * constant) >> 128
    if tick > 0:
        # canonical: ratio = type(uint256).max / ratio
        ratio = ((1 << 256) - 1) // ratio
    sqrt_price_x96 = (ratio >> 32) + (0 if ratio % (1 << 32) == 0 else 1)
    return sqrt_price_x96


def get_tick_at_sqrt_ratio(sqrt_price_x96: int) -> int:
    """Inverse of get_sqrt_ratio_at_tick via binary search (exact).

    The canonical implementation uses a log2 expansion; binary search
    over the bounded tick range returns the identical result because
    get_sqrt_ratio_at_tick is strictly increasing in tick.
    """
    if not MIN_SQRT_RATIO <= sqrt_price_x96 < MAX_SQRT_RATIO:
        raise ValueError("sqrt ratio out of range")
    lo, hi = MIN_TICK, MAX_TICK
    # invariant: ratio(lo) <= sqrt < ratio(hi + 1)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if get_sqrt_ratio_at_tick(mid) <= sqrt_price_x96:
            lo = mid
        else:
            hi = mid - 1
    return lo


# --------------------------------------------------------------------------
# SqrtPriceMath (port of uniswap-v3-core SqrtPriceMath)
# --------------------------------------------------------------------------

def get_amount0_delta(sqrt_ratio_a: int, sqrt_ratio_b: int, liquidity: int,
                      round_up: bool) -> int:
    """Amount of token0 between two prices for the given liquidity."""
    if sqrt_ratio_a > sqrt_ratio_b:
        sqrt_ratio_a, sqrt_ratio_b = sqrt_ratio_b, sqrt_ratio_a
    if sqrt_ratio_a == 0:
        raise ValueError("sqrt ratio cannot be zero")
    numerator1 = liquidity << 96
    numerator2 = sqrt_ratio_b - sqrt_ratio_a
    if round_up:
        return div_rounding_up(
            mul_div_rounding_up(numerator1, numerator2, sqrt_ratio_b),
            sqrt_ratio_a,
        )
    return (numerator1 * numerator2 // sqrt_ratio_b) // sqrt_ratio_a


def get_amount1_delta(sqrt_ratio_a: int, sqrt_ratio_b: int, liquidity: int,
                      round_up: bool) -> int:
    """Amount of token1 between two prices for the given liquidity."""
    if sqrt_ratio_a > sqrt_ratio_b:
        sqrt_ratio_a, sqrt_ratio_b = sqrt_ratio_b, sqrt_ratio_a
    if round_up:
        return mul_div_rounding_up(liquidity, sqrt_ratio_b - sqrt_ratio_a, Q96)
    return mul_div(liquidity, sqrt_ratio_b - sqrt_ratio_a, Q96)


def get_next_sqrt_price_from_input(sqrt_price: int, liquidity: int,
                                   amount_in: int, zero_for_one: bool) -> int:
    """Next sqrt price after adding amount_in of the input token."""
    if sqrt_price <= 0 or liquidity <= 0:
        raise ValueError("invalid price/liquidity")
    if amount_in == 0:
        return sqrt_price
    if zero_for_one:
        return _next_sqrt_from_amount0_rounding_up(sqrt_price, liquidity, amount_in)
    # amount1 in: sqrtP + (amountIn << 96) / liquidity, rounding down
    return sqrt_price + ((amount_in << 96) // liquidity)


def _next_sqrt_from_amount0_rounding_up(sqrt_price: int, liquidity: int,
                                        amount_in: int) -> int:
    numerator1 = liquidity << 96
    product = amount_in * sqrt_price
    # canonical overflow-safe branch structure, exact with Python big ints
    denominator = numerator1 + product
    return mul_div_rounding_up(numerator1, sqrt_price, denominator)


# --------------------------------------------------------------------------
# SwapMath.computeSwapStep — exact-input branch (port of uniswap-v3-core)
# --------------------------------------------------------------------------

def compute_swap_step(sqrt_current: int, sqrt_target: int, liquidity: int,
                      amount_remaining: int, fee_pips: int
                      ) -> Tuple[int, int, int, int]:
    """One swap step toward sqrt_target.

    Returns (sqrt_next, amount_in_step, amount_out_step, fee_amount), all
    for the exact-input case. Direction is implied: zero_for_one when
    sqrt_target < sqrt_current.
    """
    zero_for_one = sqrt_current >= sqrt_target
    amount_remaining_less_fee = mul_div(
        amount_remaining, FEE_DENOMINATOR_PIPS - fee_pips, FEE_DENOMINATOR_PIPS
    )
    if zero_for_one:
        amount_in_to_target = get_amount0_delta(
            sqrt_target, sqrt_current, liquidity, True)
    else:
        amount_in_to_target = get_amount1_delta(
            sqrt_current, sqrt_target, liquidity, True)
    if amount_remaining_less_fee >= amount_in_to_target:
        sqrt_next = sqrt_target
    else:
        sqrt_next = get_next_sqrt_price_from_input(
            sqrt_current, liquidity, amount_remaining_less_fee, zero_for_one)
    max_step = sqrt_target == sqrt_next
    if zero_for_one:
        amount_in_step = amount_in_to_target if max_step else get_amount0_delta(
            sqrt_next, sqrt_current, liquidity, True)
        amount_out_step = get_amount1_delta(
            sqrt_next, sqrt_current, liquidity, False)
    else:
        amount_in_step = amount_in_to_target if max_step else get_amount1_delta(
            sqrt_current, sqrt_next, liquidity, True)
        amount_out_step = get_amount0_delta(
            sqrt_current, sqrt_next, liquidity, False)
    if sqrt_next != sqrt_target:
        fee_amount = amount_remaining - amount_in_step
    else:
        fee_amount = mul_div_rounding_up(
            amount_in_step, fee_pips, FEE_DENOMINATOR_PIPS - fee_pips)
    return sqrt_next, amount_in_step, amount_out_step, fee_amount


# --------------------------------------------------------------------------
# V3 exact-input quoter walking initialized ticks (Quoter semantics)
# --------------------------------------------------------------------------

def quote_v3_exact_in(sqrt_price: int, tick: int, liquidity: int,
                      fee_pips: int, zero_for_one: bool, amount_in: int,
                      initialized_ticks: Dict[int, int]
                      ) -> Tuple[int, int, int, int]:
    """Quote an exact-input V3 swap, walking initialized ticks.

    ``initialized_ticks`` maps tick -> liquidityNet for every initialized
    tick the walk may cross. Returns
    (amount_out, amount_in_consumed, total_fee, end_sqrt_price).
    Raises InsufficientTickData if the walk leaves the supplied tick set
    before the input is consumed, InsufficientLiquidity if the pool's
    price range is exhausted.
    """
    if amount_in <= 0:
        return 0, 0, 0, sqrt_price
    if liquidity <= 0:
        raise InsufficientLiquidity("v3 pool has no active liquidity")
    sorted_ticks = sorted(initialized_ticks)
    sqrt_p = sqrt_price
    current_tick = tick
    active_liquidity = liquidity
    remaining = amount_in
    total_out = 0
    total_fee = 0
    steps = 0
    while remaining > 0:
        steps += 1
        if steps > 10_000:
            raise InsufficientLiquidity("swap walk exceeded step cap")
        if zero_for_one:
            candidates = [t for t in sorted_ticks if t <= current_tick]
            if not candidates:
                raise InsufficientTickData(
                    "no supplied initialized tick below the walk")
            tick_next = candidates[-1]
        else:
            candidates = [t for t in sorted_ticks if t > current_tick]
            if not candidates:
                raise InsufficientTickData(
                    "no supplied initialized tick above the walk")
            tick_next = candidates[0]
        sqrt_target = get_sqrt_ratio_at_tick(tick_next)
        sqrt_next, amt_in, amt_out, fee = compute_swap_step(
            sqrt_p, sqrt_target, active_liquidity, remaining, fee_pips)
        remaining -= amt_in + fee
        total_out += amt_out
        total_fee += fee
        sqrt_p = sqrt_next
        if sqrt_p == sqrt_target:
            # cross the tick: apply liquidityNet (negated when going down)
            net = initialized_ticks[tick_next]
            if zero_for_one:
                active_liquidity -= net
                current_tick = tick_next - 1
            else:
                active_liquidity += net
                current_tick = tick_next
            if active_liquidity < 0:
                raise InsufficientLiquidity("liquidity went negative crossing tick")
            if remaining > 0 and active_liquidity == 0:
                # no liquidity until the next initialized tick: jump price
                # to it without consuming input (canonical behavior moves
                # the price across the empty range for free)
                continue
        else:
            current_tick = get_tick_at_sqrt_ratio(sqrt_p)
    return total_out, amount_in - remaining, total_fee, sqrt_p


def v3_spot_price(sqrt_price_x96: int) -> Tuple[int, int]:
    """Spot price token1/token0 as exact (numerator, denominator).

    price = (sqrtPriceX96 / 2**96) ** 2 = sqrt^2 / 2**192.
    """
    return sqrt_price_x96 * sqrt_price_x96, 1 << 192
