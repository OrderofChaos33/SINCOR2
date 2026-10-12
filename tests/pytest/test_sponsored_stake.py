"""Sponsored stake: front/recoup ledger correctness + admin gating.

Covers: default-off (no fronting occurs), front -> earnings -> recoup ->
settled, partial recoup across multiple earnings, no double-front, admin
key gating, and the end-to-end recoup hook on proof settlement.
"""
from __future__ import annotations

from conftest import hb_headers

import os

import pytest
from flask import Flask

from sincor2.a2a_inbound import _now_ms, get_fabric
from sincor2.a2a_inbound import register as register_inbound
from sincor2.sponsored_stake import (
    ENABLE_ENV,
    SponsoredStakeDisabled,
    front_sponsored_stake,
    recoup_sponsored_stake,
    reset_sponsored_ledger,
    sponsored_ledger,
    sponsored_stake_enabled,
)

AGENT = "sponsored-agent-1"
TAGS = ["lead-enrichment"]


def _poster_auth():
    """Signed poster-auth fields: the anti-gaming guards reject bids on
    anonymous (unsigned) bounty tasks, so the post must bind a poster
    wallet (distinct from the bidding agent)."""
    import time

    from eth_account import Account
    from eth_account.messages import encode_defunct

    from sincor2.a2a_sdk import SincorAgentSDK

    poster_key = Account.create()
    return SincorAgentSDK.make_poster_auth(
        "sponsored-poster", "lead-enrichment", int(time.time()),
        lambda m: "0x" + poster_key.sign_message(
            encode_defunct(text=m)).signature.hex())
WALLET = "0x" + "33" * 20
AXM = 10**18


@pytest.fixture
def ledgers(tmp_path, monkeypatch):
    from sincor2.a2a_inbound import reset_fabric
    from sincor2.onchain.stake_ledger import reset_stake_ledger

    reset_fabric()
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    ledger = reset_sponsored_ledger(path=str(tmp_path / "sponsored.json"))
    monkeypatch.delenv(ENABLE_ENV, raising=False)
    return ledger


@pytest.fixture
def client(ledgers):
    app = Flask(__name__)
    register_inbound(app)
    app.config["TESTING"] = True
    return app.test_client()


def _register(client, agent_id=AGENT, key=None):
    body = {"agent_id": agent_id, "capability_tags": TAGS,
            "rpc_callback": "https://sponsored.example/rpc"}
    if key is not None:
        # Signed first-time registration: binds a wallet so the agent can
        # satisfy the W-77 sealed-bid auth gate.
        import time as _time

        from eth_account.messages import encode_defunct

        from sincor2.a2a_identity import register_message
        ts = int(_time.time() * 1000)
        body["wallet"] = key.address
        body["registration_wallet"] = key.address
        body["registration_ts"] = ts
        body["registration_signature"] = "0x" + key.sign_message(
            encode_defunct(text=register_message(agent_id, ts))).signature.hex()
    r = client.post("/v1/a2a/register", json=body)
    assert r.status_code == 201
    r = client.post("/v1/a2a/heartbeat", json={"agent_id": agent_id}, headers=hb_headers())
    assert r.status_code == 200


def _enable(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")
    assert sponsored_stake_enabled() is True


# -- default-off ------------------------------------------------------------

def test_disabled_by_default(ledgers):
    assert sponsored_stake_enabled() is False
    with pytest.raises(SponsoredStakeDisabled):
        front_sponsored_stake(AGENT, 2 * AXM)


def test_front_route_403_while_disabled(client):
    _register(client)
    r = client.post(
        "/v1/a2a/admin/sponsored-stake",
        headers={"X-Admin-Key": os.environ["ADMIN_PASSWORD"]},
        json={"agent_id": AGENT, "amount_axm": 2.0})
    assert r.status_code == 403
    assert "disabled" in r.get_json()["error"]


# -- front -------------------------------------------------------------------

def test_front_deposits_stake_and_records(ledgers, monkeypatch):
    from sincor2.onchain.stake_ledger import stake_ledger

    _enable(monkeypatch)
    rec = front_sponsored_stake(AGENT, 2 * AXM, approved_by="genesis-op")
    assert rec["status"] == "fronted"
    assert rec["fronted_wei"] == str(2 * AXM)
    assert rec["approved_by"] == "genesis-op"
    # The fronted stake is immediately usable in the stake ledger.
    bal = stake_ledger().balance_of(AGENT)
    assert bal["available_wei"] == str(2 * AXM)


def test_no_double_front_while_active(ledgers, monkeypatch):
    _enable(monkeypatch)
    front_sponsored_stake(AGENT, 2 * AXM)
    with pytest.raises(RuntimeError):
        front_sponsored_stake(AGENT, 1 * AXM)


def test_front_persists_to_disk(ledgers, monkeypatch, tmp_path):
    _enable(monkeypatch)
    front_sponsored_stake(AGENT, 2 * AXM)
    fresh = sponsored_ledger(path=str(tmp_path / "sponsored.json"))
    assert fresh.outstanding_wei(AGENT) == 2 * AXM


# -- recoup ------------------------------------------------------------------

def test_full_recoup_settles(ledgers, monkeypatch):
    _enable(monkeypatch)
    front_sponsored_stake(AGENT, 2 * AXM)
    out = recoup_sponsored_stake(AGENT, 2 * AXM, task_id="tsk_x")
    assert out == {"agent_id": AGENT, "recouped_wei": str(2 * AXM),
                   "outstanding_wei": "0", "status": "settled"}
    assert sponsored_ledger().status_of(AGENT)["settled_at"]


def test_partial_recoup_then_settle(ledgers, monkeypatch):
    _enable(monkeypatch)
    front_sponsored_stake(AGENT, 2 * AXM)
    first = recoup_sponsored_stake(AGENT, int(0.5 * AXM), task_id="tsk_a")
    assert first["status"] == "recouping"
    assert first["outstanding_wei"] == str(int(1.5 * AXM))
    second = recoup_sponsored_stake(AGENT, 2 * AXM, task_id="tsk_b")
    assert second["recouped_wei"] == str(int(1.5 * AXM))  # capped at outstanding
    assert second["outstanding_wei"] == "0"
    assert second["status"] == "settled"


def test_recoup_without_sponsorship_is_noop(ledgers):
    out = recoup_sponsored_stake(AGENT, 5 * AXM)
    assert out["recouped_wei"] == "0"
    assert out["outstanding_wei"] == "0"


def test_recoup_runs_even_after_disabling(ledgers, monkeypatch):
    """Disabling stops new fronts; it never forgives outstanding ones."""
    _enable(monkeypatch)
    front_sponsored_stake(AGENT, 2 * AXM)
    monkeypatch.delenv(ENABLE_ENV)
    assert sponsored_stake_enabled() is False
    out = recoup_sponsored_stake(AGENT, 2 * AXM)
    assert out["status"] == "settled"


# -- admin routes --------------------------------------------------------------

def test_admin_route_gating(client, monkeypatch):
    _register(client)
    _enable(monkeypatch)
    url = "/v1/a2a/admin/sponsored-stake"
    good = {"X-Admin-Key": os.environ["ADMIN_PASSWORD"]}

    r = client.post(url, json={"agent_id": AGENT, "amount_axm": 2.0})
    assert r.status_code == 401
    r = client.post(url, headers={"X-Admin-Key": "wrong"},
                    json={"agent_id": AGENT, "amount_axm": 2.0})
    assert r.status_code == 401
    r = client.post(url, headers=good,
                    json={"agent_id": "ghost-agent", "amount_axm": 2.0})
    assert r.status_code == 404
    r = client.post(url, headers=good,
                    json={"agent_id": AGENT, "amount_axm": 2.0})
    assert r.status_code == 201
    body = r.get_json()
    assert body["status"] == "fronted"
    assert body["fronted_wei"] == str(2 * AXM)
    # Second front while active -> 409.
    r = client.post(url, headers=good,
                    json={"agent_id": AGENT, "amount_axm": 1.0})
    assert r.status_code == 409

    r = client.get(f"{url}/{AGENT}", headers=good)
    assert r.status_code == 200
    assert r.get_json()["agent_id"] == AGENT
    r = client.get(f"{url}/{AGENT}")
    assert r.status_code == 401


# -- end-to-end: sponsored agent works the auction, earnings recoup -----------

def test_end_to_end_sponsored_auction_recoups(client, monkeypatch):
    """A genesis agent with zero self-funding: platform fronts the stake,
    the agent commits/reveals/wins, and the staged payout recoups the
    front automatically."""
    from sincor2.a2a_sdk import FlaskTestTransport, SincorAgentSDK

    _enable(monkeypatch)
    from eth_account import Account
    agent_key = Account.create()
    _register(client, key=agent_key)
    sdk = SincorAgentSDK(FlaskTestTransport(client), signer=agent_key)
    admin = {"X-Admin-Key": os.environ["ADMIN_PASSWORD"]}

    r = client.post("/v1/a2a/admin/sponsored-stake", headers=admin,
                    json={"agent_id": AGENT, "amount_axm": 2.0})
    assert r.status_code == 201

    task = sdk.post_task("lead-enrichment", TAGS, 1.5, sealed=True,
                         **_poster_auth())
    task_id = task["task_id"]
    bid = sdk.sealed_commit(task_id, AGENT, 0.9)  # works: fronted stake
    get_fabric().tasks[task_id]["commit_deadline"] = _now_ms() - 1000
    sdk.heartbeat(AGENT, heartbeat_token=os.environ["AGENT_HEARTBEAT_TOKEN"])
    sdk.sealed_reveal(bid)
    get_fabric().tasks[task_id]["reveal_deadline"] = _now_ms() - 1000
    closed = sdk.close_auction(task_id)
    assert closed["assigned_to"] == AGENT

    proof = sdk.submit_proof(task_id, AGENT, "0xdeadbeef1234567890")
    assert proof["status"] == "paid"

    # Earnings were 0.9 AXM < 2.0 fronted -> partial recoup.
    rec = sponsored_ledger().status_of(AGENT)
    assert rec["status"] == "recouping"
    assert rec["recouped_wei"] == str(int(0.9 * AXM))
    # Integer math in the ledger: 2.0 - 0.9 = 1.1 AXM exactly (no floats).
    assert sponsored_ledger().outstanding_wei(AGENT) == 2 * AXM - int(0.9 * AXM)

    # Second settlement clears the rest.
    out = recoup_sponsored_stake(AGENT, int(1.1 * AXM), task_id="tsk_next")
    assert out["status"] == "settled"
