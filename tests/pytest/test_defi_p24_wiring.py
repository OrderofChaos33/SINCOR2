"""P24 issuance wiring tests: route auth, rate-limit, policy-rejection, live-block.

Covers backlog item 10 acceptance: unauthenticated -> 401/403, over-limit
-> 429, policy-violating metadata -> 400 with the ruleset version. Happy
path exercises the full screened dry-run issuance; no broadcast happens in
tests (P24 is live-blocked).
"""

from __future__ import annotations

import pytest
from flask import Flask

from sincor2.a2a_inbound import register as register_inbound, reset_fabric
from sincor2.a2a_inbound_market import reset_socialfi_onboarding
from sincor2.a2a_sdk import FlaskTestTransport, SincorAgentSDK, SDKError
from sincor2.defi.p24.policy import RULESET_VERSION


@pytest.fixture
def client():
    reset_fabric()
    reset_socialfi_onboarding()
    app = Flask(__name__)
    register_inbound(app)
    app.config["TESTING"] = True
    yield app.test_client()
    reset_socialfi_onboarding()


def _register(client, agent_id="issuer-1"):
    resp = client.post("/v1/a2a/register", json={
        "agent_id": agent_id,
        "name": "Issuer",
        "capability_tags": ["socialfi"],
        "wallet": "0x" + "11" * 20,
        "rpc_callback": "https://issuer.example.com/rpc",
    })
    assert resp.status_code == 201
    return agent_id


def _issue_payload(agent_id, **over):
    payload = {
        "agent_id": agent_id,
        "name": "Creator Coin",
        "symbol": "CRT",
        "description": "A creator revenue-share token.",
        "bio": "Independent musician funding the next record.",
    }
    payload.update(over)
    return payload


def test_issue_unauthenticated_missing_agent_id(client):
    resp = client.post("/v1/a2a/socialfi/issue", json=_issue_payload(""))
    assert resp.status_code == 401


def test_issue_unauthenticated_unknown_agent(client):
    resp = client.post(
        "/v1/a2a/socialfi/issue", json=_issue_payload("ghost-agent"))
    assert resp.status_code == 401


def test_issue_policy_violation_returns_400_with_ruleset(client):
    agent_id = _register(client)
    resp = client.post("/v1/a2a/socialfi/issue", json=_issue_payload(
        agent_id, description="Guaranteed returns, to the moon!"))
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ruleset_version"] == RULESET_VERSION
    assert body["field"] == "description"
    assert body["matched_phrase"]  # deny-list phrase, not submitted text
    # The full submitted text must never be echoed back.
    assert "Guaranteed returns" not in resp.get_data(as_text=True)


def test_issue_rate_limited(client):
    agent_id = _register(client)
    for i in range(5):
        resp = client.post("/v1/a2a/socialfi/issue", json=_issue_payload(
            agent_id, creator_id=f"creator-{i}", symbol=f"T{i:02d}"))
        assert resp.status_code == 201, resp.get_json()
    sixth = client.post("/v1/a2a/socialfi/issue", json=_issue_payload(
        agent_id, creator_id="creator-5", symbol="T05"))
    assert sixth.status_code == 429
    assert sixth.headers.get("Retry-After") is not None
    assert sixth.get_json()["error"] == "rate_limited"


def test_issue_happy_path_dry_run(client):
    agent_id = _register(client)
    resp = client.post(
        "/v1/a2a/socialfi/issue", json=_issue_payload(agent_id))
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["status"] == "issued"
    assert body["symbol"] == "CRT"
    assert body["dry_run"] is True
    assert body["screened"] is True
    assert body["policy_version"] == RULESET_VERSION
    assert body["live_blocked"] is True
    assert body["total_supply_wei"] == str(10 ** 9 * 10 ** 18)


def test_issue_live_refused_while_live_blocked(client):
    agent_id = _register(client)
    resp = client.post("/v1/a2a/socialfi/issue", json=_issue_payload(
        agent_id, dry_run=False))
    assert resp.status_code == 403
    assert "live-blocked" in resp.get_json()["error"]


def test_issue_duplicate_creator_conflict(client):
    agent_id = _register(client)
    first = client.post(
        "/v1/a2a/socialfi/issue", json=_issue_payload(agent_id))
    assert first.status_code == 201
    second = client.post(
        "/v1/a2a/socialfi/issue", json=_issue_payload(agent_id))
    assert second.status_code == 409


def test_issue_bad_symbol_rejected(client):
    agent_id = _register(client)
    resp = client.post("/v1/a2a/socialfi/issue", json=_issue_payload(
        agent_id, symbol="not a symbol!"))
    assert resp.status_code == 400


def test_sdk_issue_creator_token_end_to_end(client):
    sdk = SincorAgentSDK(FlaskTestTransport(client))
    sdk.register("sdk-issuer", "Issuer", ["socialfi"],
                 wallet="0x" + "22" * 20,
                 rpc_callback="https://sdk-issuer.example.com/rpc")
    result = sdk.issue_creator_token(
        agent_id="sdk-issuer", name="SDK Coin", symbol="SDKC",
        description="Issued through the SDK.")
    assert result["status"] == "issued"
    assert result["symbol"] == "SDKC"
    assert result["dry_run"] is True


def test_sdk_issue_unauthenticated_raises(client):
    sdk = SincorAgentSDK(FlaskTestTransport(client))
    with pytest.raises(SDKError) as exc:
        sdk.issue_creator_token(
            agent_id="nobody", name="X", symbol="X1")
    assert exc.value.status == 401
