"""P24 issuance wiring tests: route auth, rate-limit, policy-rejection, live-block.

Covers backlog item 10 acceptance: unauthenticated -> 401/403, over-limit
-> 429, policy-violating metadata -> 400 with the ruleset version. Happy
path exercises the full screened dry-run issuance; no broadcast happens in
tests (P24 is live-blocked).

W-38 (creator-ID squatting) acceptance: creator_id is bound to the
caller's registered agent_id (mismatch -> 403), creator IDs are screened
(empty/overlong/bad charset/reserved/impersonating -> 400), and the route
is idempotency-keyed (same key + identical request -> replay; same key +
different request -> 422).
"""

from __future__ import annotations

import uuid

import pytest
from flask import Flask

from sincor2.a2a_idempotency import reset_idempotency_store
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


@pytest.fixture
def idem_client(tmp_path, monkeypatch):
    """Test client with an isolated idempotency-key SQLite store."""
    monkeypatch.setenv("SINCOR_IDEMPOTENCY_DB_PATH",
                       str(tmp_path / "idem.db"))
    reset_fabric()
    reset_socialfi_onboarding()
    reset_idempotency_store()
    app = Flask(__name__)
    register_inbound(app)
    app.config["TESTING"] = True
    yield app.test_client()
    reset_socialfi_onboarding()
    reset_idempotency_store()


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
    # Creator-ID binding means every issuance by this agent uses its own
    # creator_id: the first succeeds, the next four hit the duplicate-
    # creator 409 but still count against the per-agent issuance limit
    # (the before_request rate check runs before the view), and the sixth
    # is rate-limited.
    agent_id = _register(client)
    first = client.post("/v1/a2a/socialfi/issue", json=_issue_payload(agent_id))
    assert first.status_code == 201, first.get_json()
    for _ in range(4):
        resp = client.post(
            "/v1/a2a/socialfi/issue", json=_issue_payload(agent_id))
        assert resp.status_code == 409
    sixth = client.post("/v1/a2a/socialfi/issue", json=_issue_payload(agent_id))
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


# ---------------------------------------------------------------------------
# W-38 creator-ID squatting hardening
# ---------------------------------------------------------------------------


def test_issue_creator_id_squat_rejected(client):
    """A caller cannot dry-run issuance under another identity's creator_id.

    Before W-38, a free-registered agent could register ANY creator_id and
    the first squatter blocked the legitimate creator with a 409. Now the
    squat attempt is rejected with 403, and the legitimate creator's own
    issuance succeeds — the squat window is closed.
    """
    victim = _register(client, "creator-victim")
    squatter = _register(client, "squatter")
    resp = client.post("/v1/a2a/socialfi/issue",
                       json=_issue_payload(squatter, creator_id=victim))
    assert resp.status_code == 403
    assert "does not belong to the caller" in resp.get_json()["error"]
    # The legitimate creator is not blocked by the attempt.
    legit = client.post("/v1/a2a/socialfi/issue",
                        json=_issue_payload(victim, creator_id=victim))
    assert legit.status_code == 201
    assert legit.get_json()["creator_id"] == victim


def test_issue_creator_id_own_binding_works(client):
    """A caller issuing under its own registered agent_id succeeds."""
    agent_id = _register(client, "binder-1")
    resp = client.post("/v1/a2a/socialfi/issue",
                       json=_issue_payload(agent_id, creator_id=agent_id))
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["creator_id"] == agent_id
    assert body["agent_id"] == agent_id


def test_issue_creator_id_omitted_defaults_to_caller(client):
    """Omitting creator_id defaults to the caller's own agent_id (201)."""
    agent_id = _register(client, "default-1")
    payload = _issue_payload(agent_id)
    assert "creator_id" not in payload
    resp = client.post("/v1/a2a/socialfi/issue", json=payload)
    assert resp.status_code == 201
    assert resp.get_json()["creator_id"] == agent_id


@pytest.mark.parametrize("bad,why", [
    ("", "explicit empty is rejected, not defaulted"),
    ("   ", "whitespace-only is rejected"),
    ("x" * 65, "overlong"),
    ("sincor", "reserved platform identity"),
    ("OFFICIAL", "reserved, case-insensitive"),
    ("official-team", "deceptive official prefix"),
    ("Sincor_Support", "deceptive platform prefix"),
    ("admin-help", "deceptive admin prefix"),
    ("evil id!", "charset: no spaces/bang"),
    ("-leading-dash", "charset: must start alphanumeric"),
    (".leading-dot", "charset: must start alphanumeric"),
    ("admïn", "charset: no unicode confusables"),
    ("a/b", "charset: no slashes"),
])
def test_issue_creator_id_screened(client, bad, why):
    agent_id = _register(client)
    resp = client.post("/v1/a2a/socialfi/issue",
                       json=_issue_payload(agent_id, creator_id=bad))
    assert resp.status_code == 400, why
    # The offending value is never echoed back.
    assert bad.strip() not in resp.get_data(as_text=True) or not bad.strip()


def test_issue_idempotent_retry_replays_original(idem_client):
    """A retried issue request replays the original 201 (no duplicates)."""
    agent_id = _register(idem_client, "idem-1")
    key = str(uuid.uuid4())
    first = idem_client.post("/v1/a2a/socialfi/issue",
                             json=_issue_payload(agent_id),
                             headers={"Idempotency-Key": key})
    assert first.status_code == 201
    assert "Idempotent-Replayed" not in first.headers
    second = idem_client.post("/v1/a2a/socialfi/issue",
                              json=_issue_payload(agent_id),
                              headers={"Idempotency-Key": key})
    assert second.status_code == 201
    assert second.headers.get("Idempotent-Replayed") == "true"
    assert second.get_json()["creator_id"] == agent_id
    # Exactly one registration happened: a fresh key with the same payload
    # now 409s on the duplicate, proving the retry executed nothing new
    # and left no fresh squat window.
    third = idem_client.post("/v1/a2a/socialfi/issue",
                             json=_issue_payload(agent_id),
                             headers={"Idempotency-Key": str(uuid.uuid4())})
    assert third.status_code == 409


def test_issue_idempotent_key_bound_to_request(idem_client):
    """Same key + different request -> 422; a key never authorizes a
    different write."""
    agent_id = _register(idem_client, "idem-2")
    key = str(uuid.uuid4())
    first = idem_client.post("/v1/a2a/socialfi/issue",
                             json=_issue_payload(agent_id, symbol="AAA"),
                             headers={"Idempotency-Key": key})
    assert first.status_code == 201
    different = idem_client.post("/v1/a2a/socialfi/issue",
                                 json=_issue_payload(agent_id, symbol="BBB"),
                                 headers={"Idempotency-Key": key})
    assert different.status_code == 422


def test_issue_live_block_still_intact(client):
    """W-38 changes nothing about the live block: dry_run=false still 403s."""
    agent_id = _register(client, "blocked-1")
    resp = client.post("/v1/a2a/socialfi/issue",
                       json=_issue_payload(agent_id, creator_id=agent_id,
                                          dry_run=False))
    assert resp.status_code == 403
    assert "live-blocked" in resp.get_json()["error"]
