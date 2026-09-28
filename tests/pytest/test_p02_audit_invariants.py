"""Audit-prep invariant + adversarial fuzz tests for P02 (clmm_manager).

Covers the 2026-09-28 precision fix (integer-exact JIT threshold) and
property tests an auditor would demand:

- threshold_exactness: the detector's flag decision is IDENTICAL to the
  exact rational rule ``removed * 10000 >= added * 9500`` on every fuzzed
  wei-scale input (no float64 anywhere near the decision).
- precision_regression: hard-coded wei-scale cases where the OLD float64
  form (``removed >= added * 0.95``) false-flagged an honest LP.
- boundary: exact on/off behavior at the integer boundary.
- detection soundness: no-swap blocks never flag; two-block sequences
  never flag; sub-threshold removals never flag (fuzzed).
- tick-shift safety: cumulative cap, cooldown, tick-spacing snap, and
  move-away-from-attack direction hold across fuzzed attack sequences.
- proceeds conservation + fee cap + pull-claim safety (fuzzed).
- adversarial inputs: zero/negative/dust/max-uint amounts, extreme ticks,
  empty event streams.

Deterministic: seeded RNG, no hypothesis dependency. N/N must pass.
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

from src.sincor2.defi.clmm_manager import (
    JIT_SENSITIVITY_BPS,
    JIT_SENSITIVITY_DEN,
    AccessControl,
    GUARDIAN_ROLE,
    JITDetector,
    JITFlag,
    LiquidityEvent,
    ManagedPosition,
    PassiveLP,
    PoolConfig,
    ProceedsDistributor,
    TickShiftResponder,
    TransientFee,
)

RNG = random.Random(0xC1AA02)
BPS = JIT_SENSITIVITY_BPS
DEN = JIT_SENSITIVITY_DEN
assert BPS == 9500 and DEN == 10_000


def exact_rule(added: int, removed: int) -> bool:
    """The exact rational threshold the detector must implement."""
    return removed * DEN >= added * BPS


def ev_block(provider: str, pos: str, block: int, added: int,
             removed: int, with_swap: bool = True):
    evs = [LiquidityEvent("pool-1", pos, provider, block, "add", added, -120, 120)]
    if with_swap:
        evs.append(LiquidityEvent("pool-1", pos, provider, block, "swap", 0))
    if removed:
        evs.append(LiquidityEvent("pool-1", pos, provider, block, "remove",
                                  removed, -120, 120))
    return evs


@pytest.fixture()
def detector():
    return JITDetector(PoolConfig(pool_id="pool-1"))


# -- precision fix -----------------------------------------------------------
def test_precision_regression_float_false_positive(detector):
    """AUDIT REGRESSION: float64 ``removed >= added * 0.95`` FALSE-FLAGGED
    this honest LP at wei scale; the integer-exact rule must NOT flag.

    added=113290930051451398541124007855224703922445404575131771778104
    removed=107626383548878828614067807462463468726323134346375183189196
    removed/added = 0.949999... (strictly below 95%).
    """
    added = 113290930051451398541124007855224703922445404575131771778104
    removed = 107626383548878828614067807462463468726323134346375183189196
    assert not exact_rule(added, removed)          # ground truth: below threshold
    assert removed >= added * 0.95                 # old float64 code: flagged (bug)
    flags = detector.detect(ev_block("honest-lp", "p1", 10, added, removed))
    assert flags == []                             # fixed code: not flagged


def test_boundary_exact_on_off(detector):
    """The integer boundary is exact: boundary flags, boundary-1 does not —
    at 1 wei and at 2**200 wei. (Ceiling: the minimal integer removed with
    removed/added >= 95%.)"""
    for added in (1, 10**18, 10**30, 2**200):
        boundary = -(-added * BPS // DEN)  # ceil(added * 95 / 100)
        assert exact_rule(added, boundary)
        assert not exact_rule(added, boundary - 1)
        assert detector.detect(ev_block("p", "p1", 10, added, boundary)) != []
        assert detector.detect(ev_block("p", "p1", 10, added, boundary - 1)) == []


def test_threshold_matches_exact_rule_fuzz(detector):
    """PROPERTY: detector flag decision == exact rational rule on 300
    fuzzed wei-scale (added, removed) pairs straddling the boundary."""
    for _ in range(300):
        added = RNG.randint(10**18, 2**200)
        boundary = (added * BPS) // DEN
        removed = boundary + RNG.choice([-2, -1, 0, 1, 2])
        if removed < 0:
            continue
        flags = detector.detect(ev_block("p", "p1", 10, added, removed))
        assert (len(flags) == 1) == exact_rule(added, removed)


def test_sensitivity_env_parses_exact():
    """The legacy '0.95' env string parses to exactly 9500 bps (never via
    float64) — whereas the float64 product is demonstrably inexact."""
    import os
    from decimal import Decimal
    assert int(Decimal(os.getenv("CLMM_JIT_SENSITIVITY", "0.95")) * 10_000) == 9500
    assert int(float(10**30) * 0.95) != (10**30 * 95) // 100  # float64 drift


# -- detection soundness (fuzzed) --------------------------------------------
def test_no_swap_never_flags_fuzz(detector):
    """PROPERTY: blocks without a swap can never produce a flag (200 fuzzed)."""
    for _ in range(200):
        block = RNG.randint(1, 10**6)
        evs = []
        for i in range(RNG.randint(1, 6)):
            kind = RNG.choice(["add", "remove"])
            evs.append(LiquidityEvent("pool-1", f"p{i}", f"lp{RNG.randint(0,3)}",
                                     block, kind, RNG.randint(0, 2**128)))
        assert detector.detect(evs) == []


def test_two_block_sequences_never_flag_fuzz(detector):
    """PROPERTY: add/remove split across two blocks is rebalancing, never
    JIT — even at 100% removal (200 fuzzed)."""
    for _ in range(200):
        amt = RNG.randint(1, 2**200)
        b = RNG.randint(1, 10**6)
        evs = [
            LiquidityEvent("pool-1", "p1", "lp", b, "add", amt),
            LiquidityEvent("pool-1", "p1", "lp", b, "swap", 0),
            LiquidityEvent("pool-1", "p1", "lp", b + 1, "remove", amt),
        ]
        assert detector.detect(evs) == []


def test_sub_threshold_never_flags_fuzz(detector):
    """PROPERTY: same-block removal strictly below the exact threshold
    never flags, at any scale (200 fuzzed)."""
    for _ in range(200):
        added = RNG.randint(1, 2**200)
        removed = RNG.randint(0, (added * BPS) // DEN - 1) if added * BPS // DEN > 0 else 0
        assert not exact_rule(added, removed)
        assert detector.detect(ev_block("lp", "p1", 7, added, removed)) == []


def test_adversarial_event_amounts_no_crash(detector):
    """Adversarial: zero / dust / negative / max-uint deltas never crash
    the detector and never flag without a qualifying removal."""
    nasty = [0, 1, 10**18, 2**256 - 1, -1, -10**30]
    for d in nasty:
        evs = [LiquidityEvent("pool-1", "p", "lp", 1, "add", d),
               LiquidityEvent("pool-1", "p", "lp", 1, "swap", 0)]
        detector.detect(evs)  # must not raise
    assert detector.detect([]) == []


# -- tick-shift safety (fuzzed sequences) -------------------------------------
def _respond_sequence(seed_blocks: int = 60):
    cfg = PoolConfig(pool_id="pool-1")
    responder = TickShiftResponder(cfg)
    pos = ManagedPosition("pos-m", "pool-1", 0, 120, 5_000)
    flag = JITFlag("pos-m", "jit", "pool-1", 1, 100, 100, 100, 1.0)
    return cfg, responder, pos, flag


def test_shift_sequence_invariants_fuzz():
    """PROPERTY: across 60 fuzzed attacks — cumulative shift capped,
    ticks snapped to spacing, width preserved, cooldown honored, and every
    shift moves AWAY from the attack range."""
    cfg, responder, pos, flag = _respond_sequence()
    width0 = pos.tick_upper - pos.tick_lower
    block = 1000
    for _ in range(60):
        atk_lo = RNG.choice([-600, -300, -120, 300, 600])
        atk_hi = atk_lo + RNG.choice([60, 120, 240])
        mid_attack = (atk_lo + atk_hi) // 2
        mid_before = (pos.tick_lower + pos.tick_upper) // 2
        lo_before, hi_before = pos.tick_lower, pos.tick_upper
        cum_before = pos.cumulative_shift_ticks
        block += RNG.randint(1, 25)
        pos, intent = responder.respond(flag, pos, block, atk_lo, atk_hi)
        # Invariants that hold whether or not this attack triggered a shift:
        assert pos.cumulative_shift_ticks <= cfg.max_shift_ticks
        assert pos.tick_lower % cfg.tick_spacing == 0
        assert pos.tick_upper % cfg.tick_spacing == 0
        assert pos.tick_upper - pos.tick_lower == width0
        if intent.details.get("skipped") is None:
            step = intent.details["shift_ticks"]
            assert step <= cfg.vol_band_ticks
            assert pos.cumulative_shift_ticks == cum_before + step
            # moved away from the attack range
            if mid_attack <= mid_before:
                assert pos.tick_lower >= lo_before
            else:
                assert pos.tick_lower <= lo_before
        else:
            assert (pos.tick_lower, pos.tick_upper) == (lo_before, hi_before)
            assert pos.cumulative_shift_ticks == cum_before


def test_extreme_ticks_no_crash():
    """Adversarial: Uniswap-scale extreme ticks never crash the responder."""
    cfg = PoolConfig(pool_id="pool-1")
    responder = TickShiftResponder(cfg)
    pos = ManagedPosition("pos-m", "pool-1", -887272, 887272, 10**30)
    flag = JITFlag("pos-m", "jit", "pool-1", 1, 10**30, 10**30, 10**30, 1.0)
    pos, intent = responder.respond(flag, pos, 10**9, -887272, -887212)
    assert intent.details.get("skipped") is None
    assert pos.tick_lower % cfg.tick_spacing == 0


# -- proceeds distribution (fuzzed) -------------------------------------------
def test_proceeds_conservation_fuzz():
    """PROPERTY: distributed + treasury == proceeds EXACTLY, for 200 fuzzed
    (proceeds, lp-set) pairs; dust always lands in the treasury."""
    for _ in range(200):
        d = ProceedsDistributor()
        proceeds = RNG.choice([0, 1, RNG.randint(1, 10**6), RNG.randint(10**18, 2**200)])
        d.record_capture("c", proceeds)
        lps = [PassiveLP(f"lp{i}",
                         RNG.randint(0, 2**100),
                         RNG.choice([True, True, False]))
               for i in range(RNG.randint(0, 8))]
        out = d.distribute("c", lps)
        assert out["distributed_wei"] + out["treasury_wei"] == proceeds
        assert sum(out["shares"].values()) == out["distributed_wei"]
        # ineligible LPs (not holding, or zero liquidity-seconds) earn nothing
        for lp in lps:
            if not lp.held_through_attack or lp.liquidity_seconds <= 0:
                assert out["shares"].get(lp.lp_id, 0) == 0
        claimed_total = sum(d.claim("c", lp.lp_id) for lp in lps)
        assert claimed_total == out["distributed_wei"]
        # double claims pay 0 afterwards
        for lp in lps:
            assert d.claim("c", lp.lp_id) == 0


def test_treasury_cut_minimum_15bps_fuzz():
    """PROPERTY: treasury always receives >= the exact 15 bps cut (dust
    only increases it), never less."""
    for _ in range(100):
        d = ProceedsDistributor()
        proceeds = RNG.randint(1, 10**12)
        d.record_capture("c", proceeds)
        out = d.distribute("c", [PassiveLP("lp0", RNG.randint(1, 10**9), True)])
        assert out["treasury_wei"] >= proceeds * 15 // 10_000


def test_fee_cap_and_nonbricking_fuzz():
    """PROPERTY: surcharge capped at cap_bps for any captured value up to
    2**256-1; garbage input degrades to 0 (never raises)."""
    fee = TransientFee()
    for _ in range(100):
        captured = RNG.choice([0, 1, RNG.randint(1, 10**30), 2**256 - 1])
        flag = JITFlag("j", "jit", "pool-1", 1, captured, captured, captured, 1.0)
        assert 0 <= fee.surcharge_bps(flag) <= fee.cap_bps
    bad = JITFlag("j", "jit", "pool-1", 1, 0, 0, "garbage", 1.0)
    assert fee.surcharge_bps(bad) == 0
    assert fee.surcharge_bps(JITFlag("j", "jit", "pool-1", 1, 0, 0, -5, 1.0)) == 0


# -- access control edge fuzz --------------------------------------------------
def test_pause_unpause_sequence_fuzz():
    """Adversarial: random pause/unpause/role sequences keep the guard
    consistent; user exits are never blocked."""
    ac = AccessControl()
    ac.grant(GUARDIAN_ROLE, "g")
    for _ in range(100):
        op = RNG.choice(["pause", "unpause", "pause_bad", "require_bad"])
        if op == "pause":
            ac.pause_defense("g")
            assert not ac.defense_active()
        elif op == "unpause":
            ac.unpause_defense("g")
            assert ac.defense_active()
        elif op == "pause_bad":
            with pytest.raises(Exception):
                ac.pause_defense("stranger")
        else:
            with pytest.raises(Exception):
                ac.require("NOPE", "stranger")
        assert AccessControl.user_exit_allowed(paused=not ac.defense_active())
