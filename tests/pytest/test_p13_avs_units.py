"""Unit tests for P13 AVS Tranching & Restaking reference build.

Covers src/sincor2/defi/avs_tranching.py — the slash-oracle freshness
gate (fail-closed on stale/missing data), the junior cap, the
senior_cover gate (junior buffer must cover margin + expected slashing
loss), the strict integer-exact slashing waterfall (junior first, senior
only after junior is wiped), the yield waterfall (senior fixed target
first, junior residual), the 18 bps Treasury fee, and the fail-closed
live-restake blocker.

Pure logic, no chain access. Every test names the invariant it guards.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from src.sincor2.defi import avs_tranching as avs
from src.sincor2.defi.catalog import TREASURY
from src.sincor2.defi.avs_tranching import (
    AVSRisk,
    CoverBreachError,
    JuniorCapError,
    LiveBlockedError,
    LiveRestakeBlocker,
    SlashOracle,
    StaleOracleError,
    Trancher,
    apply_slash,
    distribute_yield,
)

NOW = 1_700_000_000
YEAR = 365 * 86400
CAPITAL = 1_000_000_00  # $1M allocation in cents


def _oracle(**over) -> SlashOracle:
    o = SlashOracle()
    kw = dict(avs_id="avs1", slash_prob_bps=200,  # 2%/yr
              expected_slash_severity_bps=5000,   # 50% severity
              as_of=NOW)
    kw.update(over)
    o.publish(AVSRisk(**kw))
    return o


def _trancher(oracle=None, **kw) -> Trancher:
    return Trancher(oracle or _oracle(), **kw)


# -- slash oracle ----------------------------------------------------------------
def test_oracle_accepts_fresh_data():
    """AC: fresh oracle reads succeed."""
    r = _oracle().read("avs1", NOW)
    assert r.slash_prob_bps == 200


def test_stale_oracle_blocks_allocation_fail_closed():
    """AC4: stale oracle data blocks new allocations (fail-closed)."""
    o = _oracle(as_of=NOW - 3601)
    with pytest.raises(StaleOracleError):
        _trancher(o).allocate("avs1", CAPITAL, 0.30, NOW)


def test_missing_oracle_data_blocks_allocation():
    """AC4: no oracle data at all -> blocked, never assumed safe."""
    o = SlashOracle()
    with pytest.raises(StaleOracleError):
        _trancher(o).allocate("ghost", CAPITAL, 0.30, NOW)


def test_oracle_validates_ranges():
    """AC: probability/severity outside 0..10000 bps refused."""
    o = SlashOracle()
    with pytest.raises(ValueError):
        o.publish(AVSRisk("x", 10_001, 5000, NOW))
    with pytest.raises(ValueError):
        o.publish(AVSRisk("x", 100, 10_001, NOW))


# -- junior cap + senior cover -----------------------------------------------------
def test_junior_cap_enforced():
    """AC1: junior above 30% is rejected; at-cap accepted."""
    t = _trancher()
    with pytest.raises(JuniorCapError):
        t.allocate("avs1", CAPITAL, 0.31, NOW)
    alloc = t.allocate("avs1", CAPITAL, 0.30, NOW)
    assert alloc.junior_cents == int(CAPITAL * 0.30)
    assert alloc.senior_cents == CAPITAL - alloc.junior_cents


def test_zero_junior_rejected():
    """AC1: a zero junior tranche leaves senior uncovered -> rejected."""
    with pytest.raises(JuniorCapError):
        _trancher().allocate("avs1", CAPITAL, 0.0, NOW)


def test_senior_cover_gate_rejects_thin_junior_on_risky_avs():
    """AC3: risky AVS + thin junior -> coverage < 1.10x -> rejected."""
    risky = _oracle(avs_id="risky", slash_prob_bps=1000,  # 10%/yr
                    expected_slash_severity_bps=8000)     # 80% severity
    t = _trancher(risky)
    # expected loss = 1000*8000/10000 = 800bps = 8%; junior 5% -> cover
    # = 5/95 + (1-0.08) = 0.0526+0.92 = 0.9726x < 1.10x
    with pytest.raises(CoverBreachError):
        t.allocate("risky", CAPITAL, 0.05, NOW)


def test_senior_cover_gate_accepts_thick_junior_on_risky_avs():
    """AC3: same risky AVS with max junior -> covered -> accepted."""
    risky = _oracle(avs_id="risky", slash_prob_bps=1000,
                    expected_slash_severity_bps=8000)
    t = _trancher(risky)
    alloc = t.allocate("risky", CAPITAL, 0.30, NOW)
    # cover = 30/70 + (1-0.08) = 0.4286 + 0.92 = 1.3486x >= 1.10x
    assert alloc.coverage_x100 >= 110


def test_safe_avs_passes_cover_with_modest_junior():
    """AC3: low-risk AVS needs only a modest junior buffer."""
    safe = _oracle(avs_id="safe", slash_prob_bps=50,   # 0.5%/yr
                   expected_slash_severity_bps=2000)   # 20% severity
    t = _trancher(safe)
    # expected loss = 50*2000/10000 = 10bps; junior 10% -> cover =
    # 10/90 + (1-0.001) = 0.1111+0.999 = 1.1101x >= 1.10x
    alloc = t.allocate("safe", CAPITAL, 0.10, NOW)
    assert alloc.coverage_x100 >= 110


def test_min_capital_floor():
    """AC: allocations below the $300 tick floor refused."""
    with pytest.raises(ValueError):
        _trancher().allocate("avs1", 29_999, 0.30, NOW)


# -- slashing waterfall ---------------------------------------------------------------
def test_waterfall_junior_absorbs_first():
    """AC2: loss smaller than junior -> senior untouched."""
    alloc = _trancher().allocate("avs1", CAPITAL, 0.30, NOW)
    out = apply_slash(alloc, loss_cents=alloc.junior_cents // 2)
    assert out.junior_loss_cents == alloc.junior_cents // 2
    assert out.senior_loss_cents == 0
    assert out.senior_surviving_cents == alloc.senior_cents
    assert (out.junior_surviving_cents + out.junior_loss_cents
            == alloc.junior_cents)


def test_waterfall_senior_impaired_only_after_junior_wiped():
    """AC2: loss exceeding junior -> senior impaired by the remainder."""
    alloc = _trancher().allocate("avs1", CAPITAL, 0.30, NOW)
    loss = alloc.junior_cents + 1_000_00
    out = apply_slash(alloc, loss_cents=loss)
    assert out.junior_loss_cents == alloc.junior_cents
    assert out.junior_surviving_cents == 0
    assert out.senior_loss_cents == 1_000_00
    assert out.senior_surviving_cents == alloc.senior_cents - 1_000_00


def test_waterfall_is_integer_exact():
    """AC2: every cent is accounted: losses + survivors == allocation."""
    alloc = _trancher().allocate("avs1", CAPITAL, 0.30, NOW)
    for loss in (0, 1, alloc.junior_cents - 1, alloc.junior_cents,
                 alloc.junior_cents + 1, CAPITAL - 1, CAPITAL, CAPITAL + 999):
        out = apply_slash(alloc, loss_cents=loss)
        assert (out.junior_loss_cents + out.senior_loss_cents
                + out.junior_surviving_cents + out.senior_surviving_cents
                == CAPITAL)
        assert out.loss_cents == min(loss, CAPITAL)  # clamped, reported


def test_waterfall_negative_loss_clamped():
    """AC2: negative loss is clamped to zero, never a gain."""
    alloc = _trancher().allocate("avs1", CAPITAL, 0.30, NOW)
    out = apply_slash(alloc, loss_cents=-500)
    assert out.loss_cents == 0
    assert out.senior_surviving_cents == alloc.senior_cents


# -- yield waterfall ---------------------------------------------------------------------
def test_senior_paid_target_first_junior_gets_residual():
    """AC5: senior fixed 6% target paid first; junior takes the residual."""
    alloc = _trancher().allocate("avs1", CAPITAL, 0.30, NOW)
    total_yield = 130_000_00  # 13% gross, one year
    d = distribute_yield(alloc, total_yield, YEAR)
    senior_target = alloc.senior_cents * 6 // 100
    assert d.senior_paid_cents == senior_target
    assert d.junior_paid_cents == total_yield - senior_target
    assert d.junior_paid_cents > d.senior_paid_cents  # levered junior


def test_senior_capped_at_available_yield():
    """AC5: when yield cannot cover the senior target, senior takes all."""
    alloc = _trancher().allocate("avs1", CAPITAL, 0.30, NOW)
    d = distribute_yield(alloc, 10_000_00, YEAR)  # 1% gross < 6% target
    assert d.senior_paid_cents == 10_000_00
    assert d.junior_paid_cents == 0


def test_fee_is_18bps_of_distributed_yield_to_treasury():
    """AC6: 18 bps of distributed yield routes to the Treasury."""
    alloc = _trancher().allocate("avs1", CAPITAL, 0.30, NOW)
    d = distribute_yield(alloc, 130_000_00, YEAR)
    assert d.fee_to == TREASURY
    assert d.fee_cents == 130_000_00 * 18 // 10_000
    # fee is on distributed yield, split is on the remainder implicitly:
    # senior + junior account for the full gross yield
    assert d.senior_paid_cents + d.junior_paid_cents == 130_000_00


def test_negative_yield_rejected():
    """AC5: losses flow through apply_slash, never the yield path."""
    alloc = _trancher().allocate("avs1", CAPITAL, 0.30, NOW)
    with pytest.raises(ValueError):
        distribute_yield(alloc, -1, YEAR)


# -- live-block ----------------------------------------------------------------------------
def test_live_restake_blocked():
    """AC: any live restaking operation raises, fail-closed."""
    alloc = _trancher().allocate("avs1", CAPITAL, 0.30, NOW)
    with pytest.raises(LiveBlockedError):
        LiveRestakeBlocker().restake_live(alloc)


def test_trancher_records_allocations():
    """AC: allocations are recorded for the audit trail."""
    t = _trancher()
    a1 = t.allocate("avs1", CAPITAL, 0.30, NOW)
    assert t.allocations == [a1]
    assert a1.opened_at == NOW
