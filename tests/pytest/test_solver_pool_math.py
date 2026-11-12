"""Unit tests for sincor2.solver.pool_math — known-value vectors.

Vectors are the canonical ones: Uniswap V2's documented formula outputs
and uniswap-v3-core's published TickMath constants (MIN/MAX sqrt ratio),
plus hand-computable SqrtPriceMath deltas.
"""

import pytest

from sincor2.solver import pool_math as pm

Q96 = pm.Q96


# ---------------------------------------------------------------------------
# Constant product (V2)
# ---------------------------------------------------------------------------

def test_v2_amount_out_small_pool():
    # in=10, reserves 1000/1000, 30bps fee -> floor(99700*1000/10099700) = 9
    assert pm.v2_amount_out(10, 1000, 1000, 30) == 9


def test_v2_amount_out_large_pool():
    # in=10_000, reserves 1e6/1e6, 30bps -> 9871 (hand-computed)
    assert pm.v2_amount_out(10_000, 1_000_000, 1_000_000, 30) == 9871


def test_v2_amount_out_zero_input_and_fee_monotonic():
    assert pm.v2_amount_out(0, 1000, 1000, 30) == 0
    low_fee = pm.v2_amount_out(10_000, 1_000_000, 1_000_000, 5)
    high_fee = pm.v2_amount_out(10_000, 1_000_000, 1_000_000, 100)
    assert low_fee > high_fee > 0


def test_v2_amount_out_empty_pool_raises():
    with pytest.raises(pm.InsufficientLiquidity):
        pm.v2_amount_out(10, 0, 1000, 30)


def test_v2_never_exceeds_reserve_out():
    out = pm.v2_amount_out(10**12, 10**6, 10**6, 30)
    assert 0 < out < 10**6


# ---------------------------------------------------------------------------
# TickMath constants (published uniswap-v3-core values)
# ---------------------------------------------------------------------------

def test_sqrt_ratio_at_tick_zero_is_q96():
    assert pm.get_sqrt_ratio_at_tick(0) == 79228162514264337593543950336


def test_sqrt_ratio_at_min_tick():
    assert pm.get_sqrt_ratio_at_tick(pm.MIN_TICK) == 4295128739


def test_sqrt_ratio_at_max_tick():
    assert pm.get_sqrt_ratio_at_tick(pm.MAX_TICK) == pm.MAX_SQRT_RATIO


def test_sqrt_ratio_monotonic_and_tick_roundtrip():
    ticks = [-500000, -1000, -1, 0, 1, 1000, 123456, 500000]
    ratios = [pm.get_sqrt_ratio_at_tick(t) for t in ticks]
    assert ratios == sorted(ratios)
    for t, r in zip(ticks, ratios):
        assert pm.get_tick_at_sqrt_ratio(r) == t


def test_tick_at_sqrt_ratio_q96_is_zero():
    assert pm.get_tick_at_sqrt_ratio(Q96) == 0


def test_tick_out_of_range_raises():
    with pytest.raises(ValueError):
        pm.get_sqrt_ratio_at_tick(pm.MAX_TICK + 1)


# ---------------------------------------------------------------------------
# SqrtPriceMath deltas — hand-computable vectors
# ---------------------------------------------------------------------------

def test_amount1_delta_exact_vector():
    # L = Q96, sqrtA = Q96, sqrtB = 2*Q96 -> amount1 = L*(B-A)/Q96 = Q96
    assert pm.get_amount1_delta(Q96, 2 * Q96, Q96, False) == Q96
    assert pm.get_amount1_delta(Q96, 2 * Q96, Q96, True) == Q96


def test_amount0_delta_exact_vector():
    # L = Q96, sqrtA = Q96, sqrtB = 2*Q96 -> amount0 = L*Q96*(B-A)/(A*B) = Q96/2
    assert pm.get_amount0_delta(Q96, 2 * Q96, Q96, False) == Q96 // 2


def test_amount_deltas_order_insensitive_and_zero_width():
    assert pm.get_amount1_delta(2 * Q96, Q96, Q96, False) == Q96
    assert pm.get_amount0_delta(Q96, Q96, Q96, False) == 0
    assert pm.get_amount1_delta(Q96, Q96, Q96, False) == 0


def test_amount0_delta_rounds_up_when_asked():
    # choose values with a remainder so rounding direction is observable
    down = pm.get_amount0_delta(Q96, Q96 + 3, 7, False)
    up = pm.get_amount0_delta(Q96, Q96 + 3, 7, True)
    assert up >= down
    assert up - down <= 1


# ---------------------------------------------------------------------------
# V3 exact-input quoter
# ---------------------------------------------------------------------------

def _wide_ticks():
    # one initialized tick far in each direction, zero net: a single
    # effectively-unbounded range around tick 0
    return {pm.MIN_TICK + 1: 0, pm.MAX_TICK - 1: 0}


def test_v3_quote_small_swap_near_spot():
    # price 1.0, L = 1e18, swap 1e15 of token1 in (oneForZero), 0.3% fee.
    # Expect out ~= in * (1 - fee) * price-impact-adjusted, within a few wei
    # of the float expectation, and the LP fee ~= 0.3% of input.
    amount_in = 10**15
    out, consumed, fee, end_sqrt = pm.quote_v3_exact_in(
        Q96, 0, 10**18, 3000, False, amount_in, _wide_ticks())
    assert consumed == amount_in
    expected = amount_in * 0.997 / (1 + amount_in * 0.997 / 10**18)
    assert abs(out - expected) <= 5
    assert abs(fee - amount_in * 3 // 1000) <= 2
    assert end_sqrt > Q96  # price moved up (token1 in pushes price up)


def test_v3_quote_zero_for_one_moves_price_down():
    amount_in = 10**15
    out, consumed, fee, end_sqrt = pm.quote_v3_exact_in(
        Q96, 0, 10**18, 3000, True, amount_in, _wide_ticks())
    assert consumed == amount_in
    assert 0 < out < amount_in  # fee + impact at price 1.0
    assert end_sqrt < Q96


def test_v3_quote_respects_fee_tier():
    amount_in = 10**15
    out_cheap, _, fee_cheap, _ = pm.quote_v3_exact_in(
        Q96, 0, 10**18, 500, False, amount_in, _wide_ticks())
    out_pricey, _, fee_pricey, _ = pm.quote_v3_exact_in(
        Q96, 0, 10**18, 10000, False, amount_in, _wide_ticks())
    assert out_cheap > out_pricey
    assert fee_cheap < fee_pricey


def test_v3_quote_crossing_tick_applies_liquidity_net():
    # Active range [500, 2000) with L=1e18 at tick 1000. Crossing 2000
    # going up removes 1e18 (net = -1e18) leaving zero liquidity; a swap
    # too large to finish inside the range must run out of tick data
    # rather than invent liquidity.
    sqrt_1000 = pm.get_sqrt_ratio_at_tick(1000)
    ticks = {500: 10**18, 2000: -(10**18)}
    with pytest.raises(pm.InsufficientTickData):
        pm.quote_v3_exact_in(sqrt_1000, 1000, 10**18, 3000, False,
                             10**19, ticks)
    # a small swap completes entirely inside the range
    out, consumed, _, _ = pm.quote_v3_exact_in(
        sqrt_1000, 1000, 10**18, 3000, False, 10**12, ticks)
    assert consumed == 10**12 and out > 0


def test_v3_quote_no_tick_data_raises():
    with pytest.raises(pm.InsufficientTickData):
        pm.quote_v3_exact_in(Q96, 0, 10**18, 3000, False, 10**12, {})


def test_v3_quote_empty_pool_raises():
    with pytest.raises(pm.InsufficientLiquidity):
        pm.quote_v3_exact_in(Q96, 0, 0, 3000, False, 10**12, _wide_ticks())


def test_v3_spot_price_at_q96_is_one():
    num, den = pm.v3_spot_price(Q96)
    assert num == den


def test_v3_quote_monotonic_in_input():
    outs = []
    for amt in (10**12, 10**13, 10**14, 10**15):
        out, _, _, _ = pm.quote_v3_exact_in(
            Q96, 0, 10**18, 3000, False, amt, _wide_ticks())
        outs.append(out)
    assert outs == sorted(outs)
    # marginal price worsens with size (concavity of the curve)
    assert outs[3] / 10**15 < outs[0] / 10**12
