# PACK-2 added coverage: position open/close lifecycle and range/tick
# math paths that the platform tests did not exercise end-to-end.
# The core module is a dry-run reference build: "opening" a position
# produces intents only, live opening is hard-blocked, and "closing" is
# modelled as liquidity-remove events plus pull-based proceeds claims.
from __future__ import annotations

import sys
from pathlib import Path

PKG_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PKG_SRC) not in sys.path:
    sys.path.insert(0, str(PKG_SRC))

import pytest

from p22_clmm_manager.clmm_manager import (
    JITDetector,
    LiquidityEvent,
    LiveLPBlockedError,
    ManagedPosition,
    PassiveLP,
    PoolConfig,
    ProceedsDistributor,
    VolRangeAgent,
    commitment_id,
)


@pytest.fixture()
def config():
    return PoolConfig(pool_id="pool-1")


def test_open_via_rerange_intent_is_dry_run_only(config):
    """Opening flow: a managed position is created and a re-range intent
    proposes its range; the intent never executes anything."""
    agent = VolRangeAgent(config, base_width_ticks=120)
    pos = ManagedPosition("pos-1", "pool-1", -60, 60, 10_000)
    intent = agent.emit_rerange_intent(pos, [100.0] * 10, mid_tick=0)
    assert intent.action == "rerange"
    assert intent.dry_run is True
    assert intent.executed is False
    width = intent.details["width_ticks"]
    assert intent.details["tick_upper"] - intent.details["tick_lower"] == width
    assert intent.details["tick_lower"] % config.tick_spacing == 0


def test_live_open_is_blocked(config):
    """The live open path stays hard-blocked behind the auditor gate."""
    agent = VolRangeAgent(config)
    with pytest.raises(LiveLPBlockedError):
        agent.open_live_position("pos-1", 10_000)


def test_close_via_remove_events_and_claim(config):
    """Closing flow: full same-block remove around a swap is detected,
    captured proceeds are distributed, and the LP closes out by pulling
    its claim exactly once."""
    detector = JITDetector(config)
    events = [
        LiquidityEvent("pool-1", "j1", "jit-bot", 10, "add", 2_000, 0, 120),
        LiquidityEvent("pool-1", "j1", "jit-bot", 10, "swap", 0, 0, 120),
        LiquidityEvent("pool-1", "j1", "jit-bot", 10, "remove", 2_000, 0, 120),
    ]
    flags = detector.detect(events)
    assert len(flags) == 1
    assert flags[0].captured_value_wei == 2_000

    d = ProceedsDistributor()
    d.record_capture("cap-1", flags[0].captured_value_wei)
    out = d.distribute("cap-1", [PassiveLP("lp-1", 100, True)])
    assert out["distributed_wei"] + out["treasury_wei"] == 2_000
    assert d.claim("cap-1", "lp-1") == out["shares"]["lp-1"]
    assert d.claim("cap-1", "lp-1") == 0  # position fully closed out


def test_range_width_snaps_and_scales_with_vol(config):
    """Range/tick math: width is tick-spacing snapped, at least one
    spacing wide, and non-decreasing as realized vol rises."""
    agent = VolRangeAgent(config, base_width_ticks=120)
    flat = agent.range_width_ticks([100.0] * 20)
    mild = agent.range_width_ticks([100.0, 101.0, 100.0, 101.0, 100.0] * 4)
    for w in (flat, mild):
        assert w % config.tick_spacing == 0
        assert w >= config.tick_spacing
        assert w <= config.vol_band_ticks
    assert mild >= flat


def test_realized_vol_edge_cases(config):
    agent = VolRangeAgent(config)
    assert agent.realized_vol([]) == 0.0
    assert agent.realized_vol([100.0]) == 0.0
    assert agent.realized_vol([100.0, 100.0, 100.0]) == 0.0
    assert agent.realized_vol([100.0, 110.0, 99.0, 108.9]) > 0.0


def test_commitment_id_stable_and_sensitive():
    assert commitment_id("pool-1", "pos-1") == commitment_id("pool-1", "pos-1")
    assert commitment_id("pool-1", "pos-1") != commitment_id("pool-1", "pos-2")
    assert len(commitment_id("a")) == 16
