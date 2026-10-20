"""Integration tests: A2A rate-limit policies enforced on the real routes.

Policies live in sincor2.a2a_rate_limits (the executable spec); this suite
proves they are actually wired into the Flask blueprints via the
before_request enforcer.

Tiers under test:
  register: 5/hour + 20/day per IP
  bid:      30/min + 300/hour per agent_id (commit/reveal/legacy bid/deposit)
  quote:    60/min + 2,000/hour per IP
  dispute:  5/hour + 20/day per IP
  read:     120/min + 5,000/hour per IP

The enforcer counts requests before view validation, so invalid bodies
still consume the bucket (standard abuse-prevention behavior) — the
tests below hammer with cheap invalid payloads and assert on the 429
boundary rather than on business-logic outcomes.
"""

from __future__ import annotations

import pytest
from flask import Flask

from conftest import hb_headers
from sincor2.a2a_inbound import get_fabric, reset_fabric
from sincor2.a2a_inbound import register as register_inbound
from sincor2.a2a_rate_limits import reset_a2a_limits
from sincor2.onchain.stake_ledger import reset_stake_ledger


@pytest.fixture
def client(tmp_path):
    reset_fabric()
    reset_a2a_limits()
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    app = Flask(__name__)
    register_inbound(app)
    # Quote endpoints live on the separate A2A discovery blueprint.
    from sincor2.a2a_integration import A2ARouter
    app.register_blueprint(A2ARouter().blueprint)
    app.config["TESTING"] = True
    return app.test_client()


def _register(client, agent_id, expect=201):
    r = client.post(
        "/v1/a2a/register",
        json={"agent_id": agent_id, "capability_tags": ["t"]},
    )
    assert r.status_code == expect, r.get_json()
    return r


# ---------------------------------------------------------------------------
# register tier: 5/hour per IP
# ---------------------------------------------------------------------------

def test_register_tier_429_with_retry_after(client):
    for i in range(5):
        _register(client, f"rl-reg-{i}")
    r = client.post("/v1/a2a/register", json={"agent_id": "rl-reg-5"})
    assert r.status_code == 429, r.status_code
    body = r.get_json()
    assert body["error"] == "rate_limited"
    assert body["policy"] == "register"
    assert body["retry_after"] > 0
    assert r.headers.get("Retry-After") == str(body["retry_after"])


# ---------------------------------------------------------------------------
# bid tier: 30/min per agent_id
# ---------------------------------------------------------------------------

def test_bid_tier_429_with_retry_after(client):
    _register(client, "rl-bidder")
    statuses = set()
    for _ in range(30):
        r = client.post(
            "/v1/a2a/bids",
            json={"task_id": "nope", "agent_id": "rl-bidder", "bid_axm": 1.0},
        )
        statuses.add(r.status_code)
    assert statuses <= {400, 404}, statuses  # rejected, but under the limit
    r = client.post(
        "/v1/a2a/bids",
        json={"task_id": "nope", "agent_id": "rl-bidder", "bid_axm": 1.0},
    )
    assert r.status_code == 429
    body = r.get_json()
    assert body["error"] == "rate_limited" and body["policy"] == "bid"
    assert r.headers.get("Retry-After")


def test_bid_tier_keyed_per_agent_not_per_ip(client):
    # Exhaust agent A's bucket; agent B (same IP) must be unaffected.
    _register(client, "rl-bid-a")
    for _ in range(30):
        client.post(
            "/v1/a2a/bids/commit",
            json={"task_id": "nope", "agent_id": "rl-bid-a",
                  "commitment": "0x" + "ab" * 32},
        )
    r = client.post(
        "/v1/a2a/bids/commit",
        json={"task_id": "nope", "agent_id": "rl-bid-a",
              "commitment": "0x" + "ab" * 32},
    )
    assert r.status_code == 429
    r = client.post(
        "/v1/a2a/bids/commit",
        json={"task_id": "nope", "agent_id": "rl-bid-b",
              "commitment": "0x" + "ab" * 32},
    )
    assert r.status_code != 429, r.status_code


def test_stake_deposit_uses_bid_tier_per_agent(client):
    _register(client, "rl-depositor")
    for _ in range(30):
        r = client.post(
            "/v1/a2a/stake/deposit",
            # Unsigned deposits are rejected (403) by the identity-bound
            # deposit endpoint; the rate limit applies regardless.
            json={"agent_id": "rl-depositor", "amount_axm": 0.01},
        )
        assert r.status_code in (403, 400), r.status_code
    r = client.post(
        "/v1/a2a/stake/deposit",
        json={"agent_id": "rl-depositor", "amount_axm": 0.01},
    )
    assert r.status_code == 429
    assert r.get_json()["policy"] == "bid"


# ---------------------------------------------------------------------------
# read tier: 120/min per IP
# ---------------------------------------------------------------------------

def test_read_tier_429_past_limit(client):
    for _ in range(120):
        r = client.get("/v1/a2a/agents")
        assert r.status_code == 200, r.status_code
    r = client.get("/v1/a2a/agents")
    assert r.status_code == 429
    assert r.get_json()["policy"] == "read"
    assert r.headers.get("Retry-After")


# ---------------------------------------------------------------------------
# quote tier: 60/min per IP
# ---------------------------------------------------------------------------

def test_quote_tier_429(client):
    for _ in range(60):
        r = client.get("/api/a2a/quote", query_string={"skill_id": "bogus"})
        assert r.status_code == 400, r.status_code  # unknown skill, under limit
    r = client.get("/api/a2a/quote", query_string={"skill_id": "bogus"})
    assert r.status_code == 429
    assert r.get_json()["policy"] == "quote"


# ---------------------------------------------------------------------------
# dispute tier: 5/hour per IP
# ---------------------------------------------------------------------------

def test_dispute_tier_429(client):
    for _ in range(5):
        r = client.post("/v1/a2a/disputes", json={"task_id": "x"})
        assert r.status_code in (400, 403, 503), r.status_code
    r = client.post("/v1/a2a/disputes", json={"task_id": "x"})
    assert r.status_code == 429
    assert r.get_json()["policy"] == "dispute"


# ---------------------------------------------------------------------------
# unmapped routes are not limited
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# heartbeat + proofs are now mapped (wave 16, G2.9) — hammering them must
# 429 at the tier boundary, never leak business logic
# ---------------------------------------------------------------------------

def test_heartbeat_tier_wired_429(client):
    # Heartbeat route requires operator auth (heartbeat-auth, G2.3); the
    # limiter counts requests before view validation, so authenticated
    # ghost-agent posts exercise the tier boundary the same way.
    for _ in range(20):
        r = client.post("/v1/a2a/heartbeat", json={"agent_id": "ghost"}, headers=hb_headers())
        assert r.status_code == 404, r.status_code
    r = client.post("/v1/a2a/heartbeat", json={"agent_id": "ghost"}, headers=hb_headers())
    assert r.status_code == 429
    assert r.get_json()["policy"] == "heartbeat"


def test_proofs_tier_wired_429(client):
    for _ in range(20):
        r = client.post(
            "/v1/a2a/proofs",
            json={"task_id": "t", "agent_id": "a", "receipt_hash": "h"},
        )
        assert r.status_code != 429, r.status_code
    r = client.post(
        "/v1/a2a/proofs",
        json={"task_id": "t", "agent_id": "a", "receipt_hash": "h"},
    )
    assert r.status_code == 429
    assert r.get_json()["policy"] == "task_write"


def test_unmapped_routes_not_limited(client):
    # Discovery/docs stay public and unlimited.
    for _ in range(10):
        r = client.get("/.well-known/agent-card.json")
        assert r.status_code != 429, r.status_code
