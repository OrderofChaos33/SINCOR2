"""Wave 1 agent hook — daily free ration + free peek (unit + surface).

Pins:
* the shared DailyRationTracker: 3/agent/skill/UTC-day, reset at the
  UTC-midnight boundary (injected dates), per-IP cap on free serves,
  per-IP cap on peeks, documented identity keying;
* the A2A agent card hook copy + per-skill freeDailyRation / peekUrl;
* the A2A peek route: real sample only where a handler runs cheaply
  (toa-decision), labelled metadata everywhere else, per-IP 429;
* the x402 peek route/param: real samples for toa-micro-decision and
  kya-trust-score, labelled metadata for live-external-read resources,
  404 for unknown resources;
* the A2A quote: daily ration is registered-agent gated (an
  unregistered signed wallet keeps pre-Wave-1 behaviour exactly).

Run: PYTHONPATH=src:. <venv>/python -m pytest \
  tests/pytest/test_wave1_free_ration.py -q
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from flask import Flask

import sincor2.mvp_app  # noqa: F401  (import order: billing binding)
from sincor2.free_ration import (
    DailyRationTracker,
    hook_payload,
    resolve_caller_identity,
)
from sincor2.mvp_blueprints import billing as billing_mod

WALLET = "0x" + "22" * 20
RESOURCE = "toa-micro-decision"

WINNER = {
    "options": [
        {"id": "strong", "label": "strong performer",
         "signals": [0.80, 0.82, 0.81, 0.83, 0.84]},
        {"id": "weak", "label": "weak performer",
         "signals": [0.20, 0.21, 0.19, 0.22, 0.20]},
    ],
    "context": {"horizon": 4},
}


@pytest.fixture(autouse=True)
def _clean_ration():
    from sincor2.free_ration import daily_ration_tracker

    daily_ration_tracker.clear()
    yield
    daily_ration_tracker.clear()


@pytest.fixture()
def x402_db(tmp_path, monkeypatch):
    import sincor2.x402_payments as x402

    db = tmp_path / "orders.db"
    monkeypatch.setenv("ORDERS_DB_PATH", str(db))
    x402.init_x402_db()
    return db


@pytest.fixture()
def billing_client():
    app = Flask("wave1_billing")
    app.register_blueprint(billing_mod.bp)
    app.config.update(TESTING=True)
    return app.test_client()


@pytest.fixture()
def a2a_client():
    from sincor2.a2a_integration import A2ARouter

    app = Flask("wave1_a2a")
    app.register_blueprint(A2ARouter().blueprint)
    app.config.update(TESTING=True)
    return app.test_client()


# ---------------------------------------------------------------------------
# Tracker units
# ---------------------------------------------------------------------------

class TestDailyRationTracker:
    def test_three_per_agent_per_skill_then_exhausted(self):
        t = DailyRationTracker()
        for _ in range(3):
            assert t.try_consume(WALLET, "skill-a", ip="1.1.1.1") is True
        assert t.remaining(WALLET, "skill-a") == 0
        assert t.try_consume(WALLET, "skill-a", ip="1.1.1.1") is False
        # A different skill has its own ration.
        assert t.remaining(WALLET, "skill-b") == 3
        # A different agent has its own ration.
        assert t.remaining("0x" + "33" * 20, "skill-a") == 3

    def test_reset_at_utc_midnight_boundary(self):
        t = DailyRationTracker()
        before = datetime(2026, 10, 26, 23, 59, 59, tzinfo=timezone.utc)
        after = datetime(2026, 10, 27, 0, 0, 1, tzinfo=timezone.utc)
        for _ in range(3):
            assert t.try_consume(WALLET, "s", ip="1.1.1.1", on=before) is True
        assert t.try_consume(WALLET, "s", ip="1.1.1.1", on=before) is False
        # New UTC day = new bucket, no explicit reset call needed.
        assert t.remaining(WALLET, "s", on=after) == 3
        assert t.try_consume(WALLET, "s", ip="1.1.1.1", on=after) is True

    def test_per_ip_daily_cap_on_free_serves(self):
        t = DailyRationTracker(per_ip_daily_cap=2)
        assert t.try_consume("wallet-a", "s", ip="9.9.9.9") is True
        assert t.try_consume("wallet-b", "s", ip="9.9.9.9") is True
        # Identity rotation from one IP does not mint more free serves.
        assert t.try_consume("wallet-c", "s", ip="9.9.9.9") is False
        assert t.remaining("wallet-c", "s") == 3  # agent bucket untouched
        # Another IP is unaffected.
        assert t.try_consume("wallet-c", "s", ip="8.8.8.8") is True

    def test_peek_per_ip_cap(self):
        t = DailyRationTracker(peek_per_ip_daily_cap=2)
        assert t.try_consume_peek("9.9.9.9") is True
        assert t.try_consume_peek("9.9.9.9") is True
        assert t.try_consume_peek("9.9.9.9") is False
        assert t.try_consume_peek("8.8.8.8") is True

    def test_identity_keying_rule(self):
        assert resolve_caller_identity(wallet="0xABC") == "0xabc"
        assert resolve_caller_identity(agent_id="agent-1") == "agent:agent-1"
        assert resolve_caller_identity(ip="1.2.3.4") == "ip:1.2.3.4"
        # Wallet wins over agent id, agent id wins over IP.
        assert resolve_caller_identity(
            wallet="0xABC", agent_id="a", ip="1.1.1.1") == "0xabc"
        assert resolve_caller_identity(
            agent_id="a", ip="1.1.1.1") == "agent:a"


def test_hook_payload_is_honest_and_conditional():
    hook = hook_payload()
    assert hook["free_daily_ration_per_skill"] == 3
    assert hook["free_peek_no_wallet"] is True
    assert hook["registered_agents_can_bid_on_pool_backed_tasks"] is True
    assert hook["free_genesis_sbt_for_registered_agents"] is True
    assert hook["free_airdrop_for_registered_agents"] is True
    assert hook["token_role"] == "platform access"
    # Sponsored stake is conditional copy, never an enabled claim.
    assert "SINCOR_SPONSORED_STAKE_ENABLED" in hook["sponsored_stake"]
    assert "investment" not in hook["copy"].lower()


# ---------------------------------------------------------------------------
# A2A card + peek
# ---------------------------------------------------------------------------

class TestA2ACardHook:
    def test_card_carries_hook_and_per_skill_ration(self, a2a_client):
        payload = a2a_client.get("/.well-known/agent-card.json").get_json()
        assert payload["hook"]["free_daily_ration_per_skill"] == 3
        assert payload["hook"]["free_peek_no_wallet"] is True
        assert "free daily ration" in payload["description"]
        assert payload["skills"], "card must advertise skills"
        for skill in payload["skills"]:
            assert skill["freeDailyRation"] == 3, skill["id"]
            assert skill["peekUrl"].endswith(f"/api/a2a/peek/{skill['id']}")

    def test_agents_listing_carries_ration(self, a2a_client):
        payload = a2a_client.get("/api/a2a/agents").get_json()
        assert payload["hook"]["free_daily_ration_per_skill"] == 3
        for row in payload["agents"]:
            assert row["free_daily_ration"] == 3


class TestA2APeek:
    def test_peek_metadata_for_task_executed_skill(self, a2a_client):
        resp = a2a_client.get("/api/a2a/peek/lead-enrichment")
        assert resp.status_code == 200
        body = resp.get_json()
        # Labelled metadata, NOT a fake sample.
        assert body["sample"] is False
        assert body["peek_kind"] == "metadata"
        assert body["metadata"]["id"] == "lead-enrichment"
        assert body["metadata"]["freeDailyRation"] == 3
        assert "inputSchema" in body["metadata"]

    def test_peek_real_sample_for_toa_decision(
            self, a2a_client, tmp_path, monkeypatch):
        monkeypatch.setenv("TOA_DECISION_LOG", str(tmp_path / "toa.jsonl"))
        resp = a2a_client.get("/api/a2a/peek/toa-decision")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["sample"] is True
        assert body["peek_kind"] == "sample"
        assert body["result"]["action"] in (
            "recommend", "escalate", "refused")

    def test_peek_unknown_skill_400(self, a2a_client):
        resp = a2a_client.get("/api/a2a/peek/no-such-skill")
        assert resp.status_code == 400

    def test_peek_rate_limited_per_ip(self, a2a_client, monkeypatch):
        from sincor2.free_ration import daily_ration_tracker

        monkeypatch.setattr(
            daily_ration_tracker, "peek_per_ip_daily_cap", 2)
        assert a2a_client.get("/api/a2a/peek/lead-enrichment").status_code == 200
        assert a2a_client.get("/api/a2a/peek/lead-enrichment").status_code == 200
        resp = a2a_client.get("/api/a2a/peek/lead-enrichment")
        assert resp.status_code == 429
        assert resp.get_json()["error"] == "peek_rate_limited"


def test_a2a_quote_daily_ration_is_registered_gated(a2a_client):
    """An unregistered signed wallet gets NO daily ration on a
    non-top-5 skill — pre-Wave-1 quote behaviour is byte-preserved."""
    import time

    from eth_account import Account
    from eth_account.messages import encode_defunct

    from sincor2 import a2a_integration as ai

    acct = Account.create()
    ts = int(time.time() * 1000)
    message = ai.quota_message_for_quote("compliance-sbom", ts)
    sig = acct.sign_message(encode_defunct(text=message)).signature.hex()
    resp = a2a_client.post("/api/a2a/quote", json={
        "skill_id": "compliance-sbom",
        "signature": "0x" + sig, "quota_ts": str(ts),
        "wallet": acct.address,
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["free_daily_ration_limit"] == 3
    assert data["free_daily_ration_remaining"] == 0
    assert data["is_free"] is False


# ---------------------------------------------------------------------------
# x402 peek
# ---------------------------------------------------------------------------

class TestX402Peek:
    def test_peek_real_sample_toa(self, billing_client, x402_db,
                                  tmp_path, monkeypatch):
        monkeypatch.setenv("TOA_DECISION_LOG", str(tmp_path / "toa.jsonl"))
        resp = billing_client.get(f"/api/x402/peek/{RESOURCE}")
        assert resp.status_code == 200
        assert resp.headers.get("X-Free-Peek") == "true"
        body = resp.get_json()
        assert body["sample"] is True
        assert body["peek_kind"] == "sample"
        assert body["result"]["action"] in (
            "recommend", "escalate", "refused")

    def test_peek_via_paid_route_param(self, billing_client, x402_db,
                                       tmp_path, monkeypatch):
        monkeypatch.setenv("TOA_DECISION_LOG", str(tmp_path / "toa.jsonl"))
        resp = billing_client.get(f"/api/paid/{RESOURCE}?peek=1")
        assert resp.status_code == 200
        assert resp.get_json()["sample"] is True

    def test_peek_metadata_for_live_read_resource(
            self, billing_client, x402_db):
        resp = billing_client.get("/api/x402/peek/market-data-read")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["sample"] is False
        assert body["peek_kind"] == "metadata"
        assert body["metadata"]["id"] == "market-data-read"

    def test_peek_unknown_resource_404(self, billing_client, x402_db):
        resp = billing_client.get("/api/x402/peek/no-such-resource")
        assert resp.status_code == 404
        assert resp.get_json()["error"] == "unknown_resource"

    def test_peek_rate_limited_per_ip(self, billing_client, x402_db,
                                      monkeypatch):
        from sincor2.free_ration import daily_ration_tracker

        monkeypatch.setattr(
            daily_ration_tracker, "peek_per_ip_daily_cap", 2)
        path = "/api/x402/peek/market-data-read"
        assert billing_client.get(path).status_code == 200
        assert billing_client.get(path).status_code == 200
        resp = billing_client.get(path)
        assert resp.status_code == 429
        assert resp.get_json()["error"] == "peek_rate_limited"

    def test_resources_listing_carries_hook(self, billing_client, x402_db):
        body = billing_client.get("/api/x402/resources").get_json()
        assert body["hook"]["free_daily_ration_per_skill"] == 3
        rationed = [r for r in body["resources"]
                    if r["id"] in billing_mod.X402_FREE_RATION_RESOURCES]
        assert len(rationed) == 7
        for res in rationed:
            assert res["free_daily_ration"] == 3
            assert res["peek_url"] == f"/api/x402/peek/{res['id']}"
