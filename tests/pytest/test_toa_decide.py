"""Tests for the TOA uncertainty-resolver interface (src/sincor2/toa_decide.py).

These tests exercise the REAL TOA machinery (Nadaraya-Watson forecaster +
Monte Carlo simulator + WFC collapser) through toa_decide — no mocks of the
forecast path.  Determinism comes from decision_id-seeded Monte Carlo
streams, which the tests assert on directly.
"""

import json
import os

import pytest

from sincor2 import toa_decide as td


@pytest.fixture()
def log_path(tmp_path, monkeypatch):
    p = tmp_path / "decisions.jsonl"
    monkeypatch.setenv("TOA_DECISION_LOG", str(p))
    return p


def _opts():
    return [
        {"id": "grow", "label": "ship the growing feature",
         "signals": [0.40, 0.48, 0.55, 0.63, 0.70]},
        {"id": "shrink", "label": "keep the declining task",
         "signals": [0.70, 0.62, 0.55, 0.47, 0.40]},
    ]


def _objectives():
    return {"revenue": 0.5, "risk": 0.3, "compliance": 0.2}


# ---------------------------------------------------------------------------
# Core behaviour
# ---------------------------------------------------------------------------


def test_recommends_clear_winner_through_real_machinery(log_path):
    r = td.toa_decide("t-winner", _opts(), context={"horizon": 4},
                      objectives=_objectives())
    assert r["action"] == "recommend"
    assert r["recommended"] == "grow"
    assert r["confidence"] >= 0.55
    # every option really went through the pipeline
    for oid in ("grow", "shrink"):
        d = r["forecast_detail"][oid]
        assert d["forecast_paths"] > 0
        assert d["evaluated_paths"] > 0
        assert d["plan_size"] > 0
    assert r["scores"]["grow"] > r["scores"]["shrink"]
    assert "rationale" in r and r["rationale"]


def test_deterministic_replay_same_decision_id(log_path):
    kwargs = dict(context={"horizon": 4}, objectives=_objectives())
    r1 = td.toa_decide("t-determinism", _opts(), **kwargs)
    r2 = td.toa_decide("t-determinism", _opts(), **kwargs)
    assert r1["recommended"] == r2["recommended"]
    assert r1["confidence"] == r2["confidence"]
    assert r1["scores"] == r2["scores"]
    assert r1["action"] == r2["action"]


def test_close_call_escalates(log_path):
    r = td.toa_decide("t-close", [
        {"id": "a", "label": "option A", "signals": [0.50, 0.52, 0.51, 0.53, 0.52]},
        {"id": "b", "label": "option B", "signals": [0.51, 0.50, 0.52, 0.51, 0.53]},
    ], context={"horizon": 4}, objectives=_objectives())
    assert r["action"] == "escalate"
    assert r["recommended"] is None
    assert r["confidence"] < r["threshold"]


def test_env_threshold_override(log_path, monkeypatch):
    # impossibly high threshold -> even a clear winner escalates
    monkeypatch.setenv("TOA_MIN_CONFIDENCE", "0.9999")
    r = td.toa_decide("t-thresh-high", _opts(), context={"horizon": 4},
                      objectives=_objectives())
    assert r["action"] == "escalate"
    assert r["threshold"] == pytest.approx(0.9999)
    # zero threshold -> close call recommends
    monkeypatch.setenv("TOA_MIN_CONFIDENCE", "0.0")
    r2 = td.toa_decide("t-thresh-low", [
        {"id": "a", "label": "A", "signals": [0.50, 0.52, 0.51, 0.53, 0.52]},
        {"id": "b", "label": "B", "signals": [0.51, 0.50, 0.52, 0.51, 0.53]},
    ], context={"horizon": 4}, objectives=_objectives())
    assert r2["action"] == "recommend"


def test_explicit_threshold_param_beats_env(log_path, monkeypatch):
    monkeypatch.setenv("TOA_MIN_CONFIDENCE", "0.9999")
    r = td.toa_decide("t-param", _opts(), context={"horizon": 4},
                      objectives=_objectives(), min_confidence=0.0)
    assert r["threshold"] == pytest.approx(0.0)
    assert r["action"] == "recommend"


# ---------------------------------------------------------------------------
# Hard-gate refusal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", [
    "merge PR #496 into main",
    "deploy the hotfix to production",
    "move funds from the treasury wallet",
    "rotate the private_key for signing",
    "broadcast the mainnet contract",
    "withdraw USDC to the operator",
])
def test_hard_gate_categories_refused(log_path, label):
    r = td.toa_decide("t-refuse", [
        {"id": "x", "label": label, "signals": [0.9, 0.92, 0.95]},
    ], context={})
    assert r["action"] == "refused"
    assert r["recommended"] is None
    assert r["forecast_detail"] == {}
    assert "founder-gated" in r["rationale"]


def test_refusal_is_logged(log_path):
    td.toa_decide("t-refuse-log", [
        {"id": "x", "label": "merge everything now", "signals": [0.9, 0.9]},
    ], context={})
    lines = [json.loads(l) for l in log_path.read_text().splitlines()]
    assert len(lines) == 1
    assert lines[0]["type"] == "decision"
    assert lines[0]["action"] == "refused"


def test_treasury_inflow_objective_name_not_a_hard_gate(log_path):
    # "treasury_inflow" is a legitimate TOA objective name, not fund movement
    r = td.toa_decide("t-defi-ok", [
        {"id": "a", "label": "run defi loop", "signals": [0.5, 0.55, 0.6]},
    ], context={}, objectives={"treasury_inflow": 0.6, "risk": 0.4})
    assert r["action"] in ("recommend", "escalate")


# ---------------------------------------------------------------------------
# Decision log + outcome round-trip (the observe half of the loop)
# ---------------------------------------------------------------------------


def test_decision_log_append_and_outcome_roundtrip(log_path):
    r = td.toa_decide("t-log", _opts(), context={"horizon": 4},
                      objectives=_objectives())
    lines = [json.loads(l) for l in log_path.read_text().splitlines()]
    assert len(lines) == 1
    entry = lines[0]
    assert entry["type"] == "decision"
    assert entry["decision_id"] == "t-log"
    assert entry["outcome"] is None
    assert entry["recommended"] == r["recommended"]
    assert set(entry["options"]) == {"grow", "shrink"}
    assert "ts" in entry

    out = td.toa_record_outcome("t-log", {"success": True, "quality": 4.0})
    assert out["decision_id"] == "t-log"
    assert out["feedback_ingested"] is True
    lines = [json.loads(l) for l in log_path.read_text().splitlines()]
    assert len(lines) == 2
    assert lines[1]["type"] == "outcome"
    assert lines[1]["outcome"]["success"] is True


def test_outcome_feeds_back_into_toa(log_path):
    from agents.toa import TOAOrchestrator  # noqa  (proves the shared machinery)

    before = td._get_orchestrator().feedback_agent.event_count
    td.toa_record_outcome("t-fb", {"success": False, "quality": 1.0})
    after = td._get_orchestrator().feedback_agent.event_count
    assert after == before + 1


# ---------------------------------------------------------------------------
# Input validation (fail closed)
# ---------------------------------------------------------------------------


def test_empty_options_raises(log_path):
    with pytest.raises(ValueError):
        td.toa_decide("t-empty", [], context={})


def test_option_without_signals_raises(log_path):
    with pytest.raises(ValueError):
        td.toa_decide("t-nosig", [{"id": "x", "label": "no data"}], context={})
    with pytest.raises(ValueError):
        td.toa_decide("t-shortsig", [
            {"id": "x", "label": "one point", "signals": [0.5]},
        ], context={})


# ---------------------------------------------------------------------------
# Single-option go/no-go
# ---------------------------------------------------------------------------


def test_single_option_go_no_go(log_path):
    r = td.toa_decide("t-go", [
        {"id": "post", "label": "post the batch", "signals": [0.6, 0.65, 0.7, 0.75]},
    ], context={"horizon": 4}, objectives=_objectives())
    # strong positive forecast utility vs doing nothing -> recommend
    assert r["action"] == "recommend"
    assert r["recommended"] == "post"


# ---------------------------------------------------------------------------
# End-to-end: realistic loop-style uncertain dispatch decision
# ---------------------------------------------------------------------------


def test_e2e_loop_dispatch_decision(log_path):
    """A worker unsure which backlog item to dispatch asks TOA.

    Noisy success-rate signals per candidate; TOA forecasts, recommends,
    the worker logs the decision id and later records the outcome.
    """
    decision_id = "buildout-dispatch-2026-10-21-e2e"
    options = [
        {"id": "w58-audit", "label": "push w58 audit-record branch",
         "signals": [0.72, 0.75, 0.74, 0.78, 0.80]},
        {"id": "w60-docs", "label": "docs-only wave",
         "signals": [0.45, 0.47, 0.44, 0.46, 0.45]},
        {"id": "w61-forksim", "label": "fork-sim evidence wave",
         "signals": [0.58, 0.55, 0.60, 0.57, 0.59]},
    ]
    r = td.toa_decide(decision_id, options, context={"horizon": 6},
                      objectives={"revenue": 0.4, "risk": 0.3, "timeline": 0.2,
                                  "compliance": 0.1})
    assert r["action"] in ("recommend", "escalate")
    assert r["decision_id"] == decision_id
    # decision id is what the worker puts in its run report
    lines = [json.loads(l) for l in log_path.read_text().splitlines()]
    assert any(l["decision_id"] == decision_id and l["type"] == "decision"
               for l in lines)

    # later: the worker observes the outcome and closes the loop
    out = td.toa_record_outcome(decision_id, {
        "success": True, "quality": 4.5,
        "acted_on": r["recommended"], "action": r["action"],
    })
    assert out["feedback_ingested"] is True
    lines = [json.loads(l) for l in log_path.read_text().splitlines()]
    outcomes = [l for l in lines if l["type"] == "outcome"
                and l["decision_id"] == decision_id]
    assert len(outcomes) == 1
