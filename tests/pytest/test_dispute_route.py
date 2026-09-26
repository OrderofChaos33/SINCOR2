"""Authenticated dispute route: POST /v1/a2a/disputes.

The adjudicator EIP-191-signs the ruling; the route recovers the signer
and requires it to equal SINCOR_ADJUDICATOR_ID. upheld=true slashes 50 %,
upheld=false releases the winner's locked stake.
"""

from __future__ import annotations

import os

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from flask import Flask

from sincor2.a2a_inbound import _now_ms, get_fabric, reset_fabric
from sincor2.a2a_inbound import register as register_inbound
from sincor2.a2a_inbound_market import (
    _dispute_message,
    sealed_commitment,
)
from sincor2.onchain.stake_ledger import (
    ADJUDICATOR_ENV,
    reset_stake_ledger,
    stake_ledger,
)

ONE_AXM = 10**18
BOUNTY = 1.5


@pytest.fixture
def client(tmp_path):
    reset_fabric()
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    app = Flask(__name__)
    register_inbound(app)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def adjudicator():
    return Account.create()


@pytest.fixture(autouse=True)
def _adjudicator_env(monkeypatch, adjudicator):
    monkeypatch.setenv(ADJUDICATOR_ENV, adjudicator.address)


def _register(client, agent_id, stake_axm=10):
    r = client.post("/v1/a2a/register", json={
        "agent_id": agent_id, "capability_tags": ["lead-enrichment"],
        "rpc_callback": "https://agent.example/rpc",
        "wallet": "0x" + "11" * 20})
    assert r.status_code in (200, 201), r.get_json()
    if stake_axm:
        stake_ledger().deposit(agent_id, int(stake_axm * ONE_AXM))


def _sealed_task(client):
    r = client.post("/v1/a2a/tasks", json={
        "skill": "lead-enrichment", "tags": ["lead-enrichment"],
        "bounty_axm": BOUNTY, "sealed": True, "poster_id": "poster-p"})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["task_id"]


def _commitment(bid_axm, nonce_hex, agent_id):
    price_wei = int(round(float(bid_axm) * 1e18))
    return "0x" + sealed_commitment(price_wei, bytes.fromhex(nonce_hex),
                                    agent_id).hex()


def _run_auction_to_winner(client, agent_id="winner-a"):
    """Commit -> reveal -> close; returns (task_id, locked_wei)."""
    _register(client, agent_id)
    task_id = _sealed_task(client)
    r = client.post("/v1/a2a/bids/commit", json={
        "task_id": task_id, "agent_id": agent_id,
        "commitment": _commitment(1.0, "aa" * 32, agent_id)})
    assert r.status_code == 201, r.get_json()
    get_fabric().tasks[task_id]["commit_deadline"] = _now_ms() - 1000
    r = client.post("/v1/a2a/bids/reveal", json={
        "task_id": task_id, "agent_id": agent_id, "bid_axm": 1.0,
        "nonce": "aa" * 32, "estimated_seconds": 300})
    assert r.status_code == 201, r.get_json()
    get_fabric().tasks[task_id]["reveal_deadline"] = _now_ms() - 1
    r = client.post(f"/v1/a2a/tasks/{task_id}/close")
    assert r.status_code == 200, r.get_json()
    locked = int(stake_ledger().balance_of(agent_id)["locked_wei"])
    assert locked > 0
    return task_id, locked


def _signed_dispute(key, task_id, agent_id, upheld,
                    expires_at_ms=None):
    expires = (expires_at_ms if expires_at_ms is not None
               else _now_ms() + 60_000)
    message = _dispute_message(task_id, agent_id, upheld, key.address,
                               expires)
    sig = Account.sign_message(encode_defunct(text=message),
                               private_key=key.key).signature.hex()
    return {
        "task_id": task_id, "agent_id": agent_id, "upheld": upheld,
        "adjudicator_address": key.address, "expires_at_ms": expires,
        "signature": "0x" + sig,
    }


def test_dispute_upheld_slashes_half(client, adjudicator):
    task_id, locked = _run_auction_to_winner(client)
    body = _signed_dispute(adjudicator, task_id, "winner-a", True)
    r = client.post("/v1/a2a/disputes", json=body)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["slashed_wei"] == str(locked // 2)


def test_dispute_rejected_releases_stake(client, adjudicator):
    task_id, locked = _run_auction_to_winner(client)
    body = _signed_dispute(adjudicator, task_id, "winner-a", False)
    r = client.post("/v1/a2a/disputes", json=body)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["upheld"] is False
    assert stake_ledger().balance_of("winner-a")["locked_wei"] == "0"


def test_dispute_wrong_signer_rejected(client, adjudicator):
    task_id, _ = _run_auction_to_winner(client)
    impostor = Account.create()
    body = _signed_dispute(impostor, task_id, "winner-a", True)
    # Claim the adjudicator's address but sign with the impostor key.
    body["adjudicator_address"] = adjudicator.address
    r = client.post("/v1/a2a/disputes", json=body)
    assert r.status_code == 403


def test_dispute_tampered_field_rejected(client, adjudicator):
    task_id, _ = _run_auction_to_winner(client)
    body = _signed_dispute(adjudicator, task_id, "winner-a", False)
    body["upheld"] = True  # flip after signing
    r = client.post("/v1/a2a/disputes", json=body)
    assert r.status_code == 403


def test_dispute_expired_rejected(client, adjudicator):
    task_id, _ = _run_auction_to_winner(client)
    body = _signed_dispute(adjudicator, task_id, "winner-a", True,
                           expires_at_ms=_now_ms() - 1000)
    r = client.post("/v1/a2a/disputes", json=body)
    assert r.status_code == 403


def test_dispute_far_future_rejected(client, adjudicator):
    task_id, _ = _run_auction_to_winner(client)
    body = _signed_dispute(adjudicator, task_id, "winner-a", True,
                           expires_at_ms=_now_ms() + 60 * 60 * 1000)
    r = client.post("/v1/a2a/disputes", json=body)
    assert r.status_code == 400


def test_dispute_missing_fields_rejected(client, adjudicator):
    task_id, _ = _run_auction_to_winner(client)
    body = _signed_dispute(adjudicator, task_id, "winner-a", True)
    del body["signature"]
    r = client.post("/v1/a2a/disputes", json=body)
    assert r.status_code == 400


def test_dispute_unknown_task_rejected(client, adjudicator):
    body = _signed_dispute(adjudicator, "no-such-task", "winner-a", True)
    r = client.post("/v1/a2a/disputes", json=body)
    assert r.status_code == 404


def test_dispute_no_adjudicator_configured(client, adjudicator, monkeypatch):
    monkeypatch.delenv(ADJUDICATOR_ENV, raising=False)
    task_id, _ = _run_auction_to_winner(client)
    body = _signed_dispute(adjudicator, task_id, "winner-a", True)
    r = client.post("/v1/a2a/disputes", json=body)
    assert r.status_code == 503
