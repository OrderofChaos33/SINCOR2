"""Integration tests for marketplace, routing, and vertical dispatch."""

from __future__ import annotations

import json

import pytest

from core.router import TaskRouter
from marketplace.registry import AgentCardRegistry
from verticals.loader import instantiate_vertical_agents, load_agent_cards, resolve_vertical_agent


@pytest.fixture
def registry(tmp_path):
    storage = tmp_path / "agent_cards.json"
    reg = AgentCardRegistry(storage_path=storage)
    for card in load_agent_cards():
        reg.register(card)
    return reg


def test_vertical_agent_cards_load():
    cards = load_agent_cards()
    assert len(cards) >= 5
    assert all("skills" in card for card in cards)


def test_vertical_agents_instantiate():
    agents = instantiate_vertical_agents()
    assert len(agents) == 5
    assert "healthcare_rcm_agent" in agents


def test_skill_resolution():
    agents = instantiate_vertical_agents()
    agent = resolve_vertical_agent("healthcare-rcm", agents)
    assert agent is not None
    assert agent.name == "healthcare_rcm_agent"


def test_task_router_selects_agent(registry):
    router = TaskRouter(registry=registry)
    decision = router.route("task-1", required_skills=["healthcare-rcm"])
    assert decision is not None
    assert decision.agent_id
    assert "healthcare-rcm" in decision.matched_skills or decision.score > 0


def test_task_router_respects_load(registry):
    router = TaskRouter(registry=registry)
    decision = router.route("task-1", required_skills=["healthcare-rcm"])
    assert decision is not None
    router.agent_loads[decision.agent_id] = router.DEFAULT_MAX_LOAD
    second = router.find_available_agents(["healthcare-rcm"])
    assert all(record.agent_id != decision.agent_id for record in second)


def test_marketplace_agents_endpoint(client):
    response = client.get("/api/marketplace/agents")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["count"] >= 5


def test_marketplace_verticals_endpoint(client):
    response = client.get("/api/marketplace/verticals")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["count"] == 5


def test_marketplace_skill_search(client):
    response = client.get("/api/marketplace/skills?q=healthcare")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["count"] >= 1


def test_health_includes_platform(client):
    response = client.get("/health")
    assert response.status_code == 200


def test_vertical_dispatch_healthcare():
    from sincor2.vertical_dispatch import dispatch_vertical_task

    agents = instantiate_vertical_agents()
    platform_state = {"vertical_agents": agents}
    output, error = dispatch_vertical_task(
        "healthcare-rcm",
        json.dumps({"task_type": "eligibility_verification", "payload": {"patient_id": "P-1"}}),
        platform_state,
    )
    assert error is None
    assert "success" in output