"""Unit tests for the P02 Concentrated Liquidity Manager reference build.

Covers src/sincor2/defi/clmm_manager.py — JIT detection, tick-shift
response, transient fee, proceeds distribution, vol range agent, and
access control behind SKU SINCOR-DEFI-P02-CLMM. Pure logic, no chain.

Each test cites the rule it guards (auction task p02-clmm-unit-tests).
Money math is asserted to the wei. 24/24 must pass.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from src.sincor2.defi.clmm_manager import (
    TREASURY,
    AccessControl,
    GUARDIAN_ROLE,
    JITDetector,
    JITFlag,
    LiquidityEvent,
    LiveLPBlockedError,
    ManagedPosition,
    MANAGER_ROLE,
    PassiveLP,
    PoolConfig,
    ProceedsDistributor,
    TickShiftResponder,
    TransientFee,
    UnauthorizedError,
    VolRangeAgent,
)


@pytest.fixture()
def config():
    return PoolConfig(pool_id="pool-1")


@pytest.fixture()
def detector(config):
    return JITDetector(config)


def ev(provider, pos, block, kind, delta=0, lo=0, hi=120):
    return LiquidityEvent("pool-1", pos, provider, block, kind, delta, lo, hi)


# -- JIT detection -----------------------------------------------------------
def test_true_jit_flagged(detector):
    """Rule p02-clmm-jit-detection: add+full-remove in one block around a swap is flagged."""
    events = [
        ev("jit-bot", "j1", 10, "add", 1_000),
        ev("jit-bot", "j1", 10, "swap"),
        ev("jit-bot", "j1", 10, "remove", 1_000),
    ]
    flags = detector.detect(events)
    assert len(flags) == 1
    assert flags[0].provider == "jit-bot"
    assert flags[0].block_number == 10
    assert flags[0].confidence == pytest.approx(1.0)


def test_two_block_rebalance_not_flagged(detector):
    """Rule p02-clmm-jit-detection: a two-block add/remove is legitimate rebalancing."""
    events = [
        ev("lp-honest", "h1", 10, "add", 1_000),
        ev("lp-honest", "h1", 10, "swap"),
        ev("lp-honest", "h1", 11, "remove", 1_000),
    ]
    assert detector.detect(events) == []


def test_split_attack_across_positions_flagged(detector):
    """Rule p02-clmm-jit-detection: multi-position splitting is aggregated per provider."""
    events = [
        ev("jit-bot", "a", 10, "add", 500),
        ev("jit-bot", "b", 10, "add", 500),
        ev("jit-bot", "a", 10, "swap"),
        ev("jit-bot", "a", 10, "remove", 500),
        ev("jit-bot", "b", 10, "remove", 500),
    ]
    flags = detector.detect(events)
    assert len(flags) == 1
    assert flags[0].added_wei == 1_000
    assert flags[0].removed_wei == 1_000


def test_honest_add_and_hold_not_flagged(detector):
    """Rule p02-clmm-jit-detection: add without same-block removal is never flagged."""
    events = [
        ev("lp-honest", "h1", 10, "add", 2_000),
        ev("lp-honest", "h1", 10, "swap"),
        ev("lp-honest", "h1", 11, "swap"),
    ]
    assert detector.detect(events) == []


def test_partial_removal_below_sensitivity_not_flagged(detector):
    """Rule p02-clmm-jit-detection: sensitivity avoids false positives on partial exits."""
    events = [
        ev("lp", "p1", 10, "add", 1_000),
        ev("lp", "p1", 10, "swap"),
        ev("lp", "p1", 10, "remove", 500),  # 50% < 95% sensitivity
    ]
    assert detector.detect(events) == []


# -- tick-shift response -----------------------------------------------------
def _flag():
    return JITFlag("pos-m", "jit-bot", "pool-1", 10, 1_000, 1_000, 1_000, 1.0)


def _managed():
    return ManagedPosition("pos-m", "pool-1", 0, 120, 5_000)


def test_shift_moves_off_attack_range(config):
    """Rule p02-clmm-dynamic-tick-shift: position migrates off the JIT range by tick_distance."""
    responder = TickShiftResponder(config)
    pos, intent = responder.respond(_flag(), _managed(), 100, -120, -60)
    assert intent.action == "shift_ticks"
    assert (pos.tick_lower, pos.tick_upper) == (60, 180)  # shifted up, away from attack
    assert intent.details["shift_ticks"] == config.tick_shift_step
    assert pos.cumulative_shift_ticks == config.tick_shift_step


def test_shift_bounded_by_vol_band():
    """Rule p02-clmm-dynamic-tick-shift: a single shift never exceeds vol_band."""
    cfg = PoolConfig(pool_id="pool-1", tick_shift_step=200, vol_band_ticks=120)
    responder = TickShiftResponder(cfg)
    pos, intent = responder.respond(_flag(), _managed(), 100, -120, -60)
    assert intent.details["shift_ticks"] == 120
    assert pos.cumulative_shift_ticks == 120


def test_cooldown_prevents_ping_pong(config):
    """Rule p02-clmm-dynamic-tick-shift: cooldown blocks oscillation across attacks."""
    responder = TickShiftResponder(config)
    pos = _managed()
    pos, first = responder.respond(_flag(), pos, 100, -120, -60)
    assert first.details.get("skipped") is None
    before = (pos.tick_lower, pos.tick_upper)
    pos, second = responder.respond(_flag(), pos, 100 + 3, -120, -60)
    assert second.details["skipped"] == "cooldown"
    assert (pos.tick_lower, pos.tick_upper) == before  # unchanged


def test_max_shift_guard_caps_cumulative(config):
    """Rule p02-clmm-dynamic-tick-shift: cumulative shift is capped by max_shift_ticks."""
    responder = TickShiftResponder(config)
    pos = _managed()
    pos.cumulative_shift_ticks = config.max_shift_ticks
    pos, intent = responder.respond(_flag(), pos, 500, -120, -60)
    assert intent.details["skipped"] == "max_shift_guard"
    assert (pos.tick_lower, pos.tick_upper) == (0, 120)


def test_shift_intent_is_dry_run(config):
    """Rule dry_run_default: every response is an intent; nothing executes."""
    responder = TickShiftResponder(config)
    _, intent = responder.respond(_flag(), _managed(), 100, -120, -60)
    assert intent.dry_run is True
    assert intent.executed is False


# -- transient fee -----------------------------------------------------------
def test_flagged_provider_pays_surcharge():
    """Rule p02-clmm-transient-fee: flagged JIT provider pays; honest LP fee unchanged."""
    fee = TransientFee()
    flag = JITFlag("j1", "jit-bot", "pool-1", 10, 5_000, 5_000, 5 * 10**15, 1.0)
    assert fee.effective_fee_bps(30, "jit-bot", [flag]) == 35  # 30 + 5 surcharge
    assert fee.effective_fee_bps(30, "honest-lp", [flag]) == 30


def test_surcharge_capped():
    """Rule p02-clmm-transient-fee: surcharge is capped so fees stay sane."""
    fee = TransientFee(cap_bps=100)
    flag = JITFlag("j1", "jit-bot", "pool-1", 10, 10**18, 10**18, 10**18, 1.0)
    assert fee.surcharge_bps(flag) == 100
    assert fee.effective_fee_bps(30, "jit-bot", [flag]) == 130


def test_fee_math_failure_degrades_to_zero():
    """Rule p02-clmm-transient-fee: fee logic can never brick the swap (non-bricking)."""
    fee = TransientFee()
    flag = JITFlag("j1", "jit-bot", "pool-1", 10, 0, 0, "not-a-number", 1.0)
    assert fee.surcharge_bps(flag) == 0  # degraded, not raised


# -- proceeds distribution ---------------------------------------------------
def _lps():
    return [
        PassiveLP(f"lp{i}", ls, True)
        for i, ls in enumerate([100, 200, 300, 400, 500])
    ]


def test_pro_rata_distribution_to_the_wei():
    """Rule p02-clmm-proceeds-distribution: 5-LP pro-rata shares match to the wei."""
    d = ProceedsDistributor()
    d.record_capture("c1", 1_000_000)
    out = d.distribute("c1", _lps())
    assert out["shares"] == {
        "lp0": 66566,
        "lp1": 133133,
        "lp2": 199700,
        "lp3": 266266,
        "lp4": 332833,
    }


def test_treasury_cut_exact_15bps():
    """Rule p02-clmm-proceeds-distribution: treasury cut is exactly 15 bps."""
    d = ProceedsDistributor()
    d.record_capture("c1", 10_000)
    out = d.distribute("c1", [PassiveLP("lp0", 1, True)])
    assert out["treasury_wei"] == 15
    assert out["treasury"] == TREASURY


def test_conservation_holds_exactly():
    """Invariant: distributed + treasury == proceeds, to the wei."""
    d = ProceedsDistributor()
    d.record_capture("c1", 7_777_777)
    out = d.distribute("c1", _lps())
    assert out["distributed_wei"] + out["treasury_wei"] == 7_777_777


def test_pull_claim_and_no_double_claim():
    """Rule p02-clmm-proceeds-distribution: pull claims; double claim pays 0."""
    d = ProceedsDistributor()
    d.record_capture("c1", 10_000)
    d.distribute("c1", [PassiveLP("lp0", 1, True)])
    assert d.claim("c1", "lp0") == 9985
    assert d.claim("c1", "lp0") == 0  # already claimed


def test_non_holder_excluded():
    """Rule p02-clmm-proceeds-distribution: LPs not holding through the attack earn nothing."""
    d = ProceedsDistributor()
    d.record_capture("c1", 10_000)
    out = d.distribute("c1", [PassiveLP("tourist", 999, False)])
    assert out["shares"] == {}
    assert out["eligible_lps"] == 0
    assert d.claim("c1", "tourist") == 0


# -- vol range agent ---------------------------------------------------------
def test_flat_prices_give_base_width(config):
    """Rule p02-clmm-vol-range-agent: zero realized vol reproduces the base width."""
    agent = VolRangeAgent(config, base_width_ticks=120)
    width = agent.range_width_ticks([100.0] * 20)
    assert width == 120


def test_width_clamped_to_vol_band(config):
    """Rule p02-clmm-vol-range-agent: width never exceeds vol_band under extreme vol."""
    agent = VolRangeAgent(config, base_width_ticks=120)
    wild = [100.0 if i % 2 == 0 else 200.0 for i in range(20)]
    width = agent.range_width_ticks(wild)
    assert width == config.vol_band_ticks
    assert width % config.tick_spacing == 0


def test_live_lp_blocked_without_auditor_gate(config):
    """Rule dry_run_default: live LP is hard-blocked until the auditor gate flips."""
    agent = VolRangeAgent(config)
    with pytest.raises(LiveLPBlockedError):
        agent.open_live_position("pos-m", 1_000)


# -- access control & edge cases ---------------------------------------------
def test_unauthorized_manager_call_reverts():
    """Rule p02-clmm-access-edge-cases: unauthorized manager call raises."""
    ac = AccessControl()
    ac.grant(MANAGER_ROLE, "agent-1")
    with pytest.raises(UnauthorizedError):
        ac.require(MANAGER_ROLE, "stranger")


def test_pause_stops_defense_not_user_exits():
    """Rule p02-clmm-access-edge-cases: pause never blocks withdrawals or claims."""
    ac = AccessControl()
    ac.grant(GUARDIAN_ROLE, "guardian-1")
    ac.pause_defense("guardian-1")
    assert ac.defense_active() is False
    assert AccessControl.user_exit_allowed(paused=True) is True
    ac.unpause_defense("guardian-1")
    assert ac.defense_active() is True


def test_empty_pool_no_events(detector):
    """Rule p02-clmm-access-edge-cases: empty pool (no events) is a safe no-op."""
    assert detector.detect([]) == []
