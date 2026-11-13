"""
Cross-DEX spread scanner — paper measurement only.

For every token pair quoted on two or more venues in a snapshot, this
measures *effective* round-trip spreads at real trade sizes: buy the
pair on venue A, sell it back on venue B (A != B), with each leg quoted
through the actual pool math (LP fees and price impact included). It
reports the gross spread and the spread net of gas, and net of gas plus
the SINCOR 5% platform fee under two treatments (fee on the captured
profit; fee on the full output) so the waterfall is fully transparent.

Nothing here trades. It reads a snapshot and does arithmetic.
"""

from __future__ import annotations

from fractions import Fraction
from typing import List, Optional

from . import pool_math as pm
from .pathfinder import GAS_TX_BASE, GAS_PER_HOP, PriceBook
from .pools import PoolSnapshot, PoolState, TOKEN_SYMBOLS, checksum

DEFAULT_SIZES_USD = (100, 1_000, 10_000)
PLATFORM_FEE_BPS = 500

# Reference bars from the 2026-11-10 paper model of solver economics:
BREAK_EVEN_SPREAD_BPS = 23        # ~0.23%
FLOOR_CLEARING_BPS_AT_10K = 76    # ~0.76% at $10k size


def _pair_key(pool: PoolState):
    return tuple(sorted((checksum(pool.token0), checksum(pool.token1))))


def _symbol(address: str) -> str:
    for known, sym in TOKEN_SYMBOLS.items():
        if checksum(known) == checksum(address):
            return sym
    return address[:10]


def _gas_units_two_swaps(pool_a: PoolState, pool_b: PoolState) -> int:
    """Two swaps bundled in one transaction (base paid once)."""
    return GAS_TX_BASE + GAS_PER_HOP[pool_a.venue] + GAS_PER_HOP[pool_b.venue]


def scan_snapshot(snapshot: PoolSnapshot,
                  price_book: Optional[PriceBook] = None,
                  sizes_usd=DEFAULT_SIZES_USD) -> dict:
    """Measure cross-venue round-trip spreads in one snapshot."""
    price_book = price_book or PriceBook.from_snapshot(snapshot)
    eth_usd = price_book.usd_price(_weth())
    pairs: dict = {}
    for pool in snapshot.pools:
        pairs.setdefault(_pair_key(pool), []).append(pool)

    pair_reports = []
    for (token_a, token_b), pools in sorted(pairs.items()):
        entry = {
            "pair": f"{_symbol(token_a)}/{_symbol(token_b)}",
            "token_a": token_a,
            "token_b": token_b,
            "venues": [
                {"venue": p.venue, "pool": p.address,
                 "fee_bps": (p.fee_bps if hasattr(p, "fee_bps")
                             else p.fee_pips // 100)}
                for p in pools
            ],
            "multi_venue": len(pools) >= 2,
            "measurements": [],
        }
        if len(pools) < 2:
            pair_reports.append(entry)
            continue
        for token_in, token_out in ((token_a, token_b), (token_b, token_a)):
            price_in = price_book.usd_price(token_in)
            if price_in is None:
                continue
            for size in sizes_usd:
                amount_in = price_book.from_usd(token_in, Fraction(size))
                if not amount_in:
                    continue
                entry["measurements"].append(_measure_direction(
                    snapshot, price_book, pools, token_in, token_out,
                    amount_in, size, eth_usd))
        pair_reports.append(entry)
    return {
        "block_number": snapshot.block_number,
        "gas_price_wei": snapshot.gas_price_wei,
        "eth_usd": (round(float(eth_usd), 4) if eth_usd is not None else None),
        "pairs": pair_reports,
    }


def _weth() -> str:
    from .pools import WETH
    return WETH


def _measure_direction(snapshot, price_book, pools, token_in, token_out,
                       amount_in, size_usd, eth_usd) -> dict:
    record = {
        "direction": f"{_symbol(token_in)}->{_symbol(token_out)}",
        "size_usd": size_usd,
        "amount_in": amount_in,
        "venue_quotes": [],
        "best_roundtrip": None,
        "unquotable": [],
    }
    # single-hop quotes per venue
    quoted = {}
    for pool in pools:
        try:
            out, _fee = pool.quote(token_in, amount_in)
            quoted[pool.address] = (pool, out)
            record["venue_quotes"].append({
                "venue": pool.venue, "pool": pool.address, "amount_out": out})
        except (pm.InsufficientLiquidity, pm.InsufficientTickData,
                ValueError) as exc:
            record["unquotable"].append({
                "venue": pool.venue, "pool": pool.address,
                "reason": type(exc).__name__})
    # round trips across ordered venue pairs
    best = None
    for buy_addr, (buy_pool, out_mid) in quoted.items():
        for sell_addr, (sell_pool, _o) in quoted.items():
            if buy_addr == sell_addr:
                continue
            try:
                back, _f = sell_pool.quote(token_out, out_mid)
            except (pm.InsufficientLiquidity, pm.InsufficientTickData,
                    ValueError):
                continue
            profit = back - amount_in
            profit_usd = price_book.to_usd(token_in, profit)
            gas_units = _gas_units_two_swaps(buy_pool, sell_pool)
            gas_usd = None
            if eth_usd is not None:
                gas_usd = (Fraction(gas_units * snapshot.gas_price_wei,
                                    10 ** 18) * eth_usd)
            size_frac = Fraction(size_usd)
            gross_bps = (float(profit_usd / size_frac * 10_000)
                         if profit_usd is not None else None)
            net_gas_bps = None
            net_gas_fee_profit_bps = None
            net_gas_fee_output_bps = None
            if profit_usd is not None and gas_usd is not None:
                net_gas_bps = float((profit_usd - gas_usd) / size_frac * 10_000)
                fee_on_profit = (max(profit_usd, Fraction(0))
                                 * PLATFORM_FEE_BPS // 10_000)
                net_gas_fee_profit_bps = float(
                    (profit_usd - gas_usd - fee_on_profit) / size_frac * 10_000)
                back_usd = price_book.to_usd(token_in, back)
                if back_usd is not None:
                    fee_on_output = back_usd * PLATFORM_FEE_BPS // 10_000
                    net_gas_fee_output_bps = float(
                        (profit_usd - gas_usd - fee_on_output)
                        / size_frac * 10_000)
            candidate = {
                "buy_venue": buy_pool.venue, "buy_pool": buy_addr,
                "sell_venue": sell_pool.venue, "sell_pool": sell_addr,
                "amount_out_mid": out_mid, "amount_back": back,
                "profit_wei": profit,
                "gas_units_bundled": gas_units,
                "gross_bps": (round(gross_bps, 2)
                              if gross_bps is not None else None),
                "net_after_gas_bps": (round(net_gas_bps, 2)
                                      if net_gas_bps is not None else None),
                "net_after_gas_fee_on_profit_bps": (
                    round(net_gas_fee_profit_bps, 2)
                    if net_gas_fee_profit_bps is not None else None),
                "net_after_gas_fee_on_output_bps": (
                    round(net_gas_fee_output_bps, 2)
                    if net_gas_fee_output_bps is not None else None),
            }
            if best is None or (candidate["net_after_gas_bps"] or -1e18) > \
                    (best["net_after_gas_bps"] or -1e18):
                best = candidate
    record["best_roundtrip"] = best
    return record


def summarize(scan: dict) -> List[dict]:
    """Flatten a scan into the headline rows used by reports."""
    rows = []
    for pair in scan["pairs"]:
        for m in pair["measurements"]:
            best = m["best_roundtrip"]
            rows.append({
                "pair": pair["pair"],
                "direction": m["direction"],
                "size_usd": m["size_usd"],
                "venues_quoted": len(m["venue_quotes"]),
                "unquotable": len(m["unquotable"]),
                "gross_bps": best["gross_bps"] if best else None,
                "net_after_gas_bps": best["net_after_gas_bps"] if best else None,
                "net_after_gas_fee_on_profit_bps":
                    best["net_after_gas_fee_on_profit_bps"] if best else None,
                "buy_venue": best["buy_venue"] if best else None,
                "sell_venue": best["sell_venue"] if best else None,
            })
    return rows
