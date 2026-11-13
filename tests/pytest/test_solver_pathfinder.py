"""Pathfinder tests over a deterministic fixture snapshot.

Fixture market (hand-built, internally consistent):
- ETH = $3,000 on the Uniswap V3 0.05% WETH/USDC pool and the V2 pool
- ETH = $2,980 on the Uniswap V3 0.30% WETH/USDC pool (a ~67bps gap,
  wide enough to survive the 0.05% + 0.30% LP-fee stack on a round trip)
- AXM = $0.50 via AXM/WETH V3 1% pool (no AXM/USDC pool in fixture)
- SINC = $0.15 via a SINC/WETH V2 pool (single venue)
"""

from fractions import Fraction
from math import isqrt

import pytest

from sincor2.solver import pool_math as pm
from sincor2.solver.pathfinder import (
    GAS_PER_HOP, GAS_TX_BASE, PriceBook, find_routes,
)
from sincor2.solver.pools import (
    AXM, SINC, USDC, WETH, PoolSnapshot, V2PoolState, V3PoolState,
)

GAS_PRICE = 6_000_000  # wei, ~what Base charged on 2026-11-11


def _sqrt_from_price(num: int, den: int) -> int:
    """sqrtPriceX96 for raw price num/den (token1 per token0)."""
    return isqrt((num << 192) // den)


def _v3_pool(address, token0, token1, fee_pips, spacing, price_num,
             price_den, liquidity=10**22):
    sqrt_p = _sqrt_from_price(price_num, price_den)
    return V3PoolState(
        venue="uniswap_v3", address=address, token0=token0, token1=token1,
        fee_pips=fee_pips, tick_spacing=spacing, sqrt_price_x96=sqrt_p,
        tick=pm.get_tick_at_sqrt_ratio(sqrt_p), liquidity=liquidity,
        initialized_ticks={pm.MIN_TICK + 1: 0, pm.MAX_TICK - 1: 0})


def fixture_snapshot() -> PoolSnapshot:
    # token0 = WETH (0x42..) < USDC (0x83..); raw price = USDC_raw/WETH_raw
    # $3,000 -> (3000 * 1e6) / 1e18 = 3e-9 ; $2,980 -> 2980e6/1e18
    v3_500 = _v3_pool("0xPOOL_V3_500", WETH, USDC, 500, 10,
                      3000 * 10**6, 10**18)
    v3_3000 = _v3_pool("0xPOOL_V3_3000", WETH, USDC, 3000, 60,
                       2980 * 10**6, 10**18)
    v2 = V2PoolState(venue="uniswap_v2", address="0xPOOL_V2", token0=WETH,
                     token1=USDC, reserve0=1000 * 10**18,
                     reserve1=3000 * 1000 * 10**6, fee_bps=30)
    # AXM/WETH: token0 = WETH (0x42..) < AXM (0x4c..); raw price AXM/WETH:
    # 1 WETH = 6000 AXM (both 18dp) -> price token1/token0 = 6000
    axm_weth = _v3_pool("0xPOOL_AXM_WETH", WETH, AXM, 10000, 200, 6000, 1,
                        liquidity=10**23)
    # SINC/WETH V2: token0 = WETH < SINC (0xe1..); 1 WETH = 20000 SINC.
    # SINC has 8 decimals (verified on-chain), so 200,000 SINC = 2e13 raw.
    sinc_weth = V2PoolState(venue="uniswap_v2", address="0xPOOL_SINC_WETH",
                            token0=WETH, token1=SINC, reserve0=10 * 10**18,
                            reserve1=200_000 * 10**8, fee_bps=30)
    return PoolSnapshot(block_number=123456, gas_price_wei=GAS_PRICE,
                        pools=(v3_500, v3_3000, v2, axm_weth, sinc_weth))


def test_price_book_derives_usd_prices():
    book = PriceBook.from_snapshot(fixture_snapshot())
    assert book.usd_price(USDC) == 1
    assert abs(book.usd_price(WETH) - 3000) < 1          # ~$3,000
    assert abs(book.usd_price(AXM) - Fraction(1, 2)) < Fraction(1, 100)
    assert abs(book.usd_price(SINC) - Fraction(15, 100)) < Fraction(1, 100)


def test_direct_route_weth_usdc_best_is_single_hop():
    snap = fixture_snapshot()
    book = PriceBook.from_snapshot(snap)
    amount = 10 * 10**18  # 10 WETH
    routes = find_routes(snap, WETH, USDC, amount, book)
    assert routes, "expected at least one route"
    best = routes[0]
    assert len(best.hops) == 1
    assert best.amount_out_gross > 29_000 * 10**6  # > $29k of USDC pre-fee
    # platform fee is exactly 5% of gross, floored
    assert best.platform_fee_amount == best.amount_out_gross * 500 // 10_000
    assert best.amount_out_net == best.amount_out_gross - best.platform_fee_amount


def test_multihop_route_axm_to_usdc_goes_via_weth():
    snap = fixture_snapshot()
    book = PriceBook.from_snapshot(snap)
    amount = 20_000 * 10**18  # 20k AXM = $10k
    routes = find_routes(snap, AXM, USDC, amount, book)
    assert routes
    best = routes[0]
    assert len(best.hops) == 2
    assert best.hops[0].token_out == best.hops[1].token_in
    assert best.hops[0].venue == "uniswap_v3"
    # $10k in, three fee layers (1% LP, best-case LP, 5% platform): net < $9.5k
    net_usd = book.to_usd(USDC, best.amount_out_net)
    assert Fraction(8_000) < net_usd < Fraction(9_500)


def test_gas_model_scales_with_hops_and_venue():
    snap = fixture_snapshot()
    book = PriceBook.from_snapshot(snap)
    one = find_routes(snap, WETH, USDC, 10**18, book)[0]
    two = find_routes(snap, AXM, USDC, 10**21, book)[0]
    assert one.gas_units == GAS_TX_BASE + GAS_PER_HOP["uniswap_v3"]
    assert two.gas_units == (GAS_TX_BASE + GAS_PER_HOP["uniswap_v3"]
                             + GAS_PER_HOP["uniswap_v3"])
    assert two.gas_units > one.gas_units
    assert one.gas_cost_usd is not None and one.gas_cost_usd > 0


def test_routes_ranked_by_net_value():
    snap = fixture_snapshot()
    book = PriceBook.from_snapshot(snap)
    routes = find_routes(snap, WETH, USDC, 10 * 10**18, book)
    values = [r.net_value_usd for r in routes]
    assert values == sorted(values, reverse=True)


def test_no_route_for_unknown_token():
    snap = fixture_snapshot()
    book = PriceBook.from_snapshot(snap)
    unknown = "0x000000000000000000000000000000000000dEaD"
    assert find_routes(snap, unknown, USDC, 10**18, book) == []


def test_same_token_route_is_empty():
    snap = fixture_snapshot()
    book = PriceBook.from_snapshot(snap)
    assert find_routes(snap, WETH, WETH, 10**18, book) == []


def test_platform_fee_can_be_disabled_for_gross_comparison():
    snap = fixture_snapshot()
    book = PriceBook.from_snapshot(snap)
    routes = find_routes(snap, WETH, USDC, 10**18, book,
                         apply_platform_fee=False)
    assert routes[0].platform_fee_amount == 0
    assert routes[0].amount_out_net == routes[0].amount_out_gross
