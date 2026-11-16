"""PACK-2 added tests: packaging smoke, sizing/trigger edges, swarm
coordination across multiple positions, oracle-stub wiring, and upkeep
accounting for the 15%-25% of build cost/yr service form.

These complement the adapted platform tests in test_p06_perp_units.py;
they cover paths the platform suite did not exercise standalone.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from perp_hedge_swarm import (
    ONE_TIME_ENGAGEMENT_MICRO,
    UPKEEP_HIGH_BPS,
    UPKEEP_LOW_BPS,
    annual_upkeep_range_micro,
    median_build_annual_upkeep_micro,
    price_feed_for,
)
from perp_hedge_swarm.perp_hedge_swarm import (
    DeltaBand,
    FundingMonitor,
    HedgeEngine,
    LiqBuffer,
    PerpError,
    StaleOracleError,
    commitment_id,
    microdollars,
    size_short_notional,
    spot_exposure_micro,
)
from perp_hedge_swarm.price_oracle_stub import OracleError, wiring_for

P = 10**8
NOW = time.time()
PKG_SRC = Path(__file__).resolve().parents[1] / "src"


def engine_with_two_positions():
    eng = HedgeEngine()
    eng.live_gate.present_conversion_proof("founder-marker")
    eng.feed.update(int(2000 * P), NOW)
    base = int(NOW // 3600) * 3600
    for h in range(24):
        eng.funding.record(base - (23 - h) * 3600, 900)
    eng.funding.record(base + 3600, 900)
    pos1, intent1 = eng.open(10 * 10**18, microdollars(1_000_000), now=NOW)
    pos2, intent2 = eng.open(5 * 10**18, microdollars(1_000_000), now=NOW)
    return eng, pos1, pos2, intent1, intent2


# -- packaging smoke -----------------------------------------------------------
def test_package_imports_clean_in_isolated_subprocess():
    """Smoke: import with ONLY the package src on the path; the platform
    tree (sincor2) must never enter sys.modules."""
    code = (
        "import sys\n"
        "import perp_hedge_swarm\n"
        "from perp_hedge_swarm import HedgeEngine, price_feed_for\n"
        "assert not any(m == 'sincor2' or m.startswith('sincor2.') "
        "for m in sys.modules), [m for m in sys.modules if 'sincor' in m]\n"
        "print('smoke-ok')\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PKG_SRC)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, env=env, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "smoke-ok" in out.stdout


def test_dry_run_intents_never_mark_executed():
    _, _, _, intent1, intent2 = engine_with_two_positions()
    for intent in (intent1, intent2):
        assert intent.dry_run is True
        assert intent.executed is False
        assert intent.to_dict()["executed"] is False


# -- hedge sizing / trigger edges ----------------------------------------------
def test_sizing_custom_headroom():
    coll, price = 10 * 10**18, int(2000 * P)
    spot = spot_exposure_micro(coll, price)
    assert size_short_notional(coll, price, headroom_bps=0) == spot
    assert size_short_notional(coll, price, headroom_bps=1000) == \
        spot * 11_000 // 10_000


def test_band_exact_boundaries():
    band = DeltaBand()
    assert band.check(300) == "hold"        # at the band: no trip
    assert band.check(301) == "rebalance"   # first bps past the band trips
    assert band.check(201) == "hold"        # tripped, still outside re-arm
    assert band.check(200) == "rearm"       # re-arm exactly inside 2%


def test_funding_monitor_no_data():
    mon = FundingMonitor()
    ok, reason = mon.entry_allowed()
    assert not ok and "no funding data" in reason
    assert not mon.kill_signaled()


def test_liq_buffer_rejects_nonpositive_maintenance():
    with pytest.raises(PerpError):
        LiqBuffer.ratio_bp(1_000, 0)


def test_commitment_id_deterministic():
    assert commitment_id("a", "b") == commitment_id("a", "b")
    assert commitment_id("a", "b") != commitment_id("a", "c")
    assert len(commitment_id("a")) == 16


def test_price_feed_without_price_is_stale():
    eng = HedgeEngine()
    with pytest.raises(StaleOracleError):
        eng.feed.get(now=NOW)


# -- swarm coordination ----------------------------------------------------------
def test_swarm_multiple_positions_unique_ids_and_sizes():
    eng, pos1, pos2, _, _ = engine_with_two_positions()
    assert pos1.position_id != pos2.position_id
    assert set(eng._positions) == {pos1.position_id, pos2.position_id}
    # sizing scales with collateral: half the collateral, half the short
    assert pos2.short_notional_micro * 2 == pos1.short_notional_micro


def test_swarm_guardian_pause_blocks_new_opens_not_checkins():
    eng, pos1, pos2, _, _ = engine_with_two_positions()
    eng.guardian_pause_opens(["GUARDIAN_ROLE"])
    eng.feed.update(int(2000 * P), NOW + 60)
    with pytest.raises(PerpError):
        eng.open(10 * 10**18, microdollars(1_000_000), now=NOW + 60)
    maintenance = pos1.short_notional_micro * 125 // 10_000
    out = eng.check_in(pos1.position_id, maintenance * 4, maintenance,
                       now=NOW + 60)
    assert out.action == "hold"
    assert pos2.position_id in eng._positions  # peer position untouched


def test_swarm_fees_accumulate_across_unwinds():
    eng, pos1, pos2, _, _ = engine_with_two_positions()
    eng.feed.update(int(1900 * P), NOW + 60)  # price falls: shorts profit
    i1 = eng._unwind(pos1, NOW + 60, reason="test")
    i2 = eng._unwind(pos2, NOW + 60, reason="test")
    assert i1.details["fee_micro"] > 0 and i2.details["fee_micro"] > 0
    assert eng.treasury_fees_micro == \
        i1.details["fee_micro"] + i2.details["fee_micro"]
    assert eng._positions == {}


def test_swarm_one_unwind_leaves_peer_open():
    eng, pos1, pos2, _, _ = engine_with_two_positions()
    eng.feed.update(int(1900 * P), NOW + 60)
    eng._unwind(pos1, NOW + 60, reason="test")
    assert pos1.position_id not in eng._positions
    assert pos2.position_id in eng._positions


# -- vendored oracle stub ---------------------------------------------------------
class _Result:
    def __init__(self, price_fp):
        self.price_fp = price_fp


class _FakeOracle:
    def __init__(self):
        self.calls = []

    def get_price(self, asset, now=None, strict=False):
        self.calls.append((asset, now, strict))
        return _Result(int(2000 * P))


def test_stub_feed_binds_declared_assets_only():
    feed = price_feed_for(_FakeOracle())
    assert feed.assets == ["ETH/USD", "BTC/USD"]
    assert feed.price("ETH/USD", NOW) == int(2000 * P)
    with pytest.raises(OracleError):
        feed.price("DOGE/USD", NOW)


def test_stub_wiring_rejects_undeclared_product():
    with pytest.raises(KeyError):
        wiring_for("P99_UNKNOWN", _FakeOracle())


def test_price_feed_syncs_from_stub_feed():
    eng = HedgeEngine()
    feed = price_feed_for(_FakeOracle())
    synced = eng.feed.sync_from_oracle(feed, "BTC/USD", now=NOW)
    assert synced == int(2000 * P)
    assert eng.feed.get(now=NOW) == int(2000 * P)


# -- upkeep accounting (secondary service form) -----------------------------------
def test_upkeep_terms_constants():
    assert ONE_TIME_ENGAGEMENT_MICRO == 17_500 * 1_000_000
    assert UPKEEP_LOW_BPS == 1_500   # 15%
    assert UPKEEP_HIGH_BPS == 2_500  # 25%


def test_upkeep_median_build_range():
    """$17,500 build -> $2,625-$4,375 per year (catalogued range)."""
    low, high = median_build_annual_upkeep_micro()
    assert low == 2_625 * 1_000_000
    assert high == 4_375 * 1_000_000


def test_upkeep_scales_with_build_cost():
    low, high = annual_upkeep_range_micro(100_000 * 1_000_000)
    assert (low, high) == (15_000 * 1_000_000, 25_000 * 1_000_000)
    assert annual_upkeep_range_micro(0) == (0, 0)
    with pytest.raises(ValueError):
        annual_upkeep_range_micro(-1)
