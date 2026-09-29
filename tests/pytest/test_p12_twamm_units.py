"""Unit tests for P12 TWAMM Large-Order Engine reference build.

Covers src/sincor2/defi/twamm.py — deterministic slice schedules that
sum exactly to the parent order, the minimum-slice gate (no
single-block dumps), per-slice impact caps with schedule auto-extension,
the constant-product output model (sliced execution never underperforms
a dump), one-block-ahead schedule visibility, the 8 bps Treasury fee,
and the fail-closed live-submission blocker.

Pure logic, no chain access. Every test names the invariant it guards.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from src.sincor2.defi import twamm
from src.sincor2.defi.catalog import TREASURY
from src.sincor2.defi.twamm import (
    ImpactCapError,
    LiveBlockedError,
    LiveOrderBlocker,
    SliceCountError,
    SliceScheduler,
    slice_impact_bps,
    slice_output_cents,
)

PARENT = 10_000_000_00      # $10M parent order in cents
RESERVE_IN = 500_000_000_00  # $500M input-side reserve
RESERVE_OUT = 500_000_000_00
START_BLOCK = 21_000_000


def _sched(**over):
    kw = dict(parent_cents=PARENT, reserve_in_cents=RESERVE_IN,
              reserve_out_cents=RESERVE_OUT, start_block=START_BLOCK)
    kw.update(over)
    return SliceScheduler().schedule(**kw)


# -- schedule construction -------------------------------------------------------
def test_schedule_sums_exactly_to_parent():
    """AC1: slices sum to the parent order to the cent (integer-exact)."""
    for parent in (PARENT, PARENT + 7, 7_500_00, 123_456_789):
        s = _sched(parent_cents=parent)
        assert sum(s.slices_cents) == parent
        assert s.parent_cents == parent


def test_remainder_goes_to_earliest_slices():
    """AC7: integer-division remainder distributes to earliest slices."""
    s = SliceScheduler().schedule(parent_cents=7_500_00 + 3,
                                  reserve_in_cents=RESERVE_IN,
                                  reserve_out_cents=RESERVE_OUT,
                                  start_block=START_BLOCK,
                                  requested_slices=4)
    base, rem = divmod(7_500_00 + 3, 4)
    assert rem == 3
    assert s.slices_cents[:3] == [base + 1] * 3
    assert s.slices_cents[3] == base


def test_minimum_slice_count_enforced():
    """AC3: schedules below the minimum slice count are rejected."""
    with pytest.raises(SliceCountError):
        SliceScheduler().schedule(parent_cents=PARENT,
                                  reserve_in_cents=RESERVE_IN,
                                  reserve_out_cents=RESERVE_OUT,
                                  start_block=START_BLOCK,
                                  requested_slices=1)
    with pytest.raises(SliceCountError):
        SliceScheduler().schedule(parent_cents=PARENT,
                                  reserve_in_cents=RESERVE_IN,
                                  reserve_out_cents=RESERVE_OUT,
                                  start_block=START_BLOCK,
                                  requested_slices=3)
    s = _sched(requested_slices=4)
    assert s.n_slices >= 4


def test_schedule_is_deterministic():
    """AC5: identical inputs -> identical schedules, every time."""
    a = _sched()
    b = _sched()
    assert a.slices_cents == b.slices_cents
    assert a.max_slice_impact_bps == b.max_slice_impact_bps


def test_sub_order_known_one_block_ahead():
    """AC: the due sub-order is queryable for any block in the window."""
    s = _sched(requested_slices=8)
    for i in range(8):
        assert s.slice_at(START_BLOCK + i) == s.slices_cents[i]
    with pytest.raises(IndexError):
        s.slice_at(START_BLOCK - 1)
    with pytest.raises(IndexError):
        s.slice_at(START_BLOCK + 8)


# -- impact cap --------------------------------------------------------------------
def test_per_slice_impact_within_cap():
    """AC2: no slice exceeds the 50 bps impact cap."""
    s = _sched()
    assert s.max_slice_impact_bps <= twamm.IMPACT_CAP_BPS
    for sl in s.slices_cents:
        assert slice_impact_bps(sl, RESERVE_IN) <= twamm.IMPACT_CAP_BPS


def test_schedule_auto_extends_when_cap_would_breach():
    """AC2: a too-large parent auto-extends slices until the cap holds."""
    thin_reserve = 5_000_000_00  # $5M reserve vs $10M parent
    s = SliceScheduler().schedule(parent_cents=PARENT,
                                  reserve_in_cents=thin_reserve,
                                  reserve_out_cents=thin_reserve,
                                  start_block=START_BLOCK,
                                  requested_slices=4)
    assert s.n_slices > 4
    assert s.max_slice_impact_bps <= twamm.IMPACT_CAP_BPS
    assert sum(s.slices_cents) == PARENT


def test_unsatisfiable_cap_refuses_fail_closed():
    """AC2: when the cap cannot be met within the slice ceiling, refuse."""
    sched = SliceScheduler(max_slices=4)  # no room to extend
    with pytest.raises(ImpactCapError):
        sched.schedule(parent_cents=PARENT, reserve_in_cents=1_000_00,
                       reserve_out_cents=1_000_00, start_block=START_BLOCK,
                       requested_slices=4)


def test_impact_formula_spot_check():
    """AC: impact = dx/(x+dx) in bps, integer math."""
    assert slice_impact_bps(1_000_00, 99_000_00) == 100  # 1% of pool
    assert slice_impact_bps(0, 99_000_00) == 0
    with pytest.raises(ValueError):
        slice_impact_bps(1_000_00, 0)


# -- TWAMM economics -----------------------------------------------------------------
def test_sliced_output_never_underperforms_dump():
    """AC4 (corrected): under constant-product math with per-slice
    replenishment, the sliced schedule's total output >= dump output.
    (The naive 'sum of impacts' comparison is backwards: f is concave.)"""
    for parent, reserve in ((PARENT, RESERVE_IN),
                            (50_000_000_00, 100_000_000_00),
                            (7_500_00, 10_000_000_00)):
        s = SliceScheduler().schedule(parent_cents=parent,
                                      reserve_in_cents=reserve,
                                      reserve_out_cents=reserve,
                                      start_block=START_BLOCK)
        assert s.output_beats_dump, (parent, reserve)
        assert s.total_output_cents >= s.dump_output_cents


def test_slicing_strictly_improves_large_orders():
    """AC4: for a large parent vs a thin pool, slicing strictly wins."""
    s = SliceScheduler().schedule(parent_cents=50_000_000_00,
                                  reserve_in_cents=100_000_000_00,
                                  reserve_out_cents=100_000_000_00,
                                  start_block=START_BLOCK,
                                  requested_slices=16)
    assert s.total_output_cents > s.dump_output_cents


def test_slice_output_formula_spot_check():
    """AC: dy = y*dx/(x+dx), integer math."""
    # 198000.00 * 100000 / 10000000 = 1980.00 -> 198_000 cents
    assert slice_output_cents(1_000_00, 99_000_00, 198_000_00) == 1_980_00
    assert slice_output_cents(0, 99_000_00, 198_000_00) == 0


# -- fee + live-block ------------------------------------------------------------------
def test_fee_is_8bps_of_parent_to_treasury():
    """AC6: 8 bps of executed notional routes to the Treasury."""
    s = _sched()
    assert s.fee_to == TREASURY
    assert s.fee_cents == PARENT * 8 // 10_000


def test_live_submission_blocked():
    """AC: submitting a scheduled sub-order to a venue raises."""
    s = _sched()
    with pytest.raises(LiveBlockedError):
        LiveOrderBlocker().submit(s, START_BLOCK)


def test_min_capital_floor():
    """AC: parent orders below the $75 tick floor are refused."""
    with pytest.raises(ValueError):
        _sched(parent_cents=7_499)


def test_empty_slices_rejected_by_constructor():
    """AC: the scheduler cannot be built with min_slices < 2."""
    with pytest.raises(ValueError):
        SliceScheduler(min_slices=1)
