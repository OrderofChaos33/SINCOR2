"""A2A registration identity proof (G2.2 / backlog item 15).

Proves that re-registering an existing agent record requires proof of
control of the registered wallet: an EIP-191 signature over the exact new
record contents plus a fresh timestamp. Unsigned, wrong-signer, stale, or
tampered re-registrations are refused with 403; first-time registration is
unchanged (no signature needed).
"""
from __future__ import annotations

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from flask import Flask

from sincor2.a2a_inbound import (
    REGISTRATION_PROOF_FRESHNESS_MS,
    _now_ms,
    build_reregistration_message,
    get_fabric,
    reset_fabric,
)
from sincor2.a2a_inbound import register as register_inbound
from sincor2.a2a_inbound_ext import register_agent_record


@pytest.fixture
def client():
    reset_fabric()
    app = Flask(__name__)
    register_inbound(app)
    app.config["TESTING"] = True
    return app.test_client()


def _body(agent_id, wallet, **extra):
    body = {
        "agent_id": agent_id,
        "name": "Proof Probe",
        "description": "registration proof test agent",
        "version": "1.0.0",
        "capability_tags": ["lead-enrichment"],
        "skills": [{"id": "lead-enrichment", "name": "Lead Enrichment"}],
        "rpc_callback": "https://agent.example/rpc",
        "wallet": wallet,
    }
    body.update(extra)
    return body


def _signed_body(key, agent_id, wallet, ts_ms=None, **extra):
    body = _body(agent_id, wallet, **extra)
    ts = _now_ms() if ts_ms is None else ts_ms
    message = build_reregistration_message(body, ts)
    sig = key.sign_message(encode_defunct(text=message)).signature
    body["registration_ts"] = ts
    body["registration_signature"] = "0x" + bytes(sig).hex()
    return body


def test_first_registration_needs_no_signature(client):
    r = client.post("/v1/a2a/register",
                    json=_body("proof-new", Account.create().address))
    assert r.status_code == 201, r.get_json()
    assert "reregistration" in r.get_json()


def test_reregistration_without_signature_is_403(client):
    key = Account.create()
    body = _body("proof-nosig", key.address)
    assert client.post("/v1/a2a/register", json=body).status_code == 201
    r = client.post("/v1/a2a/register", json=_body("proof-nosig", key.address))
    assert r.status_code == 403, r.get_json()
    assert "signature" in r.get_json()["error"].lower()


def test_reregistration_wrong_signer_is_403(client):
    owner = Account.create()
    attacker = Account.create()
    agent_id = "proof-wrongsig"
    assert client.post("/v1/a2a/register",
                       json=_body(agent_id, owner.address)).status_code == 201
    # Attacker signs a record that swaps the wallet to their own address.
    evil = _signed_body(attacker, agent_id, attacker.address,
                        rpc_callback="https://evil.example/rpc")
    r = client.post("/v1/a2a/register", json=evil)
    assert r.status_code == 403, r.get_json()
    # The record is untouched.
    assert get_fabric().agents[agent_id]["wallet"] == owner.address.lower()
    assert get_fabric().agents[agent_id]["rpc_callback"] != "https://evil.example/rpc"


def test_reregistration_with_owner_signature_succeeds(client):
    owner = Account.create()
    agent_id = "proof-ok"
    assert client.post("/v1/a2a/register",
                       json=_body(agent_id, owner.address)).status_code == 201
    new_wallet = Account.create().address
    # Owner rotates the wallet and callback, signing with the OLD wallet.
    update = _signed_body(owner, agent_id, new_wallet,
                          rpc_callback="https://agent.example/v2",
                          name="Proof Probe v2")
    r = client.post("/v1/a2a/register", json=update)
    assert r.status_code == 201, r.get_json()
    agent = get_fabric().agents[agent_id]
    assert agent["wallet"] == new_wallet.lower()
    assert agent["rpc_callback"] == "https://agent.example/v2"
    assert agent["name"] == "Proof Probe v2"


def test_reregistration_stale_timestamp_is_403(client):
    owner = Account.create()
    agent_id = "proof-stale"
    assert client.post("/v1/a2a/register",
                       json=_body(agent_id, owner.address)).status_code == 201
    stale_ts = _now_ms() - REGISTRATION_PROOF_FRESHNESS_MS - 60_000
    update = _signed_body(owner, agent_id, owner.address, ts_ms=stale_ts)
    r = client.post("/v1/a2a/register", json=update)
    assert r.status_code == 403, r.get_json()


def test_reregistration_tampered_fields_break_signature(client):
    owner = Account.create()
    agent_id = "proof-tamper"
    assert client.post("/v1/a2a/register",
                       json=_body(agent_id, owner.address)).status_code == 201
    # Sign one record, submit a different one: the signature must not verify.
    signed = _signed_body(owner, agent_id, owner.address)
    tampered = dict(signed)
    tampered["rpc_callback"] = "https://evil.example/rpc"
    r = client.post("/v1/a2a/register", json=tampered)
    assert r.status_code == 403, r.get_json()
    assert get_fabric().agents[agent_id]["rpc_callback"] == "https://agent.example/rpc"


def test_reregistration_walletless_record_is_403(client):
    agent_id = "proof-nowallet"
    body = _body(agent_id, "")
    assert client.post("/v1/a2a/register", json=body).status_code == 201
    # Nobody can prove control of a record with no bound wallet: fail closed.
    r = client.post("/v1/a2a/register", json=_body(agent_id, ""))
    assert r.status_code == 403, r.get_json()


def test_internal_platform_reseed_bypasses_proof():
    """The internal platform seed (startup path) is not subject to the
    HTTP proof gate — the route never passes _internal_reputation."""
    from sincor2.a2a_inbound import _PLATFORM_AGENT_ID
    reset_fabric()
    snap = register_agent_record(
        {"agent_id": _PLATFORM_AGENT_ID, "capability_tags": ["x"],
         "rpc_callback": "https://getsincor.com/api/a2a",
         "wallet": "0x" + "44" * 20},
        _internal_reputation=1.0)
    assert snap["agent_id"] == _PLATFORM_AGENT_ID
    # And again (re-seed on heartbeat-loop failure) — still no signature.
    snap2 = register_agent_record(
        {"agent_id": _PLATFORM_AGENT_ID, "capability_tags": ["x"],
         "rpc_callback": "https://getsincor.com/api/a2a",
         "wallet": "0x" + "44" * 20},
        _internal_reputation=1.0)
    assert snap2["agent_id"] == _PLATFORM_AGENT_ID


def test_replay_within_window_reapplies_identical_state(client):
    """A replayed proof cannot escalate: the signature binds the full
    record, so replay only re-applies the identical update."""
    owner = Account.create()
    attacker = Account.create()
    agent_id = "proof-replay"
    assert client.post("/v1/a2a/register",
                       json=_body(agent_id, owner.address)).status_code == 201
    legit = _signed_body(owner, agent_id, owner.address, name="Renamed")
    assert client.post("/v1/a2a/register", json=legit).status_code == 201
    # Attacker replays the owner's signed message verbatim: the wallet
    # cannot be redirected because it is covered by the signature.
    r = client.post("/v1/a2a/register", json=legit)
    assert r.status_code == 201, r.get_json()
    agent = get_fabric().agents[agent_id]
    assert agent["wallet"] == owner.address.lower()
    assert agent["name"] == "Renamed"
    _ = attacker  # attacker holds no usable capability in this flow
