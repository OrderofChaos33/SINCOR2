"""Heartbeat authentication (G2.3 / backlog item 16).

``POST /v1/a2a/heartbeat`` must reject anonymous callers: it accepts either
an EIP-191 signature by the agent's registered wallet over the canonical
heartbeat message, or the operator heartbeat token. Unsigned heartbeats get
401; a signature can never mark a *different* agent alive.
"""
from __future__ import annotations

import time
import uuid

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from conftest import hb_headers
from sincor2.a2a_inbound_ext import (
    HEARTBEAT_PROOF_FRESHNESS_MS,
    build_heartbeat_message,
)

AID = "hbauth-agent"
SPOOF = "hbauth-victim"
NOWALLET = "hbauth-nowallet"


def _register(client, agent_id, wallet):
    # Use unique agent_id per registration to avoid re-registration 403
    # (re-registration requires EIP-191 signature by registered wallet)
    unique_id = f"{agent_id}-{uuid.uuid4().hex[:8]}"
    body = {
        "agent_id": unique_id,
        "capability_tags": ["auth-test"],
        "rpc_callback": "https://auth.example/rpc",
    }
    if wallet:
        body["wallet"] = wallet
    r = client.post("/v1/a2a/register", json=body)
    assert r.status_code in (200, 201), r.get_json()
    return r, unique_id


@pytest.fixture()
def authed_agent(client):
    """Registered agent holding a real key; returns (agent_id, account)."""
    acct = Account.create()
    _, unique_id = _register(client, AID, acct.address)
    return unique_id, acct


def _sig(acct, agent_id, ts):
    msg = encode_defunct(text=build_heartbeat_message(agent_id, ts))
    return acct.sign_message(msg).signature.hex()


def _beat(client, agent_id, acct=None, ts=None, headers=None, **extra):
    payload = {"agent_id": agent_id, **extra}
    if acct is not None:
        ts = int(time.time() * 1000) if ts is None else ts
        payload["signature"] = _sig(acct, agent_id, ts)
        payload["ts"] = ts
    return client.post("/v1/a2a/heartbeat", json=payload, headers=headers or {})


# -- the 401 matrix -----------------------------------------------------------

def test_unsigned_heartbeat_rejected(client, authed_agent):
    agent_id, _ = authed_agent
    r = client.post("/v1/a2a/heartbeat", json={"agent_id": agent_id})
    assert r.status_code == 401, r.status_code


def test_missing_agent_id_is_400(client):
    r = client.post("/v1/a2a/heartbeat", json={}, headers=hb_headers())
    assert r.status_code == 400


def test_garbage_signature_rejected(client, authed_agent):
    agent_id, _ = authed_agent
    r = _beat(client, agent_id, signature="0xdeadbeef", ts=int(time.time() * 1000))
    assert r.status_code == 401


def test_stale_timestamp_rejected(client, authed_agent):
    agent_id, acct = authed_agent
    stale = int(time.time() * 1000) - HEARTBEAT_PROOF_FRESHNESS_MS - 60_000
    r = _beat(client, agent_id, acct=acct, ts=stale)
    assert r.status_code == 401


def test_future_timestamp_rejected(client, authed_agent):
    agent_id, acct = authed_agent
    future = int(time.time() * 1000) + HEARTBEAT_PROOF_FRESHNESS_MS + 60_000
    r = _beat(client, agent_id, acct=acct, ts=future)
    assert r.status_code == 401


def test_non_integer_timestamp_rejected(client, authed_agent):
    agent_id, acct = authed_agent
    r = _beat(client, agent_id, signature=_sig(acct, agent_id, 123), ts="not-a-number")
    assert r.status_code == 401


def test_wrong_wallet_signature_rejected(client, authed_agent):
    agent_id, _ = authed_agent
    stranger = Account.create()
    r = _beat(client, agent_id, acct=stranger)
    assert r.status_code == 401


def test_spoofed_agent_id_liveness_impossible(client, authed_agent):
    """Attacker registered under their own agent_id/wallet cannot mark the
    victim alive: the signature binds the victim's agent_id but recovers to
    the attacker's wallet, which != the victim's registered wallet."""
    victim_id, victim_acct = authed_agent
    attacker = Account.create()
    _register(client, "hbauth-attacker", attacker.address)
    ts = int(time.time() * 1000)
    # Attacker signs the *victim's* heartbeat message with their own key.
    sig = _sig(attacker, victim_id, ts)
    r = client.post("/v1/a2a/heartbeat",
                    json={"agent_id": victim_id, "signature": sig, "ts": ts})
    assert r.status_code == 401
    # And the victim's own key still works.
    r = _beat(client, victim_id, acct=victim_acct)
    assert r.status_code == 200


def test_walletless_agent_cannot_self_authenticate(client):
    _, unique_id = _register(client, NOWALLET, None)
    acct = Account.create()
    r = _beat(client, unique_id, acct=acct)
    assert r.status_code == 401  # fail closed: no bound identity to prove


def test_wrong_operator_token_rejected(client, authed_agent):
    agent_id, _ = authed_agent
    r = client.post("/v1/a2a/heartbeat", json={"agent_id": agent_id},
                    headers={"X-Sincor-Heartbeat": "wrong-token"})
    assert r.status_code == 401


# -- the happy paths ----------------------------------------------------------

def test_valid_wallet_signature_accepted(client, authed_agent):
    from sincor2.a2a_inbound import get_fabric
    agent_id, acct = authed_agent
    before = get_fabric().agents[agent_id].get("last_heartbeat")
    r = _beat(client, agent_id, acct=acct)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["ok"] is True and body["agent_id"] == agent_id
    assert get_fabric().agents[agent_id]["last_heartbeat"] >= (before or 0)


def test_operator_token_accepted(client, authed_agent):
    agent_id, _ = authed_agent
    r = client.post("/v1/a2a/heartbeat", json={"agent_id": agent_id},
                    headers=hb_headers())
    assert r.status_code == 200, r.get_json()


def test_operator_token_unknown_agent_is_404(client):
    r = client.post("/v1/a2a/heartbeat", json={"agent_id": "hbauth-ghost"},
                    headers=hb_headers())
    assert r.status_code == 404


def test_bearer_authorization_header_accepted(client, authed_agent, monkeypatch):
    import os
    agent_id, _ = authed_agent
    token = os.environ["AGENT_HEARTBEAT_TOKEN"]
    r = client.post("/v1/a2a/heartbeat", json={"agent_id": agent_id},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_sdk_heartbeat_with_signer(client, authed_agent):
    from sincor2.a2a_sdk import FlaskTestTransport, SincorAgentSDK
    agent_id, acct = authed_agent
    sdk = SincorAgentSDK(FlaskTestTransport(client))
    out = sdk.heartbeat(agent_id, signer=acct)
    assert out["ok"] is True


def test_sdk_heartbeat_unsigned_is_401(client, authed_agent):
    from sincor2.a2a_sdk import SDKError, FlaskTestTransport, SincorAgentSDK
    agent_id, _ = authed_agent
    sdk = SincorAgentSDK(FlaskTestTransport(client))
    with pytest.raises(SDKError) as exc:
        sdk.heartbeat(agent_id)
    assert exc.value.status == 401
