"""Tests for the Temporal Optimization Agent (TOA) module.

Covers:
* Unit tests for each sub-agent class.
* Integration test for the full TOA pipeline.
* End-to-end test exercising the feedback → re-run improvement loop.
"""

from __future__ import annotations

import os
import sys

import pytest

# Ensure the repo root is on the path for all test runners.
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agents.toa import (  # noqa: E402
    KernelForecaster,
    MonteCarloSimulator,
    RollingFeedbackAgent,
    TOAConfig,
    TOAOrchestrator,
    TOAStateStore,
    WFCCollapser,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def config() -> TOAConfig:
    """Fast, deterministic config for tests."""
    return TOAConfig(
        simulation_depth=10,
        collapse_threshold=0.01,
        top_k_paths=3,
        forecast_horizon=4,
        monte_carlo_iterations=100,
    )


@pytest.fixture
def simple_context() -> dict:
    return {"values": [10.0, 11.0, 12.0, 11.5, 13.0, 14.0]}


# ---------------------------------------------------------------------------
# TOAConfig
# ---------------------------------------------------------------------------

class TestTOAConfig:
    def test_defaults(self):
        cfg = TOAConfig()
        assert cfg.simulation_depth == 50
        assert cfg.top_k_paths == 5
        assert "revenue" in cfg.objective_weights

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("TOA_SIMULATION_DEPTH", "25")
        monkeypatch.setenv("TOA_TOP_K_PATHS", "2")
        cfg = TOAConfig.from_env()
        assert cfg.simulation_depth == 25
        assert cfg.top_k_paths == 2

    def test_objective_weights_keys(self):
        cfg = TOAConfig()
        for key in ("revenue", "risk", "timeline", "compliance", "governance"):
            assert key in cfg.objective_weights


# ---------------------------------------------------------------------------
# TOAStateStore
# ---------------------------------------------------------------------------

class TestTOAStateStore:
    def test_memory_only(self):
        store = TOAStateStore()
        store.set("foo", 42)
        assert store.get("foo") == 42
        assert store.version == 1

    def test_delete(self):
        store = TOAStateStore()
        store.set("k", "v")
        assert store.delete("k") is True
        assert store.get("k") is None
        assert store.delete("missing") is False

    def test_reset(self):
        store = TOAStateStore()
        store.set("a", 1)
        store.set("b", 2)
        store.reset()
        assert store.all() == {}
        assert store.version == 0

    def test_file_persistence(self, tmp_path):
        path = str(tmp_path / "toa_state.json")
        store1 = TOAStateStore(path=path)
        store1.set("x", [1, 2, 3])
        store2 = TOAStateStore(path=path)
        assert store2.get("x") == [1, 2, 3]
        assert store2.version > 0


# ---------------------------------------------------------------------------
# KernelForecaster
# ---------------------------------------------------------------------------

class TestKernelForecaster:
    def test_returns_paths(self, config, simple_context):
        forecaster = KernelForecaster(config=config)
        paths = forecaster.forecast(simple_context)
        assert len(paths) == config.simulation_depth
        for path in paths:
            assert "scenario_id" in path
            assert "probability" in path
            assert "horizon" in path
            assert len(path["values"]) == config.forecast_horizon

    def test_probabilities_sum_to_one(self, config, simple_context):
        forecaster = KernelForecaster(config=config)
        paths = forecaster.forecast(simple_context)
        total_prob = sum(p["probability"] for p in paths)
        assert abs(total_prob - 1.0) < 0.01

    def test_sorted_descending_probability(self, config, simple_context):
        forecaster = KernelForecaster(config=config)
        paths = forecaster.forecast(simple_context)
        probs = [p["probability"] for p in paths]
        assert probs == sorted(probs, reverse=True)

    def test_empty_observations_returns_empty(self, config):
        forecaster = KernelForecaster(config=config)
        paths = forecaster.forecast({"values": []})
        assert paths == []

    def test_horizon_override(self, config, simple_context):
        forecaster = KernelForecaster(config=config)
        paths = forecaster.forecast(simple_context, horizon=7)
        assert all(len(p["values"]) == 7 for p in paths)


# ---------------------------------------------------------------------------
# MonteCarloSimulator
# ---------------------------------------------------------------------------

class TestMonteCarloSimulator:
    def test_adds_utility_score(self, config, simple_context):
        forecaster = KernelForecaster(config=config)
        simulator = MonteCarloSimulator(config=config)
        paths = forecaster.forecast(simple_context)
        evaluated = simulator.evaluate(paths)
        assert len(evaluated) == len(paths)
        for ep in evaluated:
            assert "utility_score" in ep
            assert "objective_breakdown" in ep
            assert 0.0 <= ep["utility_score"] <= 1.0

    def test_objective_breakdown_keys(self, config, simple_context):
        forecaster = KernelForecaster(config=config)
        simulator = MonteCarloSimulator(config=config)
        paths = forecaster.forecast(simple_context)
        evaluated = simulator.evaluate(paths)
        expected_keys = {"revenue", "risk", "timeline", "compliance", "governance"}
        for ep in evaluated:
            assert expected_keys.issubset(set(ep["objective_breakdown"].keys()))

    def test_custom_objective_registered(self, config, simple_context):
        forecaster = KernelForecaster(config=config)
        simulator = MonteCarloSimulator(config=config)
        simulator.register_objective("custom_test", lambda _path: 0.99)
        paths = forecaster.forecast(simple_context)
        weights_with_custom = {**config.objective_weights, "custom_test": 0.0}
        evaluated = simulator.evaluate(paths, objectives=weights_with_custom)
        for ep in evaluated:
            assert "custom_test" in ep["objective_breakdown"]
            assert ep["objective_breakdown"]["custom_test"] == 0.99

    def test_weight_override(self, config, simple_context):
        forecaster = KernelForecaster(config=config)
        simulator = MonteCarloSimulator(config=config)
        paths = forecaster.forecast(simple_context)
        # All weight on revenue
        evaluated = simulator.evaluate(paths, objectives={"revenue": 1.0})
        for ep in evaluated:
            assert "revenue" in ep["objective_breakdown"]


# ---------------------------------------------------------------------------
# WFCCollapser
# ---------------------------------------------------------------------------

class TestWFCCollapser:
    def _get_evaluated_paths(self, config, context):
        forecaster = KernelForecaster(config=config)
        simulator = MonteCarloSimulator(config=config)
        paths = forecaster.forecast(context)
        return simulator.evaluate(paths)

    def test_returns_top_k(self, config, simple_context):
        collapser = WFCCollapser(config=config)
        evaluated = self._get_evaluated_paths(config, simple_context)
        collapsed = collapser.collapse(evaluated, top_k=2)
        assert len(collapsed) <= 2

    def test_rank_ordering(self, config, simple_context):
        collapser = WFCCollapser(config=config)
        evaluated = self._get_evaluated_paths(config, simple_context)
        collapsed = collapser.collapse(evaluated)
        ranks = [c["rank"] for c in collapsed]
        assert ranks == list(range(1, len(collapsed) + 1))

    def test_composite_scores_descending(self, config, simple_context):
        collapser = WFCCollapser(config=config)
        evaluated = self._get_evaluated_paths(config, simple_context)
        collapsed = collapser.collapse(evaluated)
        scores = [c["composite_score"] for c in collapsed]
        assert scores == sorted(scores, reverse=True)

    def test_action_dispatch_present(self, config, simple_context):
        collapser = WFCCollapser(config=config)
        evaluated = self._get_evaluated_paths(config, simple_context)
        collapsed = collapser.collapse(evaluated)
        for c in collapsed:
            assert "action_dispatch" in c
            assert "rationale" in c
            assert "required_skills" in c["action_dispatch"]

    def test_all_below_threshold_returns_empty(self, config, simple_context):
        cfg = TOAConfig(
            simulation_depth=5,
            collapse_threshold=1.0,  # Impossible threshold
            top_k_paths=3,
            forecast_horizon=4,
        )
        forecaster = KernelForecaster(config=cfg)
        simulator = MonteCarloSimulator(config=cfg)
        collapser = WFCCollapser(config=cfg)
        paths = forecaster.forecast(simple_context)
        evaluated = simulator.evaluate(paths)
        collapsed = collapser.collapse(evaluated)
        assert collapsed == []


# ---------------------------------------------------------------------------
# RollingFeedbackAgent
# ---------------------------------------------------------------------------

class TestRollingFeedbackAgent:
    def test_ingest_and_summary(self, config):
        agent = RollingFeedbackAgent(config=config)
        agent.ingest({
            "source": "trading_vertical",
            "payload": {"success": True, "quality_rating": 4.5},
        })
        summary = agent.get_feedback_summary()
        assert summary["total_events"] == 1
        assert summary["success_rate"] == 1.0
        assert summary["source_distribution"]["trading_vertical"] == 1

    def test_failure_lowers_success_rate(self, config):
        agent = RollingFeedbackAgent(config=config)
        agent.ingest({"source": "x", "payload": {"success": True}})
        agent.ingest({"source": "x", "payload": {"success": False}})
        summary = agent.get_feedback_summary()
        assert summary["success_rate"] == 0.5

    def test_buffer_bounded(self):
        cfg = TOAConfig(feedback_buffer_size=3)
        agent = RollingFeedbackAgent(config=cfg)
        for i in range(10):
            agent.ingest({"source": "test", "payload": {}})
        assert agent.event_count == 3

    def test_clear_resets(self, config):
        agent = RollingFeedbackAgent(config=config)
        agent.ingest({"source": "s", "payload": {"success": True}})
        agent.clear()
        assert agent.event_count == 0
        summary = agent.get_feedback_summary()
        assert summary["success_rate"] == 0.5  # Default fallback


# ---------------------------------------------------------------------------
# TOAOrchestrator — integration tests
# ---------------------------------------------------------------------------

class TestTOAOrchestrator:
    def test_run_returns_expected_keys(self, config, simple_context):
        toa = TOAOrchestrator(config=config)
        result = toa.run(context=simple_context)
        for key in ("run_id", "run_count", "elapsed_ms", "forecast_paths",
                    "evaluated_paths", "action_plan", "feedback_summary"):
            assert key in result, f"Missing key: {key}"

    def test_action_plan_not_empty(self, config, simple_context):
        toa = TOAOrchestrator(config=config)
        result = toa.run(context=simple_context)
        assert len(result["action_plan"]) > 0

    def test_run_count_increments(self, config, simple_context):
        toa = TOAOrchestrator(config=config)
        toa.run(context=simple_context)
        toa.run(context=simple_context)
        assert toa._run_count == 2

    def test_feedback_improves_signal(self, config, simple_context):
        toa = TOAOrchestrator(config=config)
        result1 = toa.run(context=simple_context)
        initial_signal = result1["feedback_summary"].get("feedback_signal", 0.5)

        toa.ingest_feedback({
            "source": "test",
            "payload": {"success": True, "quality_rating": 5.0},
        })
        result2 = toa.run(context=simple_context)
        updated_signal = result2["feedback_summary"]["feedback_signal"]
        # Signal should move from 0.5 baseline toward 1.0 after positive feedback
        assert updated_signal > initial_signal

    def test_stats(self, config, simple_context):
        toa = TOAOrchestrator(config=config)
        toa.run(context=simple_context)
        stats = toa.get_stats()
        assert stats["run_count"] == 1
        assert "config" in stats

    def test_custom_objective_wired(self, config, simple_context):
        toa = TOAOrchestrator(config=config)
        toa.register_objective("my_obj", lambda _: 0.75)
        # Just verify no exception is raised and run completes
        result = toa.run(context=simple_context)
        assert result["action_plan"] is not None

    def test_state_persistence(self, config, simple_context, tmp_path):
        state_file = str(tmp_path / "toa.json")
        cfg = TOAConfig(
            simulation_depth=5, forecast_horizon=3, state_path=state_file
        )
        toa1 = TOAOrchestrator(config=cfg)
        toa1.run(context=simple_context)
        assert toa1._run_count == 1

        # New orchestrator loads persisted run_count
        toa2 = TOAOrchestrator(config=cfg)
        assert toa2._run_count == 1


# ---------------------------------------------------------------------------
# End-to-end: full pipeline with task router integration
# ---------------------------------------------------------------------------

class TestTOAEndToEnd:
    """End-to-end test using a real TaskRouter to verify integration."""

    def test_e2e_with_task_router(self, simple_context, tmp_path):
        """Full pipeline: forecast → simulate → collapse → route."""
        from core.router import TaskRouter
        from marketplace.registry import AgentCardRegistry

        registry = AgentCardRegistry(storage_path=tmp_path / "cards.json")
        registry.register({
            "id": "exec-agent-01",
            "name": "Execution Agent",
            "description": "Handles execution tasks",
            "version": "1.0.0",
            "supportedInterfaces": [{"url": "/exec/v1"}],
            "skills": [
                {"id": "execution", "name": "Execution", "description": "Execute tasks",
                 "tags": ["execution"]},
                {"id": "revenue", "name": "Revenue", "description": "Revenue tasks",
                 "tags": ["revenue"]},
            ],
        })

        router = TaskRouter(registry=registry)
        cfg = TOAConfig(simulation_depth=5, forecast_horizon=3, top_k_paths=1)
        toa = TOAOrchestrator(config=cfg, task_router=router)

        result = toa.run(context=simple_context)
        assert len(result["action_plan"]) >= 1
        # Route decision may be None if no skill match — just verify shape
        if result["route_decision"] is not None:
            assert "agent_id" in result["route_decision"]
            assert "task_id" in result["route_decision"]
