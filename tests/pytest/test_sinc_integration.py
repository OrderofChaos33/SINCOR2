"""Tests for SINC token integration — access manager, blueprint, settlement, and registry."""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from marketplace.registry import AgentCardRecord, AgentCardRegistry
from marketplace.settlement import (
    AXIOM_TOKEN,
    SINC_TOKEN,
    SettlementCoordinator,
    _compute_fee,
)
from sincor2.sinc_access import (
    SINCAccessManager,
    SINCMeter,
    _decode_uint256,
    _encode_address,
    _TTLCache,
)

# ===========================================================================
# Helpers
# ===========================================================================

def _make_sinc_manager(mock_response: int = 100) -> SINCAccessManager:
    """Return a SINCAccessManager whose RPC calls return mock_response."""
    mgr = SINCAccessManager(rpc_url="http://mock-rpc")
    mgr._eth_call = MagicMock(return_value=mock_response)  # type: ignore[method-assign]
    return mgr


# ===========================================================================
# Unit: ABI helpers
# ===========================================================================

class TestABIHelpers:
    def test_encode_address_pads_to_64_chars(self):
        addr = "0xAf9B539D8043C634b7E611818518BA7E850F289e"
        encoded = _encode_address(addr)
        assert len(encoded) == 64
        assert encoded.startswith("0" * 24)

    def test_decode_uint256_zero(self):
        assert _decode_uint256("0x") == 0
        assert _decode_uint256("0x0") == 0
        assert _decode_uint256("") == 0

    def test_decode_uint256_value(self):
        assert _decode_uint256("0x64") == 100
        assert _decode_uint256("0x1f4") == 500


# ===========================================================================
# Unit: TTLCache
# ===========================================================================

class TestTTLCache:
    def test_miss_returns_none(self):
        cache = _TTLCache(ttl=60)
        assert cache.get("key") is None

    def test_set_and_get(self):
        cache = _TTLCache(ttl=60)
        cache.set("k", 42)
        assert cache.get("k") == 42

    def test_expired_entry_returns_none(self):
        import time
        cache = _TTLCache(ttl=0.01)
        cache.set("k", 99)
        time.sleep(0.05)
        assert cache.get("k") is None

    def test_invalidate_removes_prefix(self):
        cache = _TTLCache(ttl=60)
        cache.set("balance:0xabc", 10)
        cache.set("credits:0xabc", 5)
        cache.invalidate("balance:0xabc")
        assert cache.get("balance:0xabc") is None
        assert cache.get("credits:0xabc") == 5


# ===========================================================================
# Unit: SINCAccessManager
# ===========================================================================

class TestSINCAccessManager:
    def test_get_balance_returns_rpc_value(self):
        mgr = _make_sinc_manager(mock_response=250)
        assert mgr.get_balance("0xAf9B539D8043C634b7E611818518BA7E850F289e") == 250

    def test_get_balance_caches_result(self):
        mgr = _make_sinc_manager(mock_response=500)
        addr = "0xAf9B539D8043C634b7E611818518BA7E850F289e"
        mgr.get_balance(addr)
        mgr.get_balance(addr)
        assert mgr._eth_call.call_count == 1  # type: ignore[union-attr]

    def test_verify_minimum_passes(self):
        mgr = _make_sinc_manager(mock_response=600)
        assert mgr.verify_minimum("0x" + "a" * 40, 500) is True

    def test_verify_minimum_fails(self):
        mgr = _make_sinc_manager(mock_response=100)
        assert mgr.verify_minimum("0x" + "a" * 40, 500) is False

    def test_verify_minimum_zero_always_passes(self):
        mgr = _make_sinc_manager(mock_response=0)
        assert mgr.verify_minimum("0x" + "a" * 40, 0) is True

    def test_invalid_wallet_returns_zero(self):
        mgr = _make_sinc_manager(mock_response=100)
        assert mgr.get_balance("not-an-address") == 0

    def test_get_full_status_tiers(self):
        mgr = _make_sinc_manager(mock_response=1500)
        # Patch SINC_PLATFORM_ACCESS so get_staked uses RPC
        with patch("sincor2.sinc_access.SINC_PLATFORM_ACCESS", "0x" + "1" * 40):
            status = mgr.get_full_status("0x" + "a" * 40)
        assert status["tier"] == "priority"
        assert status["staking_discount"] is True

    def test_get_full_status_enterprise(self):
        mgr = _make_sinc_manager(mock_response=5000)
        with patch("sincor2.sinc_access.SINC_PLATFORM_ACCESS", "0x" + "1" * 40):
            status = mgr.get_full_status("0x" + "a" * 40)
        assert status["tier"] == "enterprise"

    def test_get_full_status_no_stake(self):
        mgr = _make_sinc_manager(mock_response=0)
        status = mgr.get_full_status("0x" + "a" * 40)
        assert status["tier"] == "none"
        assert status["can_use_advanced"] is False

    def test_rpc_failure_returns_zero(self):
        from urllib.error import URLError
        mgr = SINCAccessManager(rpc_url="http://invalid-host-xyz.invalid")
        with patch("urllib.request.urlopen", side_effect=URLError("simulated network failure")):
            result = mgr.get_balance("0x" + "a" * 40)
        assert result == 0


# ===========================================================================
# Unit: SINCMeter
# ===========================================================================

class TestSINCMeter:
    def test_record_debit(self):
        meter = SINCMeter()
        event = meter.record("0xabc", "agent_call", 1, "task-1")
        assert event.wallet == "0xabc"
        assert event.sinc_amount == 1
        assert event.direction == "debit"

    def test_get_events_filtered_by_wallet(self):
        meter = SINCMeter()
        meter.record("0xaaa", "agent_call", 1, "t1")
        meter.record("0xbbb", "agent_call", 2, "t2")
        events = meter.get_events(wallet="0xaaa")
        assert len(events) == 1
        assert events[0]["wallet"] == "0xaaa"

    def test_total_spent(self):
        meter = SINCMeter()
        meter.record("0xaaa", "agent_call", 5, "t1", direction="debit")
        meter.record("0xaaa", "agent_call", 3, "t2", direction="debit")
        meter.record("0xaaa", "reward", 10, "t3", direction="credit")
        assert meter.total_spent("0xaaa") == 8

    def test_get_events_limit(self):
        meter = SINCMeter()
        for i in range(10):
            meter.record("0xaaa", "agent_call", 1, f"t{i}")
        events = meter.get_events(wallet="0xaaa", limit=3)
        assert len(events) == 3

    def test_direction_filter(self):
        meter = SINCMeter()
        meter.record("0xaaa", "call", 1, "t1", direction="debit")
        meter.record("0xaaa", "reward", 5, "t2", direction="credit")
        assert len(meter.get_events(wallet="0xaaa", direction="credit")) == 1

    def test_file_logging(self, tmp_path):
        log_file = str(tmp_path / "sinc_usage.log")
        meter = SINCMeter(log_path=log_file)
        meter.record("0xaaa", "agent_call", 1, "task-1")
        with open(log_file) as f:
            lines = f.readlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["wallet"] == "0xaaa"


# ===========================================================================
# Unit: Settlement — SINC primary + fee routing
# ===========================================================================

class TestSettlementSINCPrimary:
    def test_create_quote_defaults_to_sinc(self):
        coordinator = SettlementCoordinator()
        quote = coordinator.create_quote("task-1", "0xpayer", "0xpayee", Decimal("1"))
        assert quote.token_symbol == "SINC"
        assert quote.token_address == SINC_TOKEN

    def test_create_quote_axiom_legacy(self):
        coordinator = SettlementCoordinator()
        quote = coordinator.create_quote(
            "task-2", "0xpayer", "0xpayee", Decimal("1"), token_symbol="AXIOM"
        )
        assert quote.token_symbol == "AXIOM"
        assert quote.token_address == AXIOM_TOKEN

    def test_sinc_amount_field_populated(self):
        coordinator = SettlementCoordinator()
        quote = coordinator.create_quote(
            "task-3", "0xpayer", "0xpayee", Decimal("5"), token_symbol="SINC"
        )
        assert quote.sinc_amount == "5.0000"

    def test_platform_fee_5_percent(self):
        coordinator = SettlementCoordinator()
        quote = coordinator.create_quote("task-4", "0xpayer", "0xpayee", Decimal("100"))
        coordinator.confirm_payment(quote.quote_id, "0xtxhash", Decimal("100"))
        settlement = list(coordinator.settlements.values())[0]
        assert Decimal(settlement.platform_fee) == Decimal("5.0000")
        assert Decimal(settlement.payee_amount) == Decimal("95.0000")

    def test_treasury_journal_records_fee(self):
        coordinator = SettlementCoordinator()
        quote = coordinator.create_quote("task-5", "0xpayer", "0xpayee", Decimal("20"))
        coordinator.confirm_payment(quote.quote_id, "0xtxhash", Decimal("20"))
        assert len(coordinator.treasury_journal) == 1
        entry = coordinator.treasury_journal[0]
        assert Decimal(entry["amount"]) == Decimal("1.0000")  # 5% of 20

    def test_compute_fee_helper(self):
        assert _compute_fee(Decimal("100")) == Decimal("5.0000")
        assert _compute_fee(Decimal("10")) == Decimal("0.5000")
        assert _compute_fee(Decimal("0")) == Decimal("0.0000")

    def test_sinc_credit_deduction_records_event(self):
        coordinator = SettlementCoordinator()
        event = coordinator.sinc_credit_deduction("0xwallet", Decimal("10"), "task-6")
        assert event["type"] == "credit_deduction"
        assert Decimal(event["platform_fee"]) == Decimal("0.5000")
        assert len(coordinator.treasury_journal) == 1

    def test_confirm_payment_unknown_quote_raises(self):
        coordinator = SettlementCoordinator()
        with pytest.raises(KeyError):
            coordinator.confirm_payment("nonexistent", "0xtx", Decimal("1"))


# ===========================================================================
# Unit: AgentCardRecord — SINC pricing fields
# ===========================================================================

class TestAgentCardSINCPricing:
    def _make_card(self, sinc_pricing=None):
        card = {
            "name": "TestAgent",
            "description": "A test agent",
            "version": "1.0.0",
            "skills": [{"id": "s1", "name": "Skill 1", "tags": []}],
        }
        if sinc_pricing is not None:
            card["sincPricing"] = sinc_pricing
        return card

    def test_defaults_when_no_pricing_block(self):
        record = AgentCardRecord.from_agent_card(self._make_card())
        assert record.sinc_price_per_call == 1
        assert record.sinc_price_per_minute == 0
        assert record.sinc_stake_required == 250

    def test_custom_pricing(self):
        record = AgentCardRecord.from_agent_card(self._make_card({
            "pricePerCall": 5,
            "pricePerMinute": 2,
            "stakeRequired": 1000,
        }))
        assert record.sinc_price_per_call == 5
        assert record.sinc_price_per_minute == 2
        assert record.sinc_stake_required == 1000

    def test_invalid_pricing_falls_back_to_default(self):
        record = AgentCardRecord.from_agent_card(self._make_card({
            "pricePerCall": "invalid",
        }))
        assert record.sinc_price_per_call == 1

    def test_registry_persists_sinc_pricing(self, tmp_path):
        registry = AgentCardRegistry(storage_path=tmp_path / "cards.json")
        card = self._make_card({"pricePerCall": 3, "stakeRequired": 500})
        record = registry.register(card)
        assert record.sinc_price_per_call == 3

        # Reload from disk
        registry2 = AgentCardRegistry(storage_path=tmp_path / "cards.json")
        loaded = registry2.get(record.agent_id)
        assert loaded is not None
        assert loaded.sinc_price_per_call == 3
        assert loaded.sinc_stake_required == 500


# ===========================================================================
# Integration: SINC Flask blueprint endpoints
# ===========================================================================

class TestSINCBlueprint:
    def test_get_tiers_returns_list(self, client):
        res = client.get("/api/sinc/tiers")
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert len(data["tiers"]) > 0

    def test_get_balance_missing_wallet(self, client):
        res = client.get("/api/sinc/balance")
        assert res.status_code == 400
        assert res.get_json()["code"] == "wallet_required"

    def test_get_balance_invalid_wallet(self, client):
        res = client.get("/api/sinc/balance?wallet=not-an-address")
        assert res.status_code == 400

    def test_get_balance_valid_wallet(self, client, app):
        mgr = app.extensions["sinc_access"]
        original = mgr._eth_call
        mgr._eth_call = MagicMock(return_value=150)
        try:
            res = client.get("/api/sinc/balance?wallet=0x" + "a" * 40)
        finally:
            mgr._eth_call = original
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert "sinc_balance" in data["data"]

    def test_get_quote_missing_action(self, client):
        res = client.post("/api/sinc/quote", json={})
        assert res.status_code == 400

    def test_get_quote_unknown_action(self, client):
        res = client.post("/api/sinc/quote", json={"action_type": "fly_to_mars"})
        assert res.status_code == 400

    def test_get_quote_valid_action(self, client):
        res = client.post("/api/sinc/quote", json={"action_type": "agent_call", "quantity": 1})
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert data["quote"]["base_cost_sinc"] == 1

    def test_get_quote_swarm_multiplier(self, client):
        res = client.post("/api/sinc/quote", json={"action_type": "swarm_hour", "quantity": 3})
        assert res.status_code == 200
        data = res.get_json()
        assert data["quote"]["base_cost_sinc"] == 30  # 10 SINC/hour × 3

    def test_initiate_stake_no_contract(self, client):
        res = client.post("/api/sinc/stake", json={"wallet": "0x" + "a" * 40, "amount": 250})
        # Returns 503 when SINC_PLATFORM_ACCESS_ADDRESS is not set
        assert res.status_code in (201, 503)

    def test_initiate_unstake_no_contract(self, client):
        res = client.post("/api/sinc/unstake", json={"wallet": "0x" + "a" * 40, "amount": 100})
        assert res.status_code in (201, 503)

    def test_purchase_credits_no_contract(self, client):
        res = client.post(
            "/api/sinc/credits/purchase", json={"wallet": "0x" + "a" * 40, "amount": 50}
        )
        assert res.status_code in (201, 503)

    def test_purchase_credits_amount_too_low(self, client):
        res = client.post(
            "/api/sinc/credits/purchase", json={"wallet": "0x" + "a" * 40, "amount": 5}
        )
        assert res.status_code == 400
        assert res.get_json()["code"] == "amount_too_low"

    def test_get_history_missing_wallet(self, client):
        res = client.get("/api/sinc/history")
        assert res.status_code == 400

    def test_get_history_valid(self, client):
        res = client.get("/api/sinc/history?wallet=0x" + "a" * 40)
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert "events" in data

    def test_spend_credits_requires_jwt(self, client):
        res = client.post("/api/sinc/credits/spend", json={
            "wallet": "0x" + "a" * 40,
            "action_type": "agent_call",
        })
        assert res.status_code == 401


# ===========================================================================
# Integration: sinc_required decorator
# ===========================================================================

class TestSINCRequired:
    def test_marketplace_register_requires_wallet(self, client):
        """register_agent is decorated with sinc_required(min_staked=250)."""
        res = client.post("/api/marketplace/register", json={"agent_card": {
            "name": "TestAgent",
            "description": "test",
            "version": "1.0.0",
            "skills": [{"id": "s1", "name": "s1", "tags": []}],
        }})
        # Without X-Wallet-Address header, returns 402
        assert res.status_code == 402
        data = res.get_json()
        assert data["error"] == "wallet_required"

    def test_marketplace_register_with_sufficient_stake(self, client, app):
        """With a wallet header and mocked sufficient stake, registration proceeds."""
        import sincor2.sinc_access as sinc_access_mod
        mgr = app.extensions["sinc_access"]
        original_call = mgr._eth_call
        original_addr = sinc_access_mod.SINC_PLATFORM_ACCESS
        mgr._eth_call = MagicMock(return_value=500)
        sinc_access_mod.SINC_PLATFORM_ACCESS = "0x" + "1" * 40
        try:
            # Invalidate cache so the new mock is used
            mgr.invalidate_cache("0x" + "a" * 40)
            res = client.post(
                "/api/marketplace/register",
                headers={"X-Wallet-Address": "0x" + "a" * 40},
                json={"agent_card": {
                    "name": "TestAgent",
                    "description": "test",
                    "version": "1.0.0",
                    "skills": [{"id": "s1", "name": "s1", "tags": []}],
                }},
            )
        finally:
            mgr._eth_call = original_call
            sinc_access_mod.SINC_PLATFORM_ACCESS = original_addr
        # Allowed through (may still fail for other reasons, but not 402)
        assert res.status_code != 402

    def test_marketplace_register_insufficient_stake(self, client, app):
        """With insufficient staked balance, returns 402."""
        mgr = app.extensions["sinc_access"]
        original = mgr._eth_call
        mgr._eth_call = MagicMock(return_value=0)
        try:
            res = client.post(
                "/api/marketplace/register",
                headers={"X-Wallet-Address": "0x" + "a" * 40},
                json={"agent_card": {
                    "name": "TestAgent",
                    "description": "test",
                    "version": "1.0.0",
                    "skills": [{"id": "s1", "name": "s1", "tags": []}],
                }},
            )
        finally:
            mgr._eth_call = original
        assert res.status_code == 402
        data = res.get_json()
        assert data["error"] == "insufficient_sinc_staked"
        assert data["required_staked"] == 250


# ===========================================================================
# Integration: Marketplace SINC pricing in API responses
# ===========================================================================

class TestMarketplaceSINCPricing:
    def test_list_agents_includes_sinc_pricing(self, client):
        res = client.get("/api/marketplace/agents")
        assert res.status_code == 200
        data = res.get_json()
        for agent in data.get("agents", []):
            assert "sinc_pricing" in agent
            assert "price_per_call" in agent["sinc_pricing"]

    def test_get_agent_includes_sinc_pricing(self, client):
        # List first to get an agent ID
        res = client.get("/api/marketplace/agents")
        agents = res.get_json().get("agents", [])
        if not agents:
            pytest.skip("No agents registered in test environment")
        agent_id = agents[0]["agent_id"]
        res2 = client.get(f"/api/marketplace/agents/{agent_id}")
        assert res2.status_code == 200
        data = res2.get_json()
        assert "sinc_pricing" in data


# ===========================================================================
# Integration: A2A quote endpoint includes sinc_amount
# ===========================================================================

class TestA2AQuoteSINC:
    def test_a2a_agents_list_includes_sinc_price(self, client):
        res = client.get("/api/a2a/agents")
        assert res.status_code == 200
        data = res.get_json()
        assert "sinc_contract" in data
        for agent in data.get("agents", []):
            assert "sinc_price_per_task" in agent

    def test_a2a_quote_includes_sinc_amount(self, client):
        # Get a valid skill ID first
        agents_res = client.get("/api/a2a/agents")
        agents = agents_res.get_json().get("agents", [])
        if not agents:
            pytest.skip("No agents registered")
        skill_id = agents[0]["id"]
        res = client.post("/api/a2a/quote", json={"skill_id": skill_id})
        assert res.status_code == 200
        data = res.get_json()
        assert "sinc_amount" in data
        assert data["primary_token"] == "SINC"
