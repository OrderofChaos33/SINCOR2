"""P25 acceptance tests: bounded weights, high-risk exclusion, staleness,
rebalance, fee math, read API scoping.

Maps to the numbered acceptance criteria in
~/workspace/sincor2-auction-tasks/specs/p25-portfolio-engine.md.
Self-contained: uses the fixture catalog (deterministic), no network.
"""

from __future__ import annotations

import dataclasses
import random
import sys
import unittest.mock as mock
from fractions import Fraction
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import src.sincor2.defi.p25.ingestor as ingestor_mod
import src.sincor2.defi.p25.risk as risk_mod
from src.sincor2.defi.p25 import PARAMS, api, guards
from src.sincor2.defi.p25.allocator import (
    InvalidWeights, TargetPortfolio, UnconvergedAllocation, allocate,
)
from src.sincor2.defi.p25.api import ForbiddenRead, PortfolioAPI
from src.sincor2.defi.p25.fees import FeeDenominationError, FeeLedger
from src.sincor2.defi.p25.guards import CashFloorBreach
from src.sincor2.defi.p25.ingestor import CASH_ID, FeedSignal, Ingestor
from src.sincor2.defi.p25.rebalancer import plan_rebalance
from src.sincor2.defi.p25.risk import check_weights, ineligible_protocols
from src.sincor2.defi.catalog import PROTOCOL_BY_ID

R = random.Random(20260929)
PIDS = [pid for pid in PROTOCOL_BY_ID if pid != "P26_DEFI_OS"]


def _signals(n=4, now=1_000_000.0, seed=1):
    r = random.Random(seed)
    pids = r.sample(PIDS, min(n, len(PIDS)))
    return [FeedSignal(pid, r.uniform(-0.2, 0.4), now) for pid in pids]


def _pipeline(signals, now=1_000_000.0, min_cash=0.0):
    ing = Ingestor()
    res = ing.ingest(signals, now, min_cash_weight=min_cash)
    assert res.status == "OK"
    return allocate(res.weights, res.as_of, res.feed_hash)


# -- AC1: bounded weights are always valid -------------------------------------------
def test_ac1_full_pipeline_weights_valid():
    pf = _pipeline(_signals(6, 1_000_000.0))
    chk = check_weights(pf.weights)
    assert chk.ok, chk.violations
    assert abs(sum(pf.weights.values()) - 1.0) <= 1e-9
    assert pf.cash_pct >= PARAMS["cash_floor_pct"] - 1e-9
    for pid, w in pf.weights.items():
        if pid != CASH_ID:  # cash sleeve is the risk-free parking spot
            assert w <= PARAMS["position_cap"] + 1e-9, (pid, w)
    assert pf.blended_risk <= PARAMS["blended_risk_cap"] + 1e-9


def test_ac1_p26_never_allocated():
    assert "P26_DEFI_OS" in PROTOCOL_BY_ID  # it exists in the catalog...
    ing = Ingestor()
    now = 1_000_000.0
    sigs = [FeedSignal("P26_DEFI_OS", 0.99, now), *_signals(8, now, seed=7)]
    res = ing.ingest(sigs, now)
    assert "P26_DEFI_OS" in res.excluded or "P26_DEFI_OS" not in res.weights
    pf = allocate(res.weights, res.as_of, res.feed_hash)
    assert "P26_DEFI_OS" not in pf.weights  # ...but never an allocation target


def test_ac1_cash_sleeve_exempt_from_position_cap():
    # A fully defensive book (all signals negative) is 100% cash and must
    # converge: the 0.40 cap is per-strategy, cash is risk-free parking.
    ing = Ingestor()
    now = 1_000_000.0
    res = ing.ingest([FeedSignal("P01_YIELD_AGG", -0.5, now)], now)
    assert res.weights == {CASH_ID: 1.0}
    pf = allocate(res.weights, res.as_of, res.feed_hash)
    assert pf.weights == {CASH_ID: 1.0}
    assert check_weights(pf.weights).ok


def test_ac1_fuzz_1000_signal_sets():
    for i in range(1000):
        now = 1_000_000.0 + i
        ing = Ingestor()
        res = ing.ingest(_signals(R.randint(1, 8), now, seed=i), now)
        if res.status != "OK":
            continue
        try:
            pf = allocate(res.weights, res.as_of, res.feed_hash)
        except (InvalidWeights, UnconvergedAllocation) as e:
            pytest.fail(f"fuzz {i}: allocator failed on {res.weights}: {e}")
        chk = check_weights(pf.weights)
        assert chk.ok, f"fuzz {i}: {chk.violations}"
        assert abs(sum(pf.weights.values()) - 1.0) <= 1e-9


# -- AC2: high-risk exclusion follows the catalog ---------------------------------------
def test_ac2_high_risk_exclusion_follows_catalog_mutation():
    now = 1_000_000.0
    low_risk_pid = next(p for p in PIDS if PROTOCOL_BY_ID[p].risk_score <= 0.25)
    mutated = dict(PROTOCOL_BY_ID)
    mutated[low_risk_pid] = dataclasses.replace(
        PROTOCOL_BY_ID[low_risk_pid], risk_score=0.95)
    signals = [FeedSignal(low_risk_pid, 0.30, now), *_signals(5, now, seed=3)]
    patchers = [mock.patch.object(mod, "PROTOCOL_BY_ID", mutated)
                for mod in (ingestor_mod, risk_mod)]
    for p in patchers:
        p.start()
    try:
        ing = Ingestor()
        res = ing.ingest(signals, now)
        assert low_risk_pid in res.excluded
        assert low_risk_pid not in res.weights
        assert low_risk_pid in ineligible_protocols()
    finally:
        for p in patchers:
            p.stop()


def test_ac2_static_high_risk_protocols_excluded():
    now = 1_000_000.0
    risky = [p for p in PIDS if PROTOCOL_BY_ID[p].risk_score > 0.60]
    assert risky  # the fixture catalog actually has some
    ing = Ingestor()
    res = ing.ingest(_signals(12, now, seed=11), now)
    for p in risky:
        assert p not in res.weights
    assert set(risky) <= set(ineligible_protocols())


# -- AC3: stale feed -> HOLD -----------------------------------------------------------------
def test_ac3_stale_feed_holds_last_good():
    now = 1_000_000.0
    ing = Ingestor()
    good = ing.ingest(_signals(5, now, seed=5), now)
    assert good.status == "OK"
    stale = ing.ingest(_signals(5, now - 7_200.0, seed=6), now)  # 2 h old
    assert stale.status == "HOLD"
    assert stale.weights == good.weights  # last-good weights served
    assert stale.stale_feeds  # itemized
    assert "stale" in stale.alert.lower()


def test_ac3_stale_feed_allocation_still_valid():
    now = 1_000_000.0
    ing = Ingestor()
    good = ing.ingest(_signals(5, now, seed=5), now)
    stale = ing.ingest(_signals(5, now - 7_200.0, seed=6), now)
    pf = allocate(stale.weights, stale.as_of, stale.feed_hash)
    assert check_weights(pf.weights).ok
    assert pf.weights == allocate(good.weights, good.as_of,
                                  good.feed_hash).weights


# -- AC4: rebalance trigger/dust ---------------------------------------------------------------
def _target(weights, now=1_000_000.0):
    return TargetPortfolio(weights=dict(weights),
                           cash_pct=weights.get(CASH_ID, 0.0),
                           as_of=now, feed_hash="test",
                           blended_risk=0.1, iterations=1)


def test_ac4_below_2_5pct_drift_is_noop():
    current = {"P01_YIELD_AGG": 0.30, "P02_CLMM": 0.20, CASH_ID: 0.50}
    target = _target({"P01_YIELD_AGG": 0.31, "P02_CLMM": 0.20, CASH_ID: 0.49})
    plan = plan_rebalance(current, target, aum_usd=1_000_000.0)
    assert plan.action == "NOOP" and plan.trades == []


def test_ac4_above_trigger_rebalances_with_dust_filter():
    current = {"P01_YIELD_AGG": 0.30, "P02_CLMM": 0.20, CASH_ID: 0.50}
    target = _target({"P01_YIELD_AGG": 0.40, "P02_CLMM": 0.10, CASH_ID: 0.50})
    plan = plan_rebalance(current, target, aum_usd=100_000.0)
    assert plan.action == "REBALANCE"
    for t in plan.trades:
        assert abs(t.delta_usd) >= PARAMS["min_capital_usd"]
        assert t.side in ("BUY", "SELL")
    chk = check_weights(plan.post_weights)  # post-trade gates re-pass
    assert chk.ok, chk.violations
    assert abs(sum(plan.post_weights.values()) - 1.0) <= 1e-9


def test_ac4_dust_positions_retained_not_traded():
    current = {"P01_YIELD_AGG": 0.30, "P02_CLMM": 0.20, CASH_ID: 0.50}
    target = _target({"P01_YIELD_AGG": 0.40, "P02_CLMM": 0.20, CASH_ID: 0.40})
    plan = plan_rebalance(current, target, aum_usd=10_000.0)
    assert plan.action == "REBALANCE"
    assert all(t.position_id != "P02_CLMM" for t in plan.trades)  # $0 delta


def test_ac4_all_trades_dust_is_noop():
    current = {"P01_YIELD_AGG": 0.30, "P02_CLMM": 0.20, CASH_ID: 0.50}
    target = _target({"P01_YIELD_AGG": 0.40, "P02_CLMM": 0.10, CASH_ID: 0.50})
    plan = plan_rebalance(current, target, aum_usd=100.0)  # $10 deltas < $25
    assert plan.action == "NOOP" and plan.trades == []


# -- AC5: fee math is wei-exact -------------------------------------------------------------------
def test_ac5_fee_wei_exact_worked_example():
    # docs/spec/P25_ECONOMICS.md: $1M AUM, 10 bps p.a., 1-day tick,
    # AXM @ $0.50 -> 5.475701574264202600 AXM, floored to the wei.
    ledger = FeeLedger()
    rec = ledger.accrue("u1", aum_usd=1_000_000.0, tick_s=86_400.0,
                        denomination="AXM", price_usd=0.5)
    assert rec.fee_token_wei == 5_475_701_574_264_202_600
    assert rec.fee_usd == pytest.approx(
        1_000_000.0 * (10 / 10_000) * (86_400.0 / 31_557_600), rel=1e-9)
    assert rec.denomination == "AXM"


def test_ac5_denoms_supported_and_settlement_floor():
    ledger = FeeLedger()
    for denom in ("AXM", "SINC"):
        rec = ledger.accrue("u1", 1_000_000.0, 86_400.0, denom, 0.5)
        assert rec.denomination == denom and rec.fee_token_wei > 0
    with pytest.raises(FeeDenominationError):
        ledger.accrue("u1", 1_000_000.0, 86_400.0, "ETH", 0.5)
    assert ledger.settle("u1") is None  # <$10: stays accrued, no settlement
    assert ledger.accrued("u1") > 0


def test_ac5_settlement_goes_to_treasury_above_10():
    from src.sincor2.defi.catalog import TREASURY
    ledger = FeeLedger()
    ledger.accrue("u2", 100_000_000.0, 86_400.0 * 365, "SINC", 0.5)
    s = ledger.settle("u2")
    assert s is not None and s.to == TREASURY
    assert s.amount_usd >= 10.0
    assert ledger.accrued("u2") == 0


# -- AC6: read API is user-scoped -----------------------------------------------------------------------
def test_ac6_cross_user_read_rejected():
    api_ = PortfolioAPI()
    pf = _pipeline(_signals(4, 1_000_000.0, seed=9))
    ing = Ingestor()
    res = ing.ingest(_signals(4, 1_000_000.0, seed=9), 1_000_000.0)
    api_.publish("alice", pf, res, rebalance_status="NOOP")
    snap = api_.read("alice", "alice")
    assert snap["user_id"] == "alice"
    with pytest.raises(ForbiddenRead):
        api_.read("alice", "bob")
    # schema keys are stable
    assert set(snap) >= {"user_id", "weights", "cash_pct", "blended_risk",
                         "as_of", "feed_hash", "drift", "rebalance_status",
                         "fee_denomination", "fee_accrued_wei", "feed_status"}


def test_ac6_guards_cash_floor_is_enforced():
    with pytest.raises(CashFloorBreach):
        guards.require_cash_floor({"P01_YIELD_AGG": 0.97, CASH_ID: 0.03})
    assert guards.require_cash_floor({"P01_YIELD_AGG": 0.90, CASH_ID: 0.10}) == 0.10
