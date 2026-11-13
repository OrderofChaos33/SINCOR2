"""Full-loop test: intent -> route -> quote -> spread report.

Drives the whole engine over the deterministic fixture snapshot from
test_solver_pathfinder: a SolverQuoter produces an unsigned quote for a
$10k intent, and the spread scanner measures the fixture's built-in
cross-venue spread. "Done means wired, not just green": this test is
the wiring proof.
"""

import time
from fractions import Fraction

import pytest

from sincor2.solver.pathfinder import PriceBook
from sincor2.solver.pools import AXM, SINC, USDC, WETH
from sincor2.solver.quote_engine import (
    IntentExpiredError, NoRouteError, SolverQuoter,
)
from sincor2.solver.spread_scanner import scan_snapshot, summarize

from test_solver_pathfinder import fixture_snapshot


def test_full_loop_intent_to_quote_to_spread_report():
    snap = fixture_snapshot()
    book = PriceBook.from_snapshot(snap)
    quoter = SolverQuoter(snap, book)
    now = int(time.time())

    # 1. intent -> route -> quote: $10k of AXM into USDC (2-hop via WETH)
    amount_in = 20_000 * 10**18
    quote = quoter.quote(AXM, USDC, amount_in, deadline=now + 600,
                         requester="full-loop-test", now=now)
    assert quote["mode"] == "PAPER"
    assert quote["signed"] is False and quote["signature"] is None
    assert quote["block_number"] == 123456
    assert len(quote["route"]) == 2
    assert quote["route"][0]["amount_in"] == amount_in
    assert quote["route"][1]["amount_in"] == quote["route"][0]["amount_out"]
    assert quote["amount_out_gross"] == quote["route"][-1]["amount_out"]
    # fee breakdown reconciles exactly
    fee = quote["fees"]["platform_fee_amount"]
    assert fee == quote["amount_out_gross"] * 500 // 10_000
    assert quote["amount_out_net"] == quote["amount_out_gross"] - fee
    assert quote["min_amount_out"] == quote["amount_out_net"] * 9950 // 10_000
    assert quote["expires_at"] == min(now + 600, now + 30)
    assert quote["gas"]["estimated_units"] > 21_000
    assert quote["gas"]["cost_usd"] is not None

    # 2. spread report over the same snapshot
    scan = scan_snapshot(snap, book, sizes_usd=(100, 10_000))
    assert scan["block_number"] == 123456
    rows = summarize(scan)
    weth_rows = [r for r in rows if r["pair"] == "WETH/USDC"
                 and r["size_usd"] == 10_000]
    assert weth_rows, "WETH/USDC is multi-venue in the fixture"
    # fixture carries a $3,000 vs $2,980 cross-venue gap (~67bps, wider
    # than the round-trip LP-fee stack): the round trip must show a
    # positive gross spread in at least one direction
    grosses = [r["gross_bps"] for r in weth_rows if r["gross_bps"] is not None]
    assert grosses and max(grosses) > 0
    for row in weth_rows:
        if row["net_after_gas_bps"] is not None and row["gross_bps"] is not None:
            assert row["net_after_gas_bps"] <= row["gross_bps"]
    # at the fixture's 0.006-gwei Base gas price the gas drag rounds to
    # ~0.00bps; re-scan the same pools at a spiked 2-gwei gas price and
    # the net must fall strictly below gross
    from sincor2.solver.pools import PoolSnapshot
    hot = PoolSnapshot(block_number=123456, gas_price_wei=2_000_000_000,
                       pools=snap.pools)
    hot_rows = [r for r in summarize(scan_snapshot(hot, book,
                                                   sizes_usd=(10_000,)))
                if r["pair"] == "WETH/USDC"]
    for row in hot_rows:
        if row["net_after_gas_bps"] is not None and row["gross_bps"]:
            assert row["net_after_gas_bps"] < row["gross_bps"]
    # single-venue pair is reported as such, never dressed up as a spread
    sinc_pair = [p for p in scan["pairs"] if p["pair"] == "WETH/SINC"]
    assert sinc_pair and sinc_pair[0]["multi_venue"] is False
    assert sinc_pair[0]["measurements"] == []


def test_quote_expired_intent_rejected():
    snap = fixture_snapshot()
    quoter = SolverQuoter(snap)
    now = int(time.time())
    with pytest.raises(IntentExpiredError):
        quoter.quote(WETH, USDC, 10**18, deadline=now - 1, now=now)


def test_quote_no_route_rejected():
    snap = fixture_snapshot()
    quoter = SolverQuoter(snap)
    now = int(time.time())
    unknown = "0x000000000000000000000000000000000000dEaD"
    with pytest.raises(NoRouteError):
        quoter.quote(unknown, USDC, 10**18, deadline=now + 60, now=now)


def test_quote_direct_weth_usdc_ten_k():
    snap = fixture_snapshot()
    book = PriceBook.from_snapshot(snap)
    quoter = SolverQuoter(snap, book)
    now = int(time.time())
    amount_in = book.from_usd(WETH, Fraction(10_000))
    quote = quoter.quote(WETH, USDC, amount_in, deadline=now + 120, now=now)
    assert len(quote["route"]) == 1
    net_usd = book.to_usd(USDC, quote["amount_out_net"])
    # $10k in -> net between $9.3k (after 5% platform fee + LP fee) and $10k
    assert Fraction(9_300) < net_usd < Fraction(10_000)
