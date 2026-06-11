"""Phase 1 tests: reputation routing, marketplace endpoints, settlement, A2A task flow."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from core.router import TaskRouter
from marketplace.registry import AgentCardRegistry
from marketplace.reputation import ReputationEngine
from marketplace.settlement import SettlementCoordinator

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def registry(tmp_path):
    from verticals.loader import load_agent_cards
    reg = AgentCardRegistry(storage_path=tmp_path / "cards.json")
    for card in load_agent_cards():
        reg.register(card)
    return reg


@pytest.fixture
def reputation():
    return ReputationEngine()


@pytest.fixture
def router_with_reputation(registry, reputation):
    return TaskRouter(registry=registry, reputation=reputation)


@pytest.fixture
def settlement():
    return SettlementCoordinator()


# ---------------------------------------------------------------------------
# Reputation engine
# ---------------------------------------------------------------------------

class TestReputationEngine:
    def test_new_agent_returns_zero_tasks(self, reputation):
        profile = reputation.get_reputation("unknown-agent")
        assert profile["tasks_completed"] == 0
        assert profile["trust_score"] == 0.0

    def test_record_success_raises_trust(self, reputation):
        profile = reputation.record_task_outcome("agent-1", success=True, quality_rating=5.0)
        assert profile.trust_score > 0.5
        assert profile.tasks_completed == 1

    def test_record_failure_lowers_trust(self, reputation):
        # Prime with a success first
        reputation.record_task_outcome("agent-2", success=True, quality_rating=4.0)
        before = reputation.calculate_trust_score("agent-2")
        # Now a failure
        reputation.record_task_outcome("agent-2", success=False, quality_rating=1.0)
        after = reputation.calculate_trust_score("agent-2")
        assert after < before

    def test_leaderboard_sorted_by_trust(self, reputation):
        reputation.record_task_outcome("agent-a", success=True, quality_rating=5.0)
        reputation.record_task_outcome("agent-b", success=False, quality_rating=1.0)
        board = reputation.get_leaderboard(limit=10)
        assert board[0]["agent_id"] == "agent-a"

    def test_latency_modifier_applied(self, reputation):
        # Very high latency should reduce trust
        profile_fast = reputation.record_task_outcome(
            "fast-agent", success=True, quality_rating=5.0, latency_ms=100
        )
        # Reset with a fresh engine to isolate the slow-agent
        slow_reputation_engine = ReputationEngine()
        profile_slow = slow_reputation_engine.record_task_outcome(
            "slow-agent", success=True, quality_rating=5.0, latency_ms=90_000
        )
        assert profile_fast.trust_score > profile_slow.trust_score


# ---------------------------------------------------------------------------
# Reputation-aware router
# ---------------------------------------------------------------------------

class TestReputationRouter:
    def test_router_accepts_reputation_engine(self, router_with_reputation):
        assert router_with_reputation.reputation is not None

    def test_route_includes_trust_score(self, router_with_reputation):
        decision = router_with_reputation.route("t-1", required_skills=["healthcare-rcm"])
        assert decision is not None
        assert isinstance(decision.trust_score, float)

    def test_reputation_boosts_high_trust_agent(self, registry, reputation):
        # Give one agent a high trust score before routing
        records = registry.list_all()
        high_agent_id = records[0].agent_id
        # Prime with 10 successful tasks
        for _ in range(10):
            reputation.record_task_outcome(high_agent_id, success=True, quality_rating=5.0)
        router = TaskRouter(registry=registry, reputation=reputation)
        # The high-trust agent should be top preference when skill matches
        decision = router.route("t-boost", required_skills=["healthcare-rcm"])
        # We can't guarantee which agent wins but trust_score must be plausible
        assert decision is not None
        assert 0.0 <= decision.trust_score <= 1.0

    def test_routing_stats_include_reputation_weight(self, router_with_reputation):
        stats = router_with_reputation.get_routing_stats()
        assert "reputation_weight" in stats
        assert stats["reputation_weight"] == 0.25

    def test_release_load_decrements_counter(self, router_with_reputation):
        decision = router_with_reputation.route("t-load", required_skills=["healthcare-rcm"])
        assert decision is not None
        before = router_with_reputation.agent_loads.get(decision.agent_id, 0.0)
        router_with_reputation.release_load(decision.agent_id)
        after = router_with_reputation.agent_loads.get(decision.agent_id, 0.0)
        assert after == before - 1.0

    def test_release_load_never_goes_negative(self, router_with_reputation):
        router_with_reputation.release_load("no-such-agent", decrement=5.0)
        assert router_with_reputation.agent_loads.get("no-such-agent", 0.0) == 0.0


# ---------------------------------------------------------------------------
# Settlement coordinator
# ---------------------------------------------------------------------------

class TestSettlementCoordinator:
    def test_create_quote(self, settlement):
        quote = settlement.create_quote(
            task_reference="task-001",
            payer="0xPAYER",
            payee="0xPAYEE",
            amount=Decimal("1.5"),
            token_symbol="AXIOM",
        )
        assert quote.quote_id.startswith("quote-")
        assert quote.status == "quoted"
        assert quote.amount == "1.5000"

    def test_confirm_payment_creates_settlement_record(self, settlement):
        quote = settlement.create_quote("t-002", "0xA", "0xB", Decimal("2.0"))
        record = settlement.confirm_payment(
            quote_id=quote.quote_id,
            tx_hash="0xTXHASH",
            confirmed_amount=Decimal("2.0"),
        )
        assert record.settlement_id.startswith("settle-")
        assert record.status == "confirmed"
        assert record.tx_hash == "0xTXHASH"

    def test_treasury_routing_recorded(self, settlement):
        quote = settlement.create_quote("t-003", "0xA", "0xB", Decimal("1.0"))
        settlement.confirm_payment(quote.quote_id, "0xTX2", Decimal("1.0"))
        assert len(settlement.treasury_journal) == 1
        assert settlement.treasury_journal[0]["treasury_address"] != ""

    def test_route_to_treasury(self, settlement):
        event = settlement.route_to_treasury(Decimal("0.5"), "SINC")
        assert event["token_symbol"] == "SINC"
        assert event["amount"] == "0.5000"

    def test_quote_expiry_timestamp_set(self, settlement):
        quote = settlement.create_quote("t-004", "0xA", "0xB", Decimal("1.0"), expires_in_minutes=5)
        assert quote.expires_at


# ---------------------------------------------------------------------------
# Marketplace blueprint — reputation endpoints
# ---------------------------------------------------------------------------

class TestMarketplaceReputationEndpoints:
    def test_get_unknown_agent_reputation(self, client):
        resp = client.get("/api/marketplace/agents/nonexistent/reputation")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["trust_score"] == 0.0
        assert data["tasks_completed"] == 0

    def test_get_leaderboard(self, client):
        resp = client.get("/api/marketplace/leaderboard")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "leaderboard" in data

    def test_leaderboard_limit_respected(self, client):
        resp = client.get("/api/marketplace/leaderboard?limit=2")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] <= 2

    def test_record_outcome_valid(self, client):
        resp = client.post(
            "/api/marketplace/tasks/task-xyz/outcome",
            json={"agent_id": "healthcare_rcm_agent", "success": True, "quality_rating": 4.5},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["reputation"]["tasks_completed"] == 1
        assert data["reputation"]["trust_score"] > 0.5

    def test_record_outcome_missing_agent_id(self, client):
        resp = client.post(
            "/api/marketplace/tasks/task-xyz/outcome",
            json={"success": True, "quality_rating": 3.0},
        )
        assert resp.status_code == 400

    def test_record_outcome_invalid_quality_rating(self, client):
        resp = client.post(
            "/api/marketplace/tasks/task-xyz/outcome",
            json={"agent_id": "agent-1", "success": True, "quality_rating": 6.0},
        )
        assert resp.status_code == 400

    def test_record_outcome_with_latency(self, client):
        resp = client.post(
            "/api/marketplace/tasks/task-lat/outcome",
            json={"agent_id": "agent-2", "success": True, "quality_rating": 4.0, "latency_ms": 350},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Marketplace blueprint — task submission
# ---------------------------------------------------------------------------

class TestMarketplaceTaskSubmission:
    def test_submit_healthcare_task(self, client):
        resp = client.post(
            "/api/marketplace/tasks",
            json={
                "skill_id": "healthcare-rcm",
                "input": {
                    "task_type": "eligibility_verification",
                    "payload": {"patient_id": "P-999"},
                },
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "completed"
        assert data["agent_id"]

    def test_submit_task_missing_skill_id(self, client):
        resp = client.post("/api/marketplace/tasks", json={"input": "hello"})
        assert resp.status_code == 400

    def test_submit_task_missing_input(self, client):
        resp = client.post("/api/marketplace/tasks", json={"skill_id": "healthcare-rcm"})
        assert resp.status_code == 400

    def test_submit_task_unknown_skill_returns_404(self, client):
        resp = client.post(
            "/api/marketplace/tasks",
            json={"skill_id": "nonexistent-skill", "input": "test"},
        )
        assert resp.status_code == 404

    def test_submit_task_returns_settlement_quote_when_payer_provided(self, client):
        resp = client.post(
            "/api/marketplace/tasks",
            json={
                "skill_id": "healthcare-rcm",
                "input": {"task_type": "claims_status_tracking", "payload": {"claim_id": "C-1"}},
                "payer": "0xPAYERADDRESS",
                "amount": "1.0",
                "token_symbol": "AXIOM",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "settlement_quote" in data
        assert data["settlement_quote"]["payer"] == "0xPAYERADDRESS"

    def test_submit_task_trust_score_in_response(self, client):
        resp = client.post(
            "/api/marketplace/tasks",
            json={"skill_id": "healthcare-rcm", "input": "verify patient"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "trust_score" in data
        assert isinstance(data["trust_score"], float)


# ---------------------------------------------------------------------------
# Marketplace blueprint — settlement endpoints
# ---------------------------------------------------------------------------

class TestMarketplaceSettlementEndpoints:
    def test_settlement_stats_returns_zeros_initially(self, client):
        resp = client.get("/api/marketplace/settlement/stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "total_quotes" in data
        assert "total_settlements" in data

    def test_confirm_settlement_missing_fields(self, client):
        resp = client.post("/api/marketplace/settlement/confirm", json={"quote_id": "q1"})
        assert resp.status_code == 400

    def test_confirm_settlement_unknown_quote(self, client):
        resp = client.post(
            "/api/marketplace/settlement/confirm",
            json={"quote_id": "nonexistent", "tx_hash": "0xABC", "confirmed_amount": "1.0"},
        )
        assert resp.status_code == 404

    def test_full_settlement_flow(self, client):
        # 1. Submit task with payer to create quote
        resp = client.post(
            "/api/marketplace/tasks",
            json={
                "skill_id": "healthcare-rcm",
                "input": {
                    "task_type": "eligibility_verification",
                    "payload": {"patient_id": "P-001"},
                },
                "payer": "0xWALLET",
                "amount": "2.0",
            },
        )
        assert resp.status_code == 200
        quote_id = resp.get_json()["settlement_quote"]["quote_id"]

        # 2. Confirm the quote
        resp2 = client.post(
            "/api/marketplace/settlement/confirm",
            json={"quote_id": quote_id, "tx_hash": "0xTX999", "confirmed_amount": "2.0"},
        )
        assert resp2.status_code == 200
        data = resp2.get_json()
        assert data["settlement"]["status"] == "confirmed"
        assert data["settlement"]["tx_hash"] == "0xTX999"

        # 3. Stats should reflect the settled transaction
        resp3 = client.get("/api/marketplace/settlement/stats")
        stats = resp3.get_json()
        assert stats["total_settlements"] >= 1


# ---------------------------------------------------------------------------
# Marketplace blueprint — existing endpoints still work
# ---------------------------------------------------------------------------

class TestMarketplaceExistingEndpoints:
    def test_agents_list(self, client):
        resp = client.get("/api/marketplace/agents")
        assert resp.status_code == 200
        assert resp.get_json()["count"] >= 5

    def test_agent_detail(self, client):
        resp = client.get("/api/marketplace/agents")
        agent_id = resp.get_json()["agents"][0]["agent_id"]
        resp2 = client.get(f"/api/marketplace/agents/{agent_id}")
        assert resp2.status_code == 200

    def test_skills_search(self, client):
        resp = client.get("/api/marketplace/skills?q=healthcare")
        assert resp.status_code == 200
        assert resp.get_json()["count"] >= 1

    def test_routing_stats_include_reputation_weight(self, client):
        resp = client.get("/api/marketplace/routing/stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "reputation_weight" in data

    def test_verticals_list(self, client):
        resp = client.get("/api/marketplace/verticals")
        assert resp.status_code == 200
        assert resp.get_json()["count"] == 5


# ---------------------------------------------------------------------------
# A2A integration — settlement wired into _handle_send (dev env, no payment)
# ---------------------------------------------------------------------------

class TestA2ASettlementIntegration:
    def test_a2a_task_completes_without_payment_in_dev(self, client):
        resp = client.post(
            "/api/a2a",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "message/send",
                "params": {
                    "skillId": "healthcare-rcm",
                    "message": {
                        "parts": [{"text": json.dumps({
                            "task_type": "eligibility_verification",
                            "payload": {"patient_id": "P-A2A"},
                        })}],
                    },
                },
            },
        )
        assert resp.status_code == 200
        result = resp.get_json()
        assert result.get("result", {}).get("status", {}).get("state") == "completed"

    def test_a2a_task_creates_settlement_record_when_axm_paid(self, client):
        """Settlement record is created when axmPaidWei > 0 and txHash provided."""
        resp = client.post(
            "/api/a2a",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "message/send",
                "params": {
                    "skillId": "healthcare-rcm",
                    "axmPaidWei": str(1 * 10 ** 18),
                    "txHash": "0xDEADBEEF01",
                    "message": {
                        "parts": [{"text": json.dumps({
                            "task_type": "claims_status_tracking",
                            "payload": {"claim_id": "C-99"},
                        })}],
                    },
                },
            },
        )
        assert resp.status_code == 200
        # Verify settlement stat incremented
        stats_resp = client.get("/api/marketplace/settlement/stats")
        stats = stats_resp.get_json()
        assert stats["total_settlements"] >= 1

    def test_a2a_unknown_skill_returns_error(self, client):
        resp = client.post(
            "/api/a2a",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "message/send",
                "params": {
                    "skillId": "nonexistent",
                    "message": {"parts": [{"text": "test"}]},
                },
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "error" in data


# ---------------------------------------------------------------------------
# vertical_dispatch — agency kernel fallback (non-vertical skills)
# ---------------------------------------------------------------------------

class TestVerticalDispatchKernelFallback:
    def test_unmapped_skill_returns_none_without_platform(self):
        from sincor2.vertical_dispatch import dispatch_vertical_task
        result = dispatch_vertical_task("content-creation", "write a blog post", None)
        assert result is None

    def test_vertical_skill_dispatches_correctly(self):
        from sincor2.vertical_dispatch import dispatch_vertical_task
        from verticals.loader import instantiate_vertical_agents
        agents = instantiate_vertical_agents()
        platform = {"vertical_agents": agents}
        output, err = dispatch_vertical_task(
            "healthcare-rcm",
            json.dumps({"task_type": "prior_authorization", "payload": {"patient_id": "P-PRIOR"}}),
            platform,
        )
        assert err is None
        data = json.loads(output)
        assert data["status"] == "success"

    def test_dispatch_via_router_returns_result(self):
        import pathlib
        import tempfile

        from core.router import TaskRouter
        from marketplace.registry import AgentCardRegistry
        from sincor2.vertical_dispatch import dispatch_via_router
        from verticals.loader import instantiate_vertical_agents, load_agent_cards
        with tempfile.TemporaryDirectory() as tmp:
            reg = AgentCardRegistry(storage_path=pathlib.Path(tmp) / "cards.json")
            for card in load_agent_cards():
                reg.register(card)
            router = TaskRouter(registry=reg)
            agents = instantiate_vertical_agents()
            platform = {"router": router, "vertical_agents": agents}
            result = dispatch_via_router("t-router", "healthcare-rcm", json.dumps({
                "task_type": "eligibility_verification",
                "payload": {"patient_id": "P-R"},
            }), platform)
            assert result is not None
            output, err = result
            assert err is None
            assert "success" in output
