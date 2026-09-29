"""Unit tests for the P06 Perp DEX Hedging Swarm reference build.

Covers src/sincor2/defi/perp_hedge_swarm.py — integer-exact sizing,
drift-based delta band with hysteresis, funding entry/kill, liq-buffer
ladder, live gate, oracle staleness, margin mode, fees, and unwind
safety behind SKU SINCOR-DEFI-P06-PERPS. Pure logic, no venue, no keys.
28/28 must pass.
"""

from __future__ import annotations

import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from src.sincor2.defi.perp_hedge_swarm import (
    DELTA_BAND_BPS,
    TREASURY,
    DeltaBand,
    FundingMonitor,
    GateViolationError,
    HedgeEngine,
    LiqBuffer,
    LiveBlockedError,
    MarginModeError,
    PerpError,
    StaleOracleError,
    UnauthorizedError,
    microdollars,
    size_short_notional,
    spot_exposure_micro,
)

P = 10**8  # price fixed-point
NOW = time.time()
TWO_X = 20_000  # 2x margin ratio in bp


def engine_with_open(collateral_wei=10 * 10**18, entry_usd=2000.0,
                     swarm_usd=1_000_000.0):
    """Engine with live gate released, fresh oracle, passing funding, one open."""
    eng = HedgeEngine()
    eng.live_gate.present_conversion_proof("founder-marker")
    eng.feed.update(int(entry_usd * P), NOW)
    base = int(NOW // 3600) * 3600
    for h in range(24):
        eng.funding.record(base - (23 - h) * 3600, 900)  # +9% all day
    eng.funding.record(base + 3600, 900)
    pos, _ = eng.open(collateral_wei, microdollars(swarm_usd), now=NOW)
    return eng, pos


def healthy_checkin(eng, pos, now=NOW):
    """check_in with a comfortable 4x margin ratio."""
    notional = pos.short_notional_micro
    maintenance = notional * 125 // 10_000  # 1.25% tier
    equity = maintenance * 4
    return eng.check_in(pos.position_id, equity, maintenance, now=now)


# -- sizing ------------------------------------------------------------------
def test_sizing_integer_exact_parity():
    """Acceptance #1: integer-only sizing, deterministic across vectors."""
    rng = random.Random(7)
    for _ in range(500):
        coll = rng.randint(10**15, 10**22)
        price = rng.randint(int(0.5 * P), int(50_000 * P))
        a = size_short_notional(coll, price)
        assert a == size_short_notional(coll, price)
        spot = spot_exposure_micro(coll, price)
        assert spot * 10500 // 10000 == a
        assert a * 10000 - spot * 10500 < 10000  # < 1 unit rounding


def test_sizing_5pct_over_hedge():
    spot = spot_exposure_micro(10 * 10**18, int(2000 * P))
    assert spot == 20_000_000_000  # $20,000 in microdollars
    short = size_short_notional(10 * 10**18, int(2000 * P))
    assert short == 21_000_000_000  # $21,000: exactly 5% over


def test_entry_baseline_recorded():
    """The 5% over-hedge is the drift baseline (spec-deviation documented)."""
    eng, pos = engine_with_open()
    # entry net: (20000 - 21000) * 10000 / 20000 = -500 bps
    assert pos.entry_net_bps == -500


# -- delta band ----------------------------------------------------------------
def test_band_holds_inside_drift():
    """Small drift -> hold, no rebalance."""
    eng, pos = engine_with_open()
    eng.feed.update(int(2010 * P), NOW + 60)  # +0.5% spot move
    out = healthy_checkin(eng, pos, now=NOW + 60)
    assert out.action == "hold"


def test_band_trips_on_3pct_drift():
    """Rule p06-delta-band: drift beyond 3% fires exactly one rebalance."""
    eng, pos = engine_with_open()
    eng.feed.update(int(2200 * P), NOW + 60)  # +10% spot move
    out = healthy_checkin(eng, pos, now=NOW + 60)
    assert out.action == "rebalance"
    assert out.details["drift_bps"] > DELTA_BAND_BPS


def test_hysteresis_zero_redundant_rebalances():
    """Acceptance #2: band-edge oscillation -> signals only on transitions."""
    band = DeltaBand()
    # drift path hugging the edge: crosses 3% twice, wiggles inside otherwise
    drifts = [0, 100, 250, 299, 301, 350, 320, 301, 299, 250, 200, 150,
              100, 250, 299, 301, 280, 199, 100, 0]
    signals = [band.check(d) for d in drifts]
    assert signals.count("rebalance") == 2  # exactly the two exits
    assert signals.count("rearm") == 2      # re-arm only inside 2%


def test_rebalance_resets_baseline():
    eng, pos = engine_with_open()
    eng.feed.update(int(2200 * P), NOW + 60)
    out = healthy_checkin(eng, pos, now=NOW + 60)
    assert out.action == "rebalance"
    eng.rebalance(pos.position_id, now=NOW + 61)
    out2 = healthy_checkin(eng, pos, now=NOW + 61)
    assert out2.action == "hold"
    assert out2.details["drift_bps"] == 0


def test_fuzz_10k_drift_discipline():
    """Acceptance #2: 10k fuzzed shocks keep drift within the band."""
    rng = random.Random(11)
    breaches = 0
    for _ in range(200):  # 200 paths x 50 steps
        band = DeltaBand()
        drift = 0
        for _ in range(50):
            drift += rng.randint(-120, 120)
            drift = max(-800, min(800, drift))
            sig = band.check(abs(drift))
            if abs(drift) > DELTA_BAND_BPS and not band.tripped:
                breaches += 1  # un-tripped breach: discipline failure
            if sig == "rebalance":
                drift = 0  # rebalance restores baseline
    assert breaches == 0


# -- funding -------------------------------------------------------------------
def test_funding_entry_gate_passes():
    mon = FundingMonitor()
    base = 1_000_000
    for h in range(25):
        mon.record(base + h * 3600, 900)
    ok, _ = mon.entry_allowed()
    assert ok


def test_funding_entry_rejects_spike_chase():
    """Rule p06-funding-sign: +8% now but weak 24h avg -> no entry."""
    mon = FundingMonitor()
    base = 1_000_000
    for h in range(24):
        mon.record(base + h * 3600, 100)  # +1% all day
    mon.record(base + 24 * 3600, 900)      # sudden +9% spike
    ok, reason = mon.entry_allowed()
    assert not ok and "spike" in reason


def test_funding_kill_two_consecutive_negatives():
    """Acceptance #3: 2 consecutive negative hours -> unwind signal."""
    mon = FundingMonitor()
    base = 1_000_000
    for h in range(5):
        mon.record(base + h * 3600, 900)
    assert not mon.kill_signaled()
    mon.record(base + 5 * 3600, -100)
    assert not mon.kill_signaled()  # single negative: no kill
    mon.record(base + 6 * 3600, -50)
    assert mon.kill_signaled()


def test_funding_kill_triggers_unwind_within_checkin():
    """Acceptance #3: kill -> agent unwinds within one 15-min check-in."""
    eng, pos = engine_with_open()
    base = int(NOW // 3600) * 3600
    eng.funding.record(base + 7200, -100)
    eng.funding.record(base + 10800, -50)
    eng.feed.update(int(2000 * P), NOW + 900)  # fresh oracle at check-in
    out = healthy_checkin(eng, pos, now=NOW + 900)
    assert out.action == "unwind"
    assert "funding_sign" in out.details["reason"]
    assert pos.position_id not in eng._positions


# -- liq buffer ------------------------------------------------------------------
def test_liq_ladder_thresholds():
    """Acceptance #4: 4x/2x/1.5x ladder statuses."""
    assert LiqBuffer.health(40_000, 10_000)[0] == "ok"
    assert LiqBuffer.health(19_999, 10_000)[0] == "agent_unwind"
    assert LiqBuffer.health(14_999, 10_000)[0] == "pause_opens"
    assert LiqBuffer.health(20_000, 10_000)[0] == "ok"  # boundary: 2x is ok


def test_liq_breach_agent_unwind_in_flight():
    eng, pos = engine_with_open()
    maintenance = pos.short_notional_micro * 125 // 10_000
    out = eng.check_in(pos.position_id, maintenance * 19 // 10, maintenance,
                       now=NOW + 60)  # 1.9x
    assert out.action == "unwind"
    assert "liq_buffer" in out.details["reason"]


def test_liq_below_1p5x_pauses_opens():
    """Rule p06-liq-buffer: < 1.5x pauses new opens; unwind still works."""
    eng, pos = engine_with_open()
    maintenance = pos.short_notional_micro * 125 // 10_000
    out = eng.check_in(pos.position_id, maintenance * 14 // 10, maintenance,
                       now=NOW + 60)  # 1.4x
    assert out.action == "unwind"
    assert eng.opens_paused
    eng.feed.update(int(2000 * P), NOW + 120)
    with pytest.raises(GateViolationError):
        eng.open(10 * 10**18, microdollars(1_000_000), now=NOW + 120)


def test_volatility_shocks_no_liquidation_without_unwind():
    """Acceptance #4: shocks -> unwind fires before any liquidation print."""
    eng, pos = engine_with_open()
    # -30% spot crash: equity collapses -> unwind must already be in flight
    eng.feed.update(int(1400 * P), NOW + 60)
    maintenance = pos.short_notional_micro * 125 // 10_000
    spot = spot_exposure_micro(pos.collateral_wei, int(1400 * P))
    out = eng.check_in(pos.position_id, spot, maintenance, now=NOW + 60)
    # equity == spot value; ratio = spot/maintenance; either unwind or ok,
    # never a silent sub-1x print
    ratio = LiqBuffer.ratio_bp(spot, maintenance)
    if ratio < TWO_X:
        assert out.action == "unwind"
    else:
        assert out.action in ("hold", "rebalance")


# -- live gate / oracle / margin ---------------------------------------------------
def test_live_gate_blocks_open_by_default():
    """Acceptance #5: every live path reverts with liveEnabled=false."""
    eng = HedgeEngine()
    eng.feed.update(int(2000 * P), NOW)
    base = int(NOW // 3600) * 3600
    for h in range(25):
        eng.funding.record(base + h * 3600, 900)
    with pytest.raises(LiveBlockedError):
        eng.open(10 * 10**18, microdollars(1_000_000), now=NOW)


def test_live_gate_release_path():
    eng = HedgeEngine()
    with pytest.raises(LiveBlockedError):
        eng.live_gate.assert_live()
    eng.live_gate.present_conversion_proof("founder-marker")
    eng.live_gate.assert_live()  # no raise


def test_live_gate_blocks_rebalance_path():
    """Acceptance #5: rebalance is a live path — reverts pre-proof.

    check_in may still EMIT a rebalance intent (dry-run signal), but the
    engine.rebalance execution path refuses until the conversion proof.
    """
    eng = HedgeEngine()  # live gate NOT released
    eng.feed.update(int(2000 * P), NOW)
    base = int(NOW // 3600) * 3600
    for h in range(25):
        eng.funding.record(base + h * 3600, 900)
    # Seed a position directly (bypassing open, which is also gated).
    from src.sincor2.defi.perp_hedge_swarm import Position
    pos = Position("pos-1", 10 * 10**18, 21_000_000_000, int(2000 * P), NOW)
    eng._positions[pos.position_id] = pos
    with pytest.raises(LiveBlockedError):
        eng.rebalance(pos.position_id, now=NOW)
    eng.live_gate.present_conversion_proof("founder-marker")
    out = eng.rebalance(pos.position_id, now=NOW + 61)
    assert out.action == "rebalance"


def test_stale_oracle_blocks_new_actions():
    """Rule p06-oracle: price older than 120s -> fail-static."""
    eng = HedgeEngine()
    eng.live_gate.present_conversion_proof("founder-marker")
    eng.feed.update(int(2000 * P), NOW - 1_000)  # stale
    with pytest.raises(StaleOracleError):
        eng.feed.get(now=NOW)


def test_cross_margin_rejected():
    """Rule p06-margin: isolated margin only, enforced at the adapter."""
    eng = HedgeEngine()
    eng.live_gate.present_conversion_proof("founder-marker")
    eng.feed.update(int(2000 * P), NOW)
    base = int(NOW // 3600) * 3600
    for h in range(25):
        eng.funding.record(base + h * 3600, 900)
    with pytest.raises(MarginModeError):
        eng.open(10 * 10**18, microdollars(1_000_000),
                 margin_mode="cross", now=NOW)


def test_min_capital_and_alloc_cap():
    eng, pos = engine_with_open()
    eng.feed.update(int(2000 * P), NOW + 120)
    with pytest.raises(GateViolationError):  # below $250
        eng.open(10**15, microdollars(1_000_000), now=NOW + 120)
    with pytest.raises(GateViolationError):  # above 15% of swarm
        eng.open(10 * 10**18, microdollars(100_000), now=NOW + 120)


def test_guardian_cannot_lift_live_gate():
    """Risk gate: guardian pauses opens but cannot flip liveEnabled."""
    eng = HedgeEngine()  # gate NOT released here
    eng.guardian_pause_opens(["GUARDIAN_ROLE"])
    assert eng.opens_paused
    assert not eng.live_gate.live_enabled  # guardian action can't enable
    with pytest.raises(UnauthorizedError):
        eng.guardian_pause_opens(["INTRUDER"])


# -- fees / unwind ---------------------------------------------------------------
def test_fee_exact_12bps_on_profit():
    """Acceptance #6: exactly 12 bps of positive realized P&L to treasury."""
    eng, pos = engine_with_open()
    eng.feed.update(int(1900 * P), NOW + 60)  # price falls: short profits
    intent = eng._unwind(pos, NOW + 60, reason="test")
    pnl = intent.details["realized_pnl_micro"]
    assert pnl > 0
    assert intent.details["fee_micro"] == pnl * 12 // 10_000
    assert intent.details["treasury"] == TREASURY
    assert eng.treasury_fees_micro == intent.details["fee_micro"]


def test_fee_zero_on_losing_close():
    """Acceptance #6: losing closes route zero fees, not penalized."""
    eng, pos = engine_with_open()
    eng.feed.update(int(2100 * P), NOW + 60)  # price rises: short loses
    intent = eng._unwind(pos, NOW + 60, reason="test")
    assert intent.details["realized_pnl_micro"] < 0
    assert intent.details["fee_micro"] == 0


def test_unwind_conservation():
    eng, pos = engine_with_open()
    eng.feed.update(int(1900 * P), NOW + 60)
    spot = spot_exposure_micro(pos.collateral_wei, int(1900 * P))
    intent = eng._unwind(pos, NOW + 60, reason="test")
    d = intent.details
    assert d["settled_micro"] + d["fee_micro"] == spot + d["realized_pnl_micro"]


def test_unwind_never_bricks_on_reverting_recipient():
    """Acceptance #7: reverting recipient -> pull fallback, unwind completes."""
    eng = HedgeEngine(reverting={"pos-1"})
    eng.live_gate.present_conversion_proof("founder-marker")
    eng.feed.update(int(2000 * P), NOW)
    base = int(NOW // 3600) * 3600
    for h in range(25):
        eng.funding.record(base + h * 3600, 900)
    pos, _ = eng.open(10 * 10**18, microdollars(1_000_000), now=NOW)
    eng.feed.update(int(1900 * P), NOW + 60)
    intent = eng._unwind(pos, NOW + 60, reason="test")
    assert intent.details["settled_micro"] == 0
    assert intent.details["pending_micro"] > 0
    assert pos.position_id not in eng._positions  # unwind completed
    assert eng.withdraw_pending("pos-1") == intent.details["pending_micro"]


def test_break_even_honesty_check():
    """Tip: at 10% APR with 0.5% round trip, hold ~18 days to break even."""
    round_trip_cost = 0.005
    funding_apr = 0.10
    break_even_days = round_trip_cost / funding_apr * 365
    assert break_even_days == pytest.approx(18.25, abs=0.01)
    # the entry gate (+8% now / +5% avg) implies break-even <= 23 days
    assert 0.005 / 0.08 * 365 <= 23
