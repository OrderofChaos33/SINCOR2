"""KYA Telemetry — runtime reliability loop tests.

Proves the telemetry half of the KYA extension (recommendation #2)
against a real SQLite store seeded only through the store's own
public ``record`` API:

- recording + rolling-window math (counts, rates, percentiles) are
  hand-computed below and asserted exactly;
- a high-error agent and a high-timeout agent are scaled DOWN;
- a low-evidence agent — even one whose few recorded runs ALL
  failed — returns the explicit ``unknown`` verdict with a NEUTRAL
  multiplier: insufficient evidence never throttles and never
  fabricates a reliability score;
- the multiplier is hard-clamped to [floor, ceiling] and can never
  exceed the ceiling (telemetry scales down or leaves alone, never
  boosts);
- every verdict carries its evidence counts;
- the trust-score integration is additive only: the ``telemetry``
  component appears ONLY when telemetry data exists, and the score,
  status, and tier from ``compute_trust_score`` are never altered.
"""
from __future__ import annotations

import pytest

import sincor2.kya_telemetry as kt
import sincor2.kya_trust_score as kts
from sincor2.kya_telemetry import (
    TelemetryPolicy,
    TelemetryStore,
    compute_reliability,
    evaluate_policy,
)

NOW = 1_700_000_000.0
AGENT = "agent-telemetry"


@pytest.fixture()
def store(tmp_path):
    return TelemetryStore(tmp_path / "telemetry.db", clock=lambda: NOW)


def seed(store, agent, outcomes, *, start=NOW - 3600.0, step=60.0,
         latency_ms=None):
    """Record ``outcomes`` for ``agent`` at increasing timestamps."""
    for i, outcome in enumerate(outcomes):
        store.record(
            agent, outcome, skill_id="skill-x",
            latency_ms=latency_ms, ts=start + i * step)


# ---------------------------------------------------------------------------
# Recording + durability
# ---------------------------------------------------------------------------

def test_record_returns_event_and_persists_across_instances(tmp_path):
    path = tmp_path / "durable.db"
    s1 = TelemetryStore(path, clock=lambda: NOW)
    ev = s1.record(AGENT, "success", skill_id="translate",
                   latency_ms=123.0)
    assert ev["agent_id"] == AGENT
    assert ev["outcome"] == "success"
    assert ev["skill_id"] == "translate"
    assert ev["latency_ms"] == 123.0
    assert ev["ts"] == NOW  # injectable clock used when ts omitted
    assert ev["id"] is not None

    s2 = TelemetryStore(path, clock=lambda: NOW)
    assert s2.count(AGENT) == 1
    rows = s2.events(AGENT)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "success"
    assert rows[0]["skill_id"] == "translate"


def test_record_rejects_invalid_inputs(store):
    with pytest.raises(ValueError):
        store.record("", "success")
    with pytest.raises(ValueError):
        store.record(AGENT, "exploded")
    with pytest.raises(ValueError):
        store.record(AGENT, "success", latency_ms=-1.0)
    assert store.count(AGENT) == 0


def test_record_normalizes_outcome_case(store):
    ev = store.record(AGENT, "  TIMEOUT ")
    assert ev["outcome"] == "timeout"


# ---------------------------------------------------------------------------
# Rolling-window math (hand-computed)
# ---------------------------------------------------------------------------

def test_window_math_counts_rates_and_percentiles(store):
    # 20 in-window events: 10 success / 6 error / 4 timeout,
    # latencies 10,20,...,100 ms on the first ten.
    seed(store, AGENT, ["success"] * 10, latency_ms=None)
    for i in range(10):
        store.record(AGENT, "success", latency_ms=float(10 * (i + 1)),
                     ts=NOW - 3000.0 + i)
    # Replace the plain successes' outcomes distribution: record the
    # error/timeout events explicitly.
    seed(store, AGENT, ["error"] * 6, start=NOW - 1800.0)
    seed(store, AGENT, ["timeout"] * 4, start=NOW - 900.0)
    # Out-of-window noise that must NOT count: too old, and future.
    store.record(AGENT, "error", ts=NOW - 90000.0)
    store.record(AGENT, "error", ts=NOW + 500.0)

    stats = store.reliability(AGENT, now=NOW)
    # 10 seeded successes + 10 latency successes = 20 success,
    # plus 6 error + 4 timeout = 30 attempts in window.
    assert stats["attempts"] == 30
    assert stats["successes"] == 20
    assert stats["errors"] == 6
    assert stats["timeouts"] == 4
    assert stats["error_rate"] == pytest.approx(6 / 30)
    assert stats["timeout_rate"] == pytest.approx(4 / 30)
    assert stats["failure_rate"] == pytest.approx(10 / 30)
    assert stats["latency_samples"] == 10
    # Nearest-rank on [10..100]: p50 -> 5th value, p95 -> 10th value.
    assert stats["p50_latency_ms"] == 50.0
    assert stats["p95_latency_ms"] == 100.0
    assert stats["window_start"] == NOW - 86400.0
    assert stats["window_end"] == NOW


def test_compute_reliability_is_pure_and_windowed():
    events = [
        {"outcome": "success", "latency_ms": 5.0, "ts": NOW - 10.0},
        {"outcome": "error", "latency_ms": None, "ts": NOW - 20.0},
        {"outcome": "timeout", "latency_ms": None, "ts": NOW - 999999.0},
    ]
    stats = compute_reliability(events, now=NOW)
    assert stats["attempts"] == 2
    assert stats["errors"] == 1
    assert stats["timeouts"] == 0
    assert stats["p50_latency_ms"] == 5.0


# ---------------------------------------------------------------------------
# Throttle verdicts
# ---------------------------------------------------------------------------

def test_high_error_agent_is_scaled_to_floor(store):
    seed(store, AGENT, ["error"] * 20)
    v = store.verdict(AGENT, now=NOW)
    assert v["status"] == "evaluated"
    assert v["allocation_multiplier"] == 0.1  # policy floor
    assert v["rate_limit_tier"] == "minimal"
    assert v["throttled"] is True
    assert v["evidence"]["attempts"] == 20
    assert v["evidence"]["errors"] == 20
    assert v["evidence"]["error_rate"] == 1.0


def test_high_timeout_agent_is_scaled_to_floor(store):
    seed(store, AGENT, ["timeout"] * 20)
    v = store.verdict(AGENT, now=NOW)
    assert v["status"] == "evaluated"
    assert v["allocation_multiplier"] == 0.1
    assert v["throttled"] is True
    assert v["evidence"]["timeouts"] == 20
    assert v["evidence"]["timeout_rate"] == 1.0


def test_partial_failures_scale_partially(store):
    # 20 attempts, 5 errors -> error_rate 0.25, pressure 0.25/0.5=0.5
    # -> multiplier 1.0 - 0.5 * (1.0 - 0.1) = 0.55.
    seed(store, AGENT, ["success"] * 15 + ["error"] * 5)
    v = store.verdict(AGENT, now=NOW)
    assert v["status"] == "evaluated"
    assert v["allocation_multiplier"] == pytest.approx(0.55)
    assert v["rate_limit_tier"] == "restricted"
    assert v["throttled"] is True


def test_timeout_pressure_uses_its_own_saturation(store):
    # 20 attempts, 3 timeouts -> timeout_rate 0.15, pressure
    # 0.15/0.30 = 0.5 -> multiplier 0.55 (same as error path).
    seed(store, AGENT, ["success"] * 17 + ["timeout"] * 3)
    v = store.verdict(AGENT, now=NOW)
    assert v["allocation_multiplier"] == pytest.approx(0.55)
    assert v["evidence"]["timeout_rate"] == pytest.approx(0.15)


def test_perfect_agent_is_never_boosted_above_ceiling(store):
    seed(store, AGENT, ["success"] * 40)
    v = store.verdict(AGENT, now=NOW)
    assert v["status"] == "evaluated"
    assert v["allocation_multiplier"] == 1.0  # ceiling, never above
    assert v["rate_limit_tier"] == "standard"
    assert v["throttled"] is False


def test_low_evidence_all_failures_is_unknown_and_neutral(store):
    # 19 straight errors: catastrophic rate, but below the 20-attempt
    # evidence bar — verdict must be unknown with NO throttle.
    seed(store, AGENT, ["error"] * 19)
    v = store.verdict(AGENT, now=NOW)
    assert v["status"] == "unknown"
    assert v["allocation_multiplier"] == 1.0
    assert v["rate_limit_tier"] == "standard"
    assert v["throttled"] is False
    assert v["evidence"]["attempts"] == 19
    assert v["evidence"]["errors"] == 19


def test_no_data_is_unknown_and_neutral(store):
    v = store.verdict("agent-never-seen", now=NOW)
    assert v["status"] == "unknown"
    assert v["allocation_multiplier"] == 1.0
    assert v["throttled"] is False
    assert v["evidence"]["attempts"] == 0


def test_verdict_always_carries_evidence_and_policy(store):
    seed(store, AGENT, ["success"] * 25)
    v = store.verdict(AGENT, now=NOW)
    for key in ("attempts", "successes", "errors", "timeouts",
                "error_rate", "timeout_rate", "window_seconds",
                "min_attempts"):
        assert key in v["evidence"]
    assert v["policy"]["floor"] == 0.1
    assert v["policy"]["ceiling"] == 1.0
    assert v["source"] == kt.SOURCE_LABEL


def test_custom_policy_floor_ceiling_and_min_attempts(store):
    policy = TelemetryPolicy(
        min_attempts=5, floor=0.25, ceiling=0.8)
    seed(store, AGENT, ["error"] * 5)
    v = store.verdict(AGENT, policy=policy, now=NOW)
    assert v["status"] == "evaluated"
    assert v["allocation_multiplier"] == 0.25  # custom floor

    thin = store.verdict("agent-thin", policy=policy, now=NOW)
    assert thin["status"] == "unknown"
    # Neutral under a custom policy means the policy ceiling.
    assert thin["allocation_multiplier"] == 0.8


def test_multiplier_bounds_hold_across_mixes(store):
    policy = TelemetryPolicy()
    mixes = [
        ["success"] * 20,
        ["error"] * 20,
        ["timeout"] * 20,
        ["success", "error", "timeout"] * 10,
        ["success"] * 19 + ["error"],
    ]
    for i, mix in enumerate(mixes):
        agent = "agent-mix-%d" % i
        seed(store, agent, mix)
        v = store.verdict(agent, policy=policy, now=NOW)
        assert policy.floor <= v["allocation_multiplier"] <= policy.ceiling


def test_evaluate_policy_directly_on_stats():
    stats = {
        "attempts": 100, "successes": 50, "errors": 50, "timeouts": 0,
        "error_rate": 0.5, "timeout_rate": 0.0, "failure_rate": 0.5,
        "latency_samples": 0, "p50_latency_ms": None,
        "p95_latency_ms": None,
    }
    v = evaluate_policy(stats)
    # error_rate 0.5 == error_rate_full -> pressure 1 -> floor.
    assert v["allocation_multiplier"] == 0.1


def test_policy_validation():
    with pytest.raises(ValueError):
        TelemetryPolicy(floor=0.5, ceiling=0.4)
    with pytest.raises(ValueError):
        TelemetryPolicy(ceiling=1.5)
    with pytest.raises(ValueError):
        TelemetryPolicy(min_attempts=0)


# ---------------------------------------------------------------------------
# Trust-score integration (additive, read-only)
# ---------------------------------------------------------------------------

def _bare_score(agent_id=AGENT):
    return kts.compute_trust_score(agent_id=agent_id)


def test_component_absent_when_no_telemetry(store):
    assert kt.telemetry_component(AGENT, store=store, now=NOW) is None
    result = _bare_score()
    enriched = kt.attach_telemetry_component(
        result, store=store, now=NOW)
    assert "telemetry" not in enriched["components"]
    assert enriched["components"] == result["components"]


def test_component_present_with_evidence_when_data_exists(store):
    seed(store, AGENT, ["success"] * 18 + ["error"] * 2)
    comp = kt.telemetry_component(AGENT, store=store, now=NOW)
    assert comp is not None
    assert comp["status"] == "evaluated"
    assert comp["evidence"]["attempts"] == 20
    assert comp["evidence"]["errors"] == 2
    # error_rate 0.1 -> pressure 0.2 -> multiplier 1 - 0.2*0.9 = 0.82.
    assert comp["allocation_multiplier"] == pytest.approx(0.82)


def test_component_present_but_unknown_on_thin_data(store):
    seed(store, AGENT, ["error"] * 3)
    comp = kt.telemetry_component(AGENT, store=store, now=NOW)
    assert comp is not None
    assert comp["status"] == "unknown"
    assert comp["allocation_multiplier"] == 1.0
    assert comp["throttled"] is False


def test_attach_never_alters_score_or_mutates_input(store):
    seed(store, AGENT, ["timeout"] * 20)
    result = _bare_score()
    assert result["status"] == "unknown"  # no standing records
    enriched = kt.attach_telemetry_component(
        result, store=store, now=NOW)
    assert enriched["score"] == result["score"]
    assert enriched["status"] == result["status"]
    assert enriched["tier"] == result["tier"]
    assert enriched["components"]["telemetry"]["status"] == "evaluated"
    assert enriched["components"]["telemetry"][
        "allocation_multiplier"] == 0.1
    # Input untouched.
    assert "telemetry" not in result["components"]
