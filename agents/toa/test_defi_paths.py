#!/usr/bin/env python3
"""
Tests for TOA DeFi prioritisation (treasury inflow objective + vault feedback).

Run:  python -m pytest agents/toa/test_defi_paths.py -q
"""

from __future__ import annotations

from agents.toa.config import TOAConfig, DEFAULT_OBJECTIVE_WEIGHTS
from agents.toa.forecaster import KernelForecaster
from agents.toa.orchestrator import (
    DEFI_OBJECTIVE_WEIGHTS,
    TOAOrchestrator,
    VAULT_FEEDBACK_SOURCE,
)
from agents.toa.simulator import MonteCarloSimulator


VALUES = [100.0, 102.0, 105.0, 108.0, 110.0, 113.0, 117.0]


def cfg() -> TOAConfig:
    return TOAConfig(simulation_depth=40, forecast_horizon=8, structured_logging=False)


# ----------------------------------------------------------------------
# Config: treasury_inflow is a first-class objective
# ----------------------------------------------------------------------
def test_treasury_inflow_in_default_weights():
    assert "treasury_inflow" in DEFAULT_OBJECTIVE_WEIGHTS
    assert abs(sum(DEFAULT_OBJECTIVE_WEIGHTS.values()) - 1.0) < 1e-9
    assert cfg().objective_priority[0] == "treasury_inflow"


# ----------------------------------------------------------------------
# Simulator: treasury_inflow objective differentiates DeFi paths
# ----------------------------------------------------------------------
def test_treasury_inflow_objective_scores_inflow():
    sim = MonteCarloSimulator(config=cfg())
    paths = [
        {"scenario_id": "plain", "probability": 0.5, "horizon": 8,
         "values": [100.0] * 8},
        {"scenario_id": "defi", "probability": 0.5, "horizon": 8,
         "values": [100.0] * 8, "treasury_inflow": 3000.0},
    ]
    evaluated = sim.evaluate(paths)
    by_id = {p["scenario_id"]: p for p in evaluated}
    assert by_id["defi"]["utility_score"] > by_id["plain"]["utility_score"]
    assert by_id["defi"]["objective_breakdown"]["treasury_inflow"] > 0.7
    # path without inflow signal stays neutral
    assert by_id["plain"]["objective_breakdown"]["treasury_inflow"] == 0.5


# ----------------------------------------------------------------------
# Forecaster: defi_signals boost trend + tag paths; legacy path unchanged
# ----------------------------------------------------------------------
def test_defi_signals_tag_paths_and_estimate_inflow():
    fc = KernelForecaster(config=cfg(), seed=8453)
    paths = fc.forecast({
        "values": VALUES,
        "defi_signals": {"polyclaw_edge": 0.09, "vault_yield_apr": 0.06},
    })
    assert paths, "expected paths"
    for p in paths:
        assert p["defi_path"] is True
        # inflow = last_smooth * (0.09 + 0.06) * horizon
        assert p["treasury_inflow"] > 0


def test_defi_boost_raises_terminal_values():
    kwargs = dict(config=cfg(), seed=8453)
    plain = KernelForecaster(**kwargs).forecast({"values": VALUES})
    defi = KernelForecaster(**kwargs).forecast({
        "values": VALUES,
        "defi_signals": {"polyclaw_edge": 0.10, "vault_yield_apr": 0.05},
    })
    plain_mean = sum(p["values"][-1] for p in plain) / len(plain)
    defi_mean = sum(p["values"][-1] for p in defi) / len(defi)
    assert defi_mean > plain_mean


def test_no_defi_signals_preserves_legacy_behavior():
    kwargs = dict(config=cfg(), seed=8453)
    a = KernelForecaster(**kwargs).forecast({"values": VALUES})
    b = KernelForecaster(**kwargs).forecast({"values": VALUES})
    assert [p["values"] for p in a] == [p["values"] for p in b]
    assert all("defi_path" not in p for p in a)


# ----------------------------------------------------------------------
# Orchestrator: vault events feed the loop; run_defi prioritises DeFi
# ----------------------------------------------------------------------
def test_ingest_vault_event_feeds_feedback():
    orch = TOAOrchestrator(config=cfg())
    orch.ingest_vault_event({
        "event": "Settled", "lp": "0xabc", "strategyId": 0,
        "principal": 2960.0, "fee": 266.4,
    })
    orch.ingest_vault_event({"event": "DrawDown", "lp": "0xabc", "amount": 2960.0})
    summary = orch.feedback_agent.get_feedback_summary()
    assert summary["source_distribution"][VAULT_FEEDBACK_SOURCE] == 2
    assert summary["success_rate"] == 1.0


def test_run_defi_top_action_carries_treasury_inflow():
    # lower threshold: 40 paths -> mean probability ~0.025 < default 0.05
    orch = TOAOrchestrator(config=TOAConfig(
        simulation_depth=40, forecast_horizon=8,
        structured_logging=False, collapse_threshold=0.01,
    ))
    result = orch.run_defi(
        context={"values": VALUES},
        defi_signals={"polyclaw_edge": 0.09, "vault_yield_apr": 0.06},
    )
    assert result["action_plan"], "expected a non-empty action plan"
    top = result["action_plan"][0]
    assert top["objective_breakdown"]["treasury_inflow"] > 0.7
    # treasury_inflow must be the largest *weighted* contributor to utility
    contributions = {
        name: score * DEFI_OBJECTIVE_WEIGHTS[name]
        for name, score in top["objective_breakdown"].items()
        if name in DEFI_OBJECTIVE_WEIGHTS
    }
    assert max(contributions, key=contributions.get) == "treasury_inflow"


def test_defi_weights_normalise():
    assert abs(sum(DEFI_OBJECTIVE_WEIGHTS.values()) - 1.0) < 1e-9
