"""Tests for marketplace modules: registry, discovery, reputation, and settlement."""
from __future__ import annotations

import sys
import os
from decimal import Decimal

import pytest

# Ensure marketplace package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from marketplace.registry import AgentCardRecord, AgentCardRegistry
from marketplace.discovery import CapabilityMatcher, DiscoveryIndex
from marketplace.reputation import ReputationEngine
from marketplace.settlement import SettlementCoordinator, TREASURY_ADDRESS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TRADING_CARD = {
    "name": "Trading Bot",
    "description": "Executes trades on Polymarket",
    "version": "1.0.0",
    "supportedInterfaces": [{"url": "https://trading-bot.example/a2a"}],
    "skills": [
        {
            "id": "place-trade",
            "name": "Place Trade",
            "description": "Submits limit or market orders",
            "tags": ["trading", "polymarket"],
        },
        {
            "id": "risk-check",
            "name": "Risk Check",
            "description": "Validates position sizing",
            "tags": ["risk"],
        },
    ],
    "capabilities": {"stateTransitionHistory": True},
}

COMPLIANCE_CARD = {
    "id": "compliance-agent",
    "name": "Compliance Agent",
    "description": "AML and KYC verification",
    "version": "2.0.0",
    "supportedInterfaces": [{"url": "https://compliance.example/a2a"}],
    "skills": [
        {
            "id": "kyc-verify",
            "name": "KYC Verification",
            "description": "Verifies user identity",
            "tags": ["compliance", "kyc"],
        }
    ],
}


# ===========================================================================
# AgentCardRegistry
# ===========================================================================

@pytest.fixture()
def registry(tmp_path):
    return AgentCardRegistry(storage_path=tmp_path / "registry.json")


def test_registry_register_and_get(registry):
    record = registry.register(TRADING_CARD)
    fetched = registry.get(record.agent_id)
    assert fetched is not None
    assert fetched.name == "Trading Bot"


def test_registry_from_agent_card_extracts_endpoint(registry):
    record = AgentCardRecord.from_agent_card(TRADING_CARD)
    assert record.endpoint == "https://trading-bot.example/a2a"


def test_registry_from_agent_card_collects_tags(registry):
    record = AgentCardRecord.from_agent_card(TRADING_CARD)
    assert "trading" in record.tags
    assert "polymarket" in record.tags
    assert "risk" in record.tags


def test_registry_from_agent_card_uses_explicit_id(registry):
    record = AgentCardRecord.from_agent_card(COMPLIANCE_CARD)
    assert record.agent_id == "compliance-agent"


def test_registry_search_by_skill(registry):
    registry.register(TRADING_CARD)
    registry.register(COMPLIANCE_CARD)
    results = registry.search_by_skill("kyc")
    ids = [r.agent_id for r in results]
    assert "compliance-agent" in ids
    # Trading bot should not match kyc
    trading_ids = [r.agent_id for r in registry.search_by_skill("limit order")]
    assert "compliance-agent" not in trading_ids


def test_registry_list_all_sorted_by_name(registry):
    registry.register(TRADING_CARD)
    registry.register(COMPLIANCE_CARD)
    names = [r.name for r in registry.list_all()]
    assert names == sorted(names, key=str.lower)


def test_registry_deregister(registry):
    record = registry.register(COMPLIANCE_CARD)
    removed = registry.deregister(record.agent_id)
    assert removed is True
    assert registry.get(record.agent_id) is None


def test_registry_deregister_missing_returns_false(registry):
    assert registry.deregister("nonexistent") is False


def test_registry_persists_to_disk(tmp_path):
    path = tmp_path / "persist.json"
    r1 = AgentCardRegistry(storage_path=path)
    r1.register(COMPLIANCE_CARD)
    # Load a second registry from the same file
    r2 = AgentCardRegistry(storage_path=path)
    assert r2.get("compliance-agent") is not None


# ===========================================================================
# CapabilityMatcher + DiscoveryIndex
# ===========================================================================

@pytest.fixture()
def two_agents():
    return [
        AgentCardRecord.from_agent_card(TRADING_CARD),
        AgentCardRecord.from_agent_card(COMPLIANCE_CARD),
    ]


def test_capability_matcher_matches_required_skill(two_agents):
    matcher = CapabilityMatcher()
    results = matcher.match(two_agents, required_skills=["kyc"])
    assert len(results) == 1
    assert results[0].agent.agent_id == "compliance-agent"


def test_capability_matcher_no_match_returns_empty(two_agents):
    matcher = CapabilityMatcher()
    results = matcher.match(two_agents, required_skills=["does-not-exist"])
    assert results == []


def test_capability_matcher_preferred_tags_boost_score(two_agents):
    matcher = CapabilityMatcher()
    without_tag = matcher.match(two_agents, required_skills=["place-trade"])
    with_tag = matcher.match(two_agents, required_skills=["place-trade"], preferred_tags=["polymarket"])
    assert with_tag[0].score >= without_tag[0].score


def test_capability_matcher_find_best_match(two_agents):
    matcher = CapabilityMatcher()
    best = matcher.find_best_match(two_agents, required_skills=["kyc"])
    assert best is not None
    assert best.agent.agent_id == "compliance-agent"


def test_capability_matcher_find_best_match_no_result():
    matcher = CapabilityMatcher()
    result = matcher.find_best_match([], required_skills=["anything"])
    assert result is None


def test_discovery_index_full_text_search(two_agents):
    index = DiscoveryIndex()
    index.index_cards(two_agents)
    results = index.search_full_text("aml kyc")
    ids = [r.agent_id for r in results]
    assert "compliance-agent" in ids


def test_discovery_index_tag_search(two_agents):
    index = DiscoveryIndex()
    index.index_cards(two_agents)
    results = index.search_by_tag("trading")
    assert len(results) >= 1
    assert results[0].agent_id != "compliance-agent"


def test_discovery_index_empty_query_returns_all(two_agents):
    index = DiscoveryIndex()
    index.index_cards(two_agents)
    results = index.search_full_text("")
    assert len(results) == 2


# ===========================================================================
# ReputationEngine
# ===========================================================================

def test_reputation_initial_profile_is_unknown():
    engine = ReputationEngine()
    rep = engine.get_reputation("no-such-agent")
    assert rep["trust_score"] == 0.0
    assert rep["tasks_completed"] == 0


def test_reputation_single_perfect_outcome_raises_score():
    engine = ReputationEngine()
    profile = engine.record_task_outcome("agent-1", success=True, quality_rating=5.0)
    assert profile.trust_score > 0.5


def test_reputation_repeated_failures_lower_score():
    engine = ReputationEngine(smoothing=0.5)
    for _ in range(10):
        engine.record_task_outcome("flaky-agent", success=False, quality_rating=1.0)
    profile = engine.get_reputation("flaky-agent")
    assert profile["trust_score"] < 0.5


def test_reputation_ema_converges_toward_new_observations():
    engine = ReputationEngine(smoothing=0.5)
    # Start at default 0.5; perfect inputs should push it up
    for _ in range(15):
        engine.record_task_outcome("good-agent", success=True, quality_rating=5.0)
    rep = engine.get_reputation("good-agent")
    assert rep["trust_score"] > 0.7


def test_reputation_leaderboard_ordered_by_trust():
    engine = ReputationEngine()
    engine.record_task_outcome("low", success=False, quality_rating=1.0)
    engine.record_task_outcome("high", success=True, quality_rating=5.0)
    board = engine.get_leaderboard(limit=2)
    assert board[0]["agent_id"] == "high"


def test_reputation_latency_modifier_penalizes_slow_agents():
    engine = ReputationEngine(smoothing=1.0)  # observe directly
    fast = engine.record_task_outcome("fast", success=True, quality_rating=5.0, latency_ms=100)
    slow = engine.record_task_outcome("slow", success=True, quality_rating=5.0, latency_ms=90_000)
    assert fast.trust_score >= slow.trust_score


# ===========================================================================
# SettlementCoordinator
# ===========================================================================

def test_settlement_create_quote_axiom(monkeypatch):
    monkeypatch.delenv("TREASURY_ADDRESS", raising=False)
    coordinator = SettlementCoordinator()
    quote = coordinator.create_quote(
        task_reference="task-001",
        payer="0xPayer",
        payee="0xPayee",
        amount=Decimal("10.00"),
        token_symbol="AXIOM",
    )
    assert quote.quote_id.startswith("quote-")
    assert quote.token_symbol == "AXIOM"
    assert quote.status == "quoted"
    assert quote.amount == "10.0000"


def test_settlement_create_quote_sinc():
    coordinator = SettlementCoordinator()
    quote = coordinator.create_quote(
        task_reference="task-002",
        payer="0xPayer",
        payee="0xPayee",
        amount=Decimal("5.50"),
        token_symbol="SINC",
    )
    assert quote.token_symbol == "SINC"


def test_settlement_confirm_payment_creates_record():
    coordinator = SettlementCoordinator()
    quote = coordinator.create_quote("task-003", "0xA", "0xB", Decimal("20"))
    settlement = coordinator.confirm_payment(
        quote_id=quote.quote_id,
        tx_hash="0xdeadbeef",
        confirmed_amount=Decimal("20"),
    )
    assert settlement.settlement_id.startswith("settle-")
    assert settlement.status == "confirmed"
    assert settlement.tx_hash == "0xdeadbeef"


def test_settlement_routes_to_correct_treasury():
    coordinator = SettlementCoordinator()
    quote = coordinator.create_quote("task-004", "0xA", "0xB", Decimal("50"))
    coordinator.confirm_payment(quote.quote_id, "0xabc", Decimal("50"))
    assert len(coordinator.treasury_journal) >= 1
    entry = coordinator.treasury_journal[0]
    assert entry["treasury_address"] == TREASURY_ADDRESS


def test_settlement_route_to_treasury_records_entry():
    coordinator = SettlementCoordinator()
    event = coordinator.route_to_treasury(Decimal("7.5"), "AXIOM")
    assert event["token_symbol"] == "AXIOM"
    assert event["amount"] == "7.5000"
    assert event["treasury_address"] == TREASURY_ADDRESS
    assert len(coordinator.treasury_journal) == 1


def test_settlement_quote_marked_paid_after_confirm():
    coordinator = SettlementCoordinator()
    quote = coordinator.create_quote("task-005", "0xA", "0xB", Decimal("1"))
    coordinator.confirm_payment(quote.quote_id, "0x1", Decimal("1"))
    assert coordinator.quotes[quote.quote_id].status == "paid"
