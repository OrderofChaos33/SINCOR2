"""Audit-prep invariant + adversarial fuzz tests for P12 (twamm).

Property tests an auditor would demand of the TWAMM slice-schedule
reference model:

- math_fuzz: slice impact and output match the documented constant-product
  formulas exactly (integer math); non-positive slices output 0; reserves
  <= 0 raise; impact is always < 10000 bps.
- schedule_fuzz: slice sums equal the parent to the cent; schedule length
  is >= the 4-slice minimum; the worst per-slice impact never exceeds the
  50 bps cap; total modeled output never underperforms the single-block
  dump (concavity); the 8 bps treasury fee is integer-exact; identical
  inputs always produce identical slice lists (deterministic).
- gate_fuzz: parents below the $75 floor, requested counts below the
  minimum, and non-positive reserves are all rejected; an unsatisfiable
  impact cap raises ImpactCapError instead of silently violating the cap.
- lookahead_fuzz: slice_at returns the exact scheduled sub-order for every
  block in the window and raises IndexError outside it.
- live_fuzz: venue submission always raises, fail-closed.

Money is integer cents. Deterministic: seeded RNG. N/N must pass.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.sincor2.defi import twamm  # noqa: E402
from src.sincor2.defi.catalog import TREASURY  # noqa: E402
from src.sincor2.defi.twamm import (  # noqa: E402
    LiveOrderBlocker,
    SliceScheduler,
    slice_impact_bps,
    slice_output_cents,
)

RNG = random.Random(0xA01212)


def test_slice_math_matches_documented_formulas():
    for _ in range(1000):
        dx = RNG.choice([RNG.randint(1, 10**12), 0, -5])
        x = RNG.choice([RNG.randint(1, 10**15), 1])
        y = RNG.choice([RNG.randint(1, 10**15), 1])
        if dx <= 0:
            assert slice_impact_bps(dx, x) == 0
            assert slice_output_cents(dx, x, y) == 0
        else:
            assert slice_impact_bps(dx, x) == dx * 10_000 // (x + dx)
            assert slice_impact_bps(dx, x) < 10_000  # dx/(x+dx) < 1 always
            assert slice_output_cents(dx, x, y) == y * dx // (x + dx)
    for bad_reserve in (0, -1):
        try:
            slice_impact_bps(100, bad_reserve)
            raise AssertionError("non-positive reserve accepted")
        except ValueError:
            pass
        try:
            slice_output_cents(100, bad_reserve, 10**9)
            raise AssertionError("non-positive reserve accepted")
        except ValueError:
            pass


def test_schedule_load_bearing_invariants():
    sched = SliceScheduler()
    for _ in range(200):
        parent = RNG.choice([RNG.randint(twamm.MIN_CAPITAL_CENTS, 10**12),
                             twamm.MIN_CAPITAL_CENTS])
        xin = RNG.randint(10**6, 10**15)
        xout = RNG.randint(10**6, 10**15)
        start = RNG.randint(0, 10**6)
        req = RNG.choice([4, 8, RNG.randint(4, 64)])
        s = sched.schedule(parent, xin, xout, start, requested_slices=req)
        # sum-to-parent to the cent
        assert sum(s.slices_cents) == parent
        # never a single slice; at least the minimum
        assert s.n_slices >= twamm.MIN_SLICES
        # impact cap respected on every slice
        assert s.max_slice_impact_bps <= twamm.IMPACT_CAP_BPS
        assert all(slice_impact_bps(sl, xin) <= twamm.IMPACT_CAP_BPS
                   for sl in s.slices_cents)
        # the economic point, honestly bounded: integer floor on each slice
        # can cost up to 1 cent per slice vs the single floored dump, while
        # concavity holds on the reals. FINDING (reported): the module
        # docstring's "never underperforms" is exact only on real numbers;
        # on integer math the deficit is bounded by n_slices cents.
        assert s.dump_output_cents - s.total_output_cents <= s.n_slices
        assert s.total_output_cents + s.n_slices >= s.dump_output_cents
        # 8 bps treasury fee, integer-exact, to the canonical treasury
        assert s.fee_cents == parent * twamm.FEE_BPS // 10_000
        assert s.fee_to == TREASURY
        # all slices positive
        assert all(sl > 0 for sl in s.slices_cents)
        # deterministic: identical inputs -> identical slice list
        s2 = sched.schedule(parent, xin, xout, start, requested_slices=req)
        assert s2.slices_cents == s.slices_cents


def test_schedule_auto_extends_under_pressure():
    # thin-ish reserves force auto-extension beyond the requested count
    sched = SliceScheduler()
    s = sched.schedule(10**8, 10**9, 10**9, 0, requested_slices=4)
    assert s.n_slices > 4
    assert s.max_slice_impact_bps <= twamm.IMPACT_CAP_BPS
    assert sum(s.slices_cents) == 10**8
    # pathological thinness: cap unsatisfiable within the ceiling
    tight = SliceScheduler(max_slices=8)
    try:
        tight.schedule(10**12, 100, 100, 0, requested_slices=4)
        raise AssertionError("unsatisfiable cap accepted")
    except twamm.ImpactCapError:
        pass


def test_schedule_gate_rejections():
    sched = SliceScheduler()
    # below the $75 floor
    try:
        sched.schedule(twamm.MIN_CAPITAL_CENTS - 1, 10**9, 10**9, 0)
        raise AssertionError("sub-floor parent accepted")
    except ValueError:
        pass
    # below the minimum slice count
    for req in (1, 2, 3):
        try:
            sched.schedule(twamm.MIN_CAPITAL_CENTS, 10**9, 10**9, 0,
                           requested_slices=req)
            raise AssertionError(f"{req} slices accepted")
        except twamm.SliceCountError:
            pass
    # non-positive reserves
    for bad in (0, -10):
        try:
            sched.schedule(twamm.MIN_CAPITAL_CENTS, bad, 10**9, 0)
            raise AssertionError("bad reserve accepted")
        except ValueError:
            pass
    # constructor guard
    try:
        SliceScheduler(min_slices=1)
        raise AssertionError("min_slices=1 accepted")
    except ValueError:
        pass


def test_slice_at_lookahead():
    sched = SliceScheduler()
    for _ in range(100):
        parent = RNG.randint(twamm.MIN_CAPITAL_CENTS, 10**9)
        start = RNG.randint(0, 10**5)
        s = sched.schedule(parent, 10**12, 10**12, start)
        for i, sl in enumerate(s.slices_cents):
            assert s.slice_at(start + i) == sl
        for bad_block in (start - 1, start - 100, start + s.n_slices):
            try:
                s.slice_at(bad_block)
                raise AssertionError("out-of-window slice served")
            except IndexError:
                pass


def test_live_submission_blocked():
    sched = SliceScheduler()
    s = sched.schedule(10**8, 10**12, 10**12, 0)
    blocker = LiveOrderBlocker()
    for _ in range(20):
        try:
            blocker.submit(s, RNG.randint(0, 100))
            raise AssertionError("venue submission escaped")
        except twamm.LiveBlockedError:
            pass
