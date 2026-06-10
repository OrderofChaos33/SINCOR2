"""Tests for the SINCOR framework adapter layer, observability, policy engine,
SINC-aware reputation routing, healthcare schemas + HIPAA guardrails, and
self-registration script.
"""

from __future__ import annotations

import json
import math
import sys
import os
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

# Ensure src paths are on PYTHONPATH (mirrors the CI setup)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "sincor2"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


# ===========================================================================
# 1. Generic Adapter
# ===========================================================================

class TestGenericAdapter:
    def _make_adapter(self, handler=None):
        from adapters.generic_adapter import GenericAdapter

        def _default_handler(task):
            return {"status": "success", "result": {"echo": task}}

        return GenericAdapter(
            name="Test Agent",
            handler=handler or _default_handler,
            skills=[{"id": "test-skill", "name": "Test Skill"}],
            description="A test agent.",
        )

    def test_build_agent_card_structure(self):
        adapter = self._make_adapter()
        card = adapter.build_agent_card(base_url="https://example.com")
        assert card["name"] == "Test Agent"
        assert card["version"] == "1.0.0"
        assert len(card["skills"]) == 1
        assert card["skills"][0]["id"] == "test-skill"
        assert card["supportedInterfaces"][0]["url"].endswith("/api/a2a")
        assert card["capabilities"]["streaming"] is False

    def test_agent_card_skill_defaults_filled(self):
        adapter = self._make_adapter()
        card = adapter.build_agent_card()
        skill = card["skills"][0]
        assert "description" in skill
        assert "inputModes" in skill
        assert "outputModes" in skill

    def test_to_flask_blueprint_serves_agent_card(self):
        adapter = self._make_adapter()
        from flask import Flask

        app = Flask(__name__)
        app.register_blueprint(adapter.to_flask_blueprint())
        client = app.test_client()

        resp = client.get("/.well-known/agent-card.json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["name"] == "Test Agent"

    def test_to_flask_blueprint_serves_legacy_agent_json(self):
        adapter = self._make_adapter()
        from flask import Flask

        app = Flask(__name__)
        app.register_blueprint(adapter.to_flask_blueprint())
        client = app.test_client()

        resp = client.get("/.well-known/agent.json")
        assert resp.status_code == 200

    def test_to_flask_blueprint_a2a_rpc_message_send(self):
        adapter = self._make_adapter()
        from flask import Flask

        app = Flask(__name__)
        app.register_blueprint(adapter.to_flask_blueprint())
        client = app.test_client()

        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "message/send",
            "params": {
                "message": {"parts": [{"kind": "text", "text": "hello"}]}
            },
        }
        resp = client.post("/api/a2a", json=body)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["result"]["status"]["state"] == "completed"

    def test_to_flask_blueprint_unknown_method_returns_error(self):
        adapter = self._make_adapter()
        from flask import Flask

        app = Flask(__name__)
        app.register_blueprint(adapter.to_flask_blueprint())
        client = app.test_client()

        body = {"jsonrpc": "2.0", "id": 2, "method": "unknown/method", "params": {}}
        resp = client.post("/api/a2a", json=body)
        assert resp.status_code == 400
        data = resp.get_json()
        assert "error" in data

    def test_sincor_agent_decorator(self):
        from adapters.generic_adapter import sincor_agent

        @sincor_agent(
            name="Decorated Agent",
            skills=[{"id": "deco-skill", "name": "Deco Skill"}],
            description="Decorated.",
        )
        def my_handler(task):
            return {"status": "success", "result": {"x": 1}}

        # Decorated function still callable
        result = my_handler({"task_type": "deco_skill", "payload": {}})
        assert result["status"] == "success"

        # Has adapter attrs
        assert hasattr(my_handler, "adapter")
        card = my_handler.build_agent_card()
        assert card["name"] == "Decorated Agent"


# ===========================================================================
# 2. CrewAI Adapter
# ===========================================================================

class TestCrewAIAdapter:
    def _mock_crew(self):
        crew = MagicMock()
        agent1 = MagicMock()
        agent1.role = "Researcher"
        agent1.goal = "Find facts"
        agent2 = MagicMock()
        agent2.role = "Writer"
        agent2.goal = "Write summaries"
        crew.agents = [agent1, agent2]
        task1 = MagicMock()
        task1.description = "Research the topic and produce a summary"
        crew.tasks = [task1]
        crew.kickoff.return_value = "Mock research result"
        return crew

    def test_wrap_crew_returns_adapter(self):
        from adapters.crewai_adapter import wrap_crew

        crew = self._mock_crew()
        adapter = wrap_crew(crew, name="Test Crew")
        assert adapter.name == "Test Crew"
        assert any(s["id"] == "researcher" for s in adapter.skills)
        assert "crewai" in adapter.tags

    def test_wrap_crew_skills_derived_from_agents(self):
        from adapters.crewai_adapter import wrap_crew

        crew = self._mock_crew()
        adapter = wrap_crew(crew)
        skill_ids = [s["id"] for s in adapter.skills]
        assert "researcher" in skill_ids
        assert "writer" in skill_ids

    def test_wrap_crew_handler_calls_kickoff(self):
        from adapters.crewai_adapter import wrap_crew

        crew = self._mock_crew()
        adapter = wrap_crew(crew)
        result = adapter.handler({"task_type": "researcher", "payload": {"input": "test"}})
        assert result["status"] == "success"
        assert "Mock research result" in result["result"]["output"]
        crew.kickoff.assert_called_once()

    def test_wrap_crew_handler_handles_exception(self):
        from adapters.crewai_adapter import wrap_crew

        crew = self._mock_crew()
        crew.kickoff.side_effect = RuntimeError("boom")
        adapter = wrap_crew(crew)
        result = adapter.handler({"task_type": "researcher", "payload": {}})
        assert result["status"] == "error"
        assert "boom" in result["error"]


# ===========================================================================
# 3. LangGraph Adapter
# ===========================================================================

class TestLangGraphAdapter:
    def _mock_graph(self):
        graph = MagicMock()
        # Simulate compiled graph with nodes
        inner = MagicMock()
        inner.nodes = {"search": MagicMock(), "summarise": MagicMock(), "__start__": MagicMock()}
        graph.graph = inner
        graph.invoke.return_value = {"output": "LangGraph result"}
        return graph

    def test_wrap_graph_returns_adapter(self):
        from adapters.langgraph_adapter import wrap_graph

        graph = self._mock_graph()
        adapter = wrap_graph(graph, name="LG Agent")
        assert adapter.name == "LG Agent"
        assert "langgraph" in adapter.tags

    def test_wrap_graph_nodes_become_skills(self):
        from adapters.langgraph_adapter import wrap_graph

        graph = self._mock_graph()
        adapter = wrap_graph(graph)
        skill_ids = [s["id"] for s in adapter.skills]
        # __start__ should be filtered out
        assert "__start__" not in skill_ids
        assert "search" in skill_ids or "summarise" in skill_ids

    def test_wrap_graph_handler_invokes_graph(self):
        from adapters.langgraph_adapter import wrap_graph

        graph = self._mock_graph()
        adapter = wrap_graph(graph)
        result = adapter.handler({"task_type": "search", "payload": {"input": "AI"}})
        assert result["status"] == "success"
        assert result["result"]["output"] == "LangGraph result"

    def test_wrap_graph_handler_handles_error(self):
        from adapters.langgraph_adapter import wrap_graph

        graph = self._mock_graph()
        graph.invoke.side_effect = ValueError("graph error")
        adapter = wrap_graph(graph)
        result = adapter.handler({"task_type": "search", "payload": {}})
        assert result["status"] == "error"
        assert "graph error" in result["error"]

    def test_wrap_graph_explicit_skills_preserved(self):
        from adapters.langgraph_adapter import wrap_graph

        graph = self._mock_graph()
        explicit = [{"id": "my-skill", "name": "My Skill"}]
        adapter = wrap_graph(graph, skills=explicit)
        skill_ids = [s["id"] for s in adapter.skills]
        assert "my-skill" in skill_ids


# ===========================================================================
# 4. BeeAI Adapter
# ===========================================================================

class TestBeeAIAdapter:
    def _mock_agent(self):
        agent = MagicMock()
        tool1 = MagicMock()
        tool1.name = "WebSearch"
        tool1.description = "Search the web."
        agent.tools = [tool1]
        agent.run.return_value = MagicMock(result="BeeAI answer")
        return agent

    def test_wrap_beeai_returns_adapter(self):
        from adapters.beeai_adapter import wrap_beeai

        agent = self._mock_agent()
        adapter = wrap_beeai(agent, name="BeeAI Test Agent")
        assert adapter.name == "BeeAI Test Agent"
        assert "beeai" in adapter.tags

    def test_wrap_beeai_skills_from_tools(self):
        from adapters.beeai_adapter import wrap_beeai

        agent = self._mock_agent()
        adapter = wrap_beeai(agent)
        skill_ids = [s["id"] for s in adapter.skills]
        assert "websearch" in skill_ids

    def test_wrap_beeai_handler_calls_run(self):
        from adapters.beeai_adapter import wrap_beeai

        agent = self._mock_agent()
        adapter = wrap_beeai(agent)
        result = adapter.handler({"task_type": "websearch", "payload": {"input": "AI"}})
        assert result["status"] == "success"
        assert "BeeAI answer" in result["result"]["output"]

    def test_wrap_beeai_invoke_fallback(self):
        from adapters.beeai_adapter import wrap_beeai

        agent = MagicMock(spec=[])  # no run or invoke
        del agent.run
        del agent.invoke
        adapter = wrap_beeai(agent)
        result = adapter.handler({"task_type": "test", "payload": {"input": "x"}})
        assert result["status"] == "error"


# ===========================================================================
# 5. Observability
# ===========================================================================

class TestObservability:
    def test_trace_task_emits_on_success(self, capsys):
        from sincor2.observability import trace_task

        with trace_task("t-1", skill_id="test-skill", agent_id="test-agent") as span:
            span.set_attribute("result.count", 5)

        out = capsys.readouterr().out
        events = [json.loads(line) for line in out.strip().splitlines() if line.strip()]
        assert any("sincor_trace" in e for e in events)
        trace = next(e["sincor_trace"] for e in events if "sincor_trace" in e)
        assert trace["task_id"] == "t-1"
        assert trace["skill_id"] == "test-skill"
        assert trace["status"] == "ok"
        assert trace["attributes"]["result.count"] == 5
        assert trace["duration_ms"] >= 0

    def test_trace_task_captures_error(self, capsys):
        from sincor2.observability import trace_task

        with pytest.raises(ValueError):
            with trace_task("t-2", skill_id="fail-skill"):
                raise ValueError("test error")

        out = capsys.readouterr().out
        events = [json.loads(line) for line in out.strip().splitlines() if line.strip()]
        trace = next(e["sincor_trace"] for e in events if "sincor_trace" in e)
        assert trace["status"] == "error"
        assert "test error" in trace["error"]

    def test_get_tracer_returns_singleton(self):
        from sincor2.observability import get_tracer

        t1 = get_tracer()
        t2 = get_tracer()
        assert t1 is t2

    def test_span_set_error(self):
        from sincor2.observability import Span

        span = Span(
            span_id="abc",
            trace_id="xyz",
            operation_name="test",
            start_time_ms=0.0,
        )
        span.set_error(RuntimeError("boom"))
        assert span.status == "error"
        assert "boom" in span.error

    def test_trace_level_off_suppresses_output(self, capsys):
        from sincor2.observability import Tracer

        tracer = Tracer(trace_level="off")
        span = tracer.start_span("silent.op", task_id="t-silent")
        tracer.emit(span)
        out = capsys.readouterr().out
        assert "sincor_trace" not in out


# ===========================================================================
# 6. Execution Policy — extended
# ===========================================================================

class TestExtendedExecutionPolicy:
    def test_check_policy_allows_valid_task(self):
        from core.policy import ExecutionPolicy

        policy = ExecutionPolicy()
        result = policy.check_policy({"budget": 50.0, "retries": 1, "request_rate": 10})
        assert result["allowed"] is True
        assert result["violations"] == []

    def test_check_policy_blocks_over_budget(self):
        from core.policy import ExecutionPolicy

        policy = ExecutionPolicy()
        result = policy.check_policy({"budget": 9999.0, "retries": 0, "request_rate": 1})
        assert result["allowed"] is False
        assert any(v["rule"] == "max-budget" for v in result["violations"])

    def test_middleware_context_auth_middleware_rejects_missing_caller(self):
        from core.policy import auth_middleware, MiddlewareContext

        ctx = MiddlewareContext({"caller_id": ""})
        auth_middleware(ctx)
        assert ctx.allowed is False
        assert "caller_id" in ctx.rejection_reason

    def test_middleware_context_auth_middleware_allows_valid_caller(self):
        from core.policy import auth_middleware, MiddlewareContext

        ctx = MiddlewareContext({"caller_id": "wallet-0xABC"})
        auth_middleware(ctx)
        assert ctx.allowed is True

    def test_sinc_balance_middleware_factory(self):
        from core.policy import sinc_balance_middleware, MiddlewareContext

        check = sinc_balance_middleware(min_sinc_balance=100.0)

        ctx_pass = MiddlewareContext({"caller_id": "x", "sinc_balance": 200.0})
        check(ctx_pass)
        assert ctx_pass.allowed is True

        ctx_fail = MiddlewareContext({"caller_id": "x", "sinc_balance": 50.0})
        check(ctx_fail)
        assert ctx_fail.allowed is False

    def test_schema_validation_middleware_passes_valid(self):
        from core.policy import schema_validation_middleware, MiddlewareContext
        from pydantic import BaseModel

        class MySchema(BaseModel):
            status: str

        mw = schema_validation_middleware(MySchema)
        ctx = MiddlewareContext({})
        ctx.metadata["result"] = {"status": "success"}
        mw(ctx)
        assert ctx.allowed is True

    def test_schema_validation_middleware_fails_invalid(self):
        from core.policy import schema_validation_middleware, MiddlewareContext
        from pydantic import BaseModel

        class MySchema(BaseModel):
            required_field: str

        mw = schema_validation_middleware(MySchema)
        ctx = MiddlewareContext({})
        ctx.metadata["result"] = {"not_required_field": "x"}
        mw(ctx)
        assert ctx.allowed is False

    def test_policy_enforced_executor_success(self):
        from core.policy import PolicyEnforcedExecutor

        executor = PolicyEnforcedExecutor()

        def handler(task):
            return {"status": "success", "result": {"x": 1}}

        result = executor.execute(handler, {"caller_id": "agent-1"})
        assert result["status"] == "success"

    def test_policy_enforced_executor_retries_then_succeeds(self):
        from core.policy import PolicyEnforcedExecutor

        attempts = []

        def flaky_handler(task):
            attempts.append(1)
            if len(attempts) < 2:
                raise RuntimeError("transient error")
            return {"status": "success", "result": {}}

        executor = PolicyEnforcedExecutor(max_retries=3, base_delay=0.0)
        result = executor.execute(flaky_handler, {"caller_id": "agent-1"})
        assert result["status"] == "success"
        assert len(attempts) == 2

    def test_policy_enforced_executor_exhausted_uses_fallback(self):
        from core.policy import PolicyEnforcedExecutor

        def always_fail(task):
            raise RuntimeError("always")

        def fallback(task):
            return {"status": "partial", "result": {"fallback": True}}

        executor = PolicyEnforcedExecutor(
            max_retries=1,
            base_delay=0.0,
            fallback_handler=fallback,
        )
        result = executor.execute(always_fail, {"caller_id": "x"})
        assert result["status"] == "partial"
        assert result["result"]["fallback"] is True

    def test_policy_enforced_executor_blocks_missing_caller(self):
        from core.policy import PolicyEnforcedExecutor, auth_middleware

        # Explicitly opt in to auth_middleware (not in the default list)
        executor = PolicyEnforcedExecutor(pre_middleware=[auth_middleware])

        def handler(task):
            return {"status": "success", "result": {}}

        result = executor.execute(handler, {"caller_id": ""})
        assert result["status"] == "error"
        assert "caller_id" in result["error"]

    def test_axiom_settlement_middleware_triggers_hook(self):
        from core.policy import axiom_settlement_middleware, MiddlewareContext

        triggered = []

        def hook(task):
            triggered.append(task)

        mw = axiom_settlement_middleware(settlement_hook=hook)
        ctx = MiddlewareContext({"task_id": "t-99"})
        mw(ctx)
        assert len(triggered) == 1
        assert ctx.settlement_triggered is True


# ===========================================================================
# 7. SINC-Aware Reputation Routing
# ===========================================================================

class TestReputationEngine:
    def _engine(self):
        from marketplace.reputation import ReputationEngine

        return ReputationEngine(smoothing=0.2)

    def test_stake_sinc_increases_trust_score(self):
        engine = self._engine()
        # Record some outcomes first so the base score is meaningful
        engine.record_task_outcome("agent-a", success=True, quality_rating=4.0)
        engine.record_task_outcome("agent-a", success=True, quality_rating=4.5)

        profile_before = engine.get_reputation("agent-a")
        engine.stake_sinc("agent-a", 100.0)
        profile_after = engine.get_reputation("agent-a")

        assert profile_after["trust_score"] > profile_before["trust_score"]
        assert profile_after["sinc_staked"] == 100.0

    def test_unstake_sinc_reduces_trust_score(self):
        engine = self._engine()
        engine.record_task_outcome("agent-b", success=True, quality_rating=4.0)
        engine.stake_sinc("agent-b", 200.0)
        score_with_stake = engine.get_reputation("agent-b")["trust_score"]

        engine.unstake_sinc("agent-b", 200.0)
        score_without_stake = engine.get_reputation("agent-b")["trust_score"]

        assert score_without_stake < score_with_stake

    def test_unstake_all_clears_sinc(self):
        engine = self._engine()
        engine.stake_sinc("agent-c", 500.0)
        engine.unstake_sinc("agent-c")
        profile = engine.get_reputation("agent-c")
        assert profile["sinc_staked"] == 0.0

    def test_reward_sinc_accumulates(self):
        engine = self._engine()
        engine.reward_sinc("agent-d", "task-1", 10.0)
        engine.reward_sinc("agent-d", "task-2", 5.5)
        profile = engine.get_reputation("agent-d")
        assert abs(profile["sinc_earned"] - 15.5) < 1e-6

    def test_reward_history_filter(self):
        engine = self._engine()
        engine.reward_sinc("agent-e", "t-1", 1.0)
        engine.reward_sinc("agent-f", "t-2", 2.0)
        history = engine.get_reward_history("agent-e")
        assert len(history) == 1
        assert history[0]["agent_id"] == "agent-e"

    def test_trust_score_formula(self):
        engine = self._engine()
        engine._profiles["manual"] = engine._profiles.get(
            "manual",
            __import__("marketplace.reputation", fromlist=["ReputationProfile"]).ReputationProfile(
                agent_id="manual",
                success_rate_ema=1.0,
                quality_ema=1.0,
                tasks_completed=20,
                sinc_staked=0.0,
            ),
        )
        score_no_stake = engine.calculate_trust_score("manual")
        engine.stake_sinc("manual", 100.0)
        score_with_stake = engine.calculate_trust_score("manual")
        # Boost is capped at 3× base score; log(101) ≈ 5.6 which exceeds cap
        expected_boost = min(1.0 + math.log(100.0 + 1.0), 3.0)
        assert abs(score_with_stake - score_no_stake * expected_boost) < 0.001

    def test_leaderboard_orders_by_trust_score(self):
        engine = self._engine()
        engine.record_task_outcome("low", success=False, quality_rating=1.0)
        engine.record_task_outcome("high", success=True, quality_rating=5.0)
        engine.stake_sinc("high", 50.0)
        board = engine.get_leaderboard(limit=10)
        ids = [e["agent_id"] for e in board]
        assert ids.index("high") < ids.index("low")

    def test_stake_negative_raises(self):
        engine = self._engine()
        with pytest.raises(ValueError):
            engine.stake_sinc("agent-x", -1.0)

    def test_reward_negative_raises(self):
        engine = self._engine()
        with pytest.raises(ValueError):
            engine.reward_sinc("agent-x", "t-0", -1.0)


# ===========================================================================
# 8. SINC-Weighted Discovery
# ===========================================================================

class TestSINCWeightedDiscovery:
    def _records(self):
        from marketplace.registry import AgentCardRecord

        return [
            AgentCardRecord(
                agent_id="agent-low",
                name="Low Reputation Agent",
                description="Desc",
                version="1.0.0",
                endpoint="http://low.example.com/api/a2a",
                skills=[{"id": "search", "name": "Search", "tags": ["search"], "description": ""}],
                tags=["search"],
                capabilities={"stateTransitionHistory": False},
            ),
            AgentCardRecord(
                agent_id="agent-high",
                name="High Reputation Agent",
                description="Desc",
                version="1.0.0",
                endpoint="http://high.example.com/api/a2a",
                skills=[{"id": "search", "name": "Search", "tags": ["search"], "description": ""}],
                tags=["search"],
                capabilities={"stateTransitionHistory": False},
            ),
        ]

    def _engine_with_profiles(self):
        from marketplace.reputation import ReputationEngine

        engine = ReputationEngine()
        engine.record_task_outcome("agent-low", success=False, quality_rating=1.0)
        engine.record_task_outcome("agent-high", success=True, quality_rating=5.0)
        engine.stake_sinc("agent-high", 100.0)
        return engine

    def test_sinc_staked_agent_ranked_higher(self):
        from marketplace.discovery import CapabilityMatcher

        records = self._records()
        engine = self._engine_with_profiles()
        matcher = CapabilityMatcher(reputation_engine=engine)
        results = matcher.match(records, required_skills=["search"])

        assert results[0].agent.agent_id == "agent-high"
        assert results[0].sinc_staked == 100.0

    def test_match_agents_convenience(self):
        from marketplace.discovery import match_agents

        records = self._records()
        engine = self._engine_with_profiles()
        results = match_agents(records, ["search"], reputation_engine=engine)
        assert results[0].agent.agent_id == "agent-high"

    def test_match_result_includes_trust_fields(self):
        from marketplace.discovery import CapabilityMatcher

        records = self._records()
        engine = self._engine_with_profiles()
        matcher = CapabilityMatcher(reputation_engine=engine)
        results = matcher.match(records, required_skills=["search"])
        for r in results:
            assert hasattr(r, "trust_score")
            assert hasattr(r, "sinc_staked")

    def test_match_without_reputation_engine(self):
        from marketplace.discovery import CapabilityMatcher

        records = self._records()
        matcher = CapabilityMatcher(reputation_engine=None)
        results = matcher.match(records, required_skills=["search"])
        # Both agents match; order by name when no reputation
        assert len(results) == 2


# ===========================================================================
# 9. Healthcare Schemas + HIPAA Guardrails
# ===========================================================================

class TestHealthcareSchemas:
    def test_eligibility_request_validates(self):
        from verticals.healthcare.schemas import EligibilityRequest
        from datetime import date

        req = EligibilityRequest(
            member_id="M-12345",
            payer_id="BCBS01",
            service_date=date(2026, 6, 10),
            provider_npi="1234567890",
        )
        assert req.member_id == "M-12345"
        assert req.provider_npi == "1234567890"

    def test_eligibility_request_rejects_bad_npi(self):
        from verticals.healthcare.schemas import EligibilityRequest
        from datetime import date
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            EligibilityRequest(
                member_id="M-1",
                payer_id="BCBS",
                service_date=date(2026, 1, 1),
                provider_npi="12345",  # too short
            )

    def test_claim_submission_request_total_charge(self):
        from verticals.healthcare.schemas import ClaimSubmissionRequest, ServiceLine, DiagnosisCode
        from datetime import date

        req = ClaimSubmissionRequest(
            patient_id="P-001",
            patient_dob=date(1980, 1, 1),
            member_id="M-001",
            payer_id="BCBS01",
            billing_provider_npi="1234567890",
            billing_provider_tax_id="12-3456789",
            diagnosis_codes=[DiagnosisCode(code="Z00.00")],
            service_lines=[
                ServiceLine(
                    cpt_code="99213",
                    charge_amount=Decimal("150.00"),
                    service_date=date(2026, 6, 10),
                    units=2,
                )
            ],
        )
        assert req.total_charge == Decimal("300.00")

    def test_prior_auth_request_validates(self):
        from verticals.healthcare.schemas import PriorAuthRequest
        from datetime import date

        req = PriorAuthRequest(
            patient_id="P-002",
            member_id="M-002",
            payer_id="AETNA",
            provider_npi="1234567890",
            cpt_codes=["27447"],
            diagnosis_codes=["M17.11"],
            requested_start_date=date(2026, 7, 1),
        )
        assert req.requested_units == 1


class TestHIPAAGuardrails:
    def test_mask_phi_masks_patient_id(self):
        from verticals.healthcare.hipaa_guardrails import mask_phi

        data = {"patient_id": "P-12345", "payer_id": "BCBS"}
        masked = mask_phi(data)
        assert masked["patient_id"] == "***PHI***"
        assert masked["payer_id"] == "BCBS"  # not PHI

    def test_mask_phi_masks_member_id(self):
        from verticals.healthcare.hipaa_guardrails import mask_phi

        data = {"member_id": "M-999", "claim_id": "CLM-001"}
        masked = mask_phi(data)
        assert masked["member_id"] == "***PHI***"
        assert masked["claim_id"] == "CLM-001"

    def test_mask_phi_nested(self):
        from verticals.healthcare.hipaa_guardrails import mask_phi

        data = {"claim": {"patient_id": "P-1", "total": 100}}
        masked = mask_phi(data)
        assert masked["claim"]["patient_id"] == "***PHI***"
        assert masked["claim"]["total"] == 100

    def test_mask_phi_list(self):
        from verticals.healthcare.hipaa_guardrails import mask_phi

        data = [{"patient_id": "P-1"}, {"patient_id": "P-2"}]
        masked = mask_phi(data)
        assert all(item["patient_id"] == "***PHI***" for item in masked)

    def test_mask_phi_provider_npi_not_masked(self):
        from verticals.healthcare.hipaa_guardrails import mask_phi

        # provider_npi is a public identifier — should not be masked
        data = {"provider_npi": "1234567890", "patient_id": "P-1"}
        masked = mask_phi(data)
        assert masked["provider_npi"] == "1234567890"

    def test_phi_token_is_deterministic(self):
        from verticals.healthcare.hipaa_guardrails import phi_token

        t1 = phi_token("M-12345")
        t2 = phi_token("M-12345")
        t3 = phi_token("M-99999")
        assert t1 == t2
        assert t1 != t3
        assert t1.startswith("phi-")

    def test_hipaa_audit_log_emits_event(self, capsys):
        from verticals.healthcare.hipaa_guardrails import hipaa_audit_log

        event = hipaa_audit_log(
            action="eligibility_verification",
            agent_id="healthcare_rcm_agent",
            task_id="t-100",
            outcome="success",
            phi_token_value="phi-abc123",
        )
        assert event["action"] == "eligibility_verification"
        assert event["hipaa_audit"] is True
        out = capsys.readouterr().out
        assert "eligibility_verification" in out

    def test_hipaa_audit_log_masks_additional_context(self, capsys):
        from verticals.healthcare.hipaa_guardrails import hipaa_audit_log

        hipaa_audit_log(
            action="claim_submission",
            agent_id="healthcare_rcm_agent",
            task_id="t-101",
            outcome="success",
            additional_context={"patient_id": "P-999", "payer_id": "BCBS"},
        )
        out = capsys.readouterr().out
        assert "P-999" not in out  # PHI masked in additional_context


# ===========================================================================
# 10. Self-Registration Script
# ===========================================================================

class TestRegisterAgent:
    _VALID_CARD = {
        "name": "My Test Agent",
        "description": "A test agent.",
        "version": "1.0.0",
        "skills": [{"id": "test-skill", "name": "Test Skill"}],
        "supportedInterfaces": [{"url": "http://agent.example.com/api/a2a"}],
    }

    def test_validate_agent_card_passes(self):
        from scripts.register_agent import validate_agent_card

        validate_agent_card(self._VALID_CARD)  # Should not raise

    def test_validate_agent_card_missing_name(self):
        from scripts.register_agent import validate_agent_card, A2AValidationError

        bad = dict(self._VALID_CARD)
        del bad["name"]
        with pytest.raises(A2AValidationError, match="name"):
            validate_agent_card(bad)

    def test_validate_agent_card_no_skills(self):
        from scripts.register_agent import validate_agent_card, A2AValidationError

        bad = dict(self._VALID_CARD, skills=[])
        with pytest.raises(A2AValidationError, match="skill"):
            validate_agent_card(bad)

    def test_validate_agent_card_skill_missing_id(self):
        from scripts.register_agent import validate_agent_card, A2AValidationError

        bad = dict(self._VALID_CARD, skills=[{"name": "No ID"}])
        with pytest.raises(A2AValidationError, match="id"):
            validate_agent_card(bad)

    def test_validate_agent_card_no_interfaces(self):
        from scripts.register_agent import validate_agent_card, A2AValidationError

        bad = dict(self._VALID_CARD, supportedInterfaces=[])
        with pytest.raises(A2AValidationError, match="supportedInterfaces"):
            validate_agent_card(bad)

    def test_fetch_agent_card_uses_well_known(self):
        from scripts.register_agent import fetch_agent_card
        import urllib.request as _req

        card_json = json.dumps(self._VALID_CARD).encode()

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def read(self):
                return card_json

        with patch.object(_req, "urlopen", return_value=FakeResp()):
            card = fetch_agent_card("http://agent.example.com")

        assert card["name"] == "My Test Agent"

    def test_fetch_agent_card_falls_back_to_legacy(self):
        from scripts.register_agent import fetch_agent_card
        import urllib.request as _req

        card_json = json.dumps(self._VALID_CARD).encode()

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def read(self):
                return card_json

        call_count = [0]

        def fake_urlopen(req, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise _req.HTTPError(req.full_url, 404, "Not Found", {}, None)
            return FakeResp()

        with patch.object(_req, "urlopen", side_effect=fake_urlopen):
            card = fetch_agent_card("http://agent.example.com")

        assert call_count[0] == 2  # first 404, then success on legacy path
        assert card["name"] == "My Test Agent"


# ===========================================================================
# 11. Marketplace Registration Endpoint
# ===========================================================================

class TestMarketplaceRegistrationEndpoint:
    def _valid_card(self):
        return {
            "name": "External Agent",
            "description": "An external agent.",
            "version": "1.0.0",
            "skills": [{"id": "ext-skill", "name": "External Skill"}],
            "supportedInterfaces": [{"url": "http://ext.example.com/api/a2a"}],
        }

    def test_register_endpoint_returns_201(self, client):
        resp = client.post(
            "/api/marketplace/register",
            json={"agent_card": self._valid_card(), "agent_url": "http://ext.example.com"},
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["status"] == "registered"
        assert "agent_id" in data
        assert "skills_indexed" in data

    def test_register_endpoint_missing_card_returns_400(self, client):
        resp = client.post("/api/marketplace/register", json={})
        assert resp.status_code == 400

    def test_register_endpoint_missing_name_returns_400(self, client):
        bad_card = {"description": "d", "version": "1.0", "skills": [{"id": "x", "name": "X"}],
                    "supportedInterfaces": [{"url": "http://x.com"}]}
        resp = client.post("/api/marketplace/register", json={"agent_card": bad_card})
        assert resp.status_code == 400

    def test_register_endpoint_no_skills_returns_400(self, client):
        bad_card = dict(self._valid_card(), skills=[])
        resp = client.post("/api/marketplace/register", json={"agent_card": bad_card})
        assert resp.status_code == 400

    def test_registered_agent_appears_in_list(self, client):
        client.post(
            "/api/marketplace/register",
            json={"agent_card": self._valid_card()},
        )
        resp = client.get("/api/marketplace/agents")
        assert resp.status_code == 200
        agents = resp.get_json()["agents"]
        names = [a["name"] for a in agents]
        assert "External Agent" in names


# ===========================================================================
# 12. Reputation API Endpoints
# ===========================================================================

class TestReputationEndpoints:
    def test_get_reputation_returns_profile(self, client):
        resp = client.get("/api/marketplace/reputation/some-agent")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "trust_score" in data

    def test_leaderboard_endpoint(self, client):
        resp = client.get("/api/marketplace/reputation/leaderboard")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "leaderboard" in data
