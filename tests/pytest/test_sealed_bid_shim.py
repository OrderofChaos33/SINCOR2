"""Sealed-bid commit/reveal shim — commit -> reveal -> close lifecycle."""
from __future__ import annotations

import pytest
from flask import Flask

from sincor2.a2a_inbound import _now_ms, get_fabric
from sincor2.a2a_inbound import register as register_inbound
from sincor2.a2a_inbound_market import sealed_commitment
from sincor2.contract_net import calculate_bid_score


@pytest.fixture
def client(tmp_path):
    from sincor2.a2a_inbound import reset_fabric
    from sincor2.onchain.stake_ledger import reset_stake_ledger

    reset_fabric()
    # Stake enforcement is active: each test gets an isolated ledger.
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    app = Flask(__name__)
    register_inbound(app)
    app.config["TESTING"] = True
    return app.test_client()


def _register(client, agent_id):
    from sincor2.onchain.stake_ledger import stake_ledger

    r = client.post(
        "/v1/a2a/register",
        json={
            "agent_id": agent_id,
            "capability_tags": ["lead-enrichment"],
            "rpc_callback": "https://agent.example/rpc",
            "wallet": "0x" + "11" * 20,
        },
    )
    assert r.status_code in (200, 201), r.get_json()
    r = client.post("/v1/a2a/heartbeat", json={"agent_id": agent_id})
    assert r.status_code == 200
    # Fund the agent so the commit-time stake lock (bounty * 50 %) succeeds.
    stake_ledger().deposit(agent_id, 10 * 10**18)


def _sealed_task(client, skill="lead-enrichment"):
    r = client.post(
        "/v1/a2a/tasks",
        json={"skill": skill, "tags": ["lead-enrichment"], "bounty_axm": 1.5, "sealed": True},
    )
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["sealed"] is True
    assert body["commit_deadline"] and body["reveal_deadline"]
    assert body["reveal_deadline"] - body["commit_deadline"] == 5 * 60 * 1000
    return body["task_id"]


def _commitment(bid_axm, nonce_hex, agent_id):
    price_wei = int(round(float(bid_axm) * 1e18))
    salt = bytes.fromhex(nonce_hex)
    return "0x" + sealed_commitment(price_wei, salt, agent_id).hex()


def _commit(client, task_id, agent_id, bid_axm, nonce_hex):
    return client.post(
        "/v1/a2a/bids/commit",
        json={"task_id": task_id, "agent_id": agent_id,
              "commitment": _commitment(bid_axm, nonce_hex, agent_id)},
    )


def _reveal(client, task_id, agent_id, bid_axm, nonce_hex, est=300):
    return client.post(
        "/v1/a2a/bids/reveal",
        json={"task_id": task_id, "agent_id": agent_id, "bid_axm": bid_axm,
              "nonce": nonce_hex, "estimated_seconds": est},
    )


def _force_reveal_window(task_id):
    # Move into the reveal window: commit phase over, reveal phase open.
    fabric = get_fabric()
    fabric.tasks[task_id]["commit_deadline"] = _now_ms() - 1000


def _force_past_reveal_deadline(task_id):
    fabric = get_fabric()
    fabric.tasks[task_id]["reveal_deadline"] = _now_ms() - 1


def test_commitment_scheme_matches_contract_encoding():
    # Pins the exact CommitRevealAuction preimage:
    # keccak256(abi.encodePacked(bytes32(price), salt, keccak256(agent_id))).
    from eth_hash.auto import keccak

    price_wei = int(1.5 * 1e18)
    salt = bytes.fromhex("ab" * 32)
    agent_id = "seal-agent-1"
    expected = keccak(price_wei.to_bytes(32, "big") + salt + keccak(agent_id.encode()))
    assert sealed_commitment(price_wei, salt, agent_id) == expected


def test_commit_reveal_close_assigns_winner(client):
    _register(client, "seal-a")
    _register(client, "seal-b")
    task_id = _sealed_task(client)
    nonce_a, nonce_b = "aa" * 32, "bb" * 32

    assert _commit(client, task_id, "seal-a", 1.0, nonce_a).status_code == 201
    assert _commit(client, task_id, "seal-b", 2.0, nonce_b).status_code == 201

    # Reveal is rejected during the commit window (mirrors RevealTooEarly).
    assert _reveal(client, task_id, "seal-a", 1.0, nonce_a).status_code == 403

    _force_reveal_window(task_id)
    assert _reveal(client, task_id, "seal-a", 1.0, nonce_a).status_code == 201
    assert _reveal(client, task_id, "seal-b", 2.0, nonce_b).status_code == 201

    _force_past_reveal_deadline(task_id)
    closed = client.post(f"/v1/a2a/tasks/{task_id}/close")
    assert closed.status_code == 200
    body = closed.get_json()
    assert body["state"] == "assigned"
    # Cheaper bid wins on composite score (both rep 0, same time est).
    assert body["assigned_to"] == "seal-a"
    assert body["winning_bid_axm"] == 1.0
    assert body["ghosted_commits"] == 0


def test_unrevealed_commit_ignored_at_close(client):
    _register(client, "seal-a")
    _register(client, "ghost-b")
    task_id = _sealed_task(client)

    assert _commit(client, task_id, "seal-a", 1.0, "aa" * 32).status_code == 201
    assert _commit(client, task_id, "ghost-b", 0.5, "bb" * 32).status_code == 201

    _force_reveal_window(task_id)
    # Only seal-a reveals; ghost-b's cheaper commitment is dropped.
    assert _reveal(client, task_id, "seal-a", 1.0, "aa" * 32).status_code == 201

    _force_past_reveal_deadline(task_id)
    body = client.post(f"/v1/a2a/tasks/{task_id}/close").get_json()
    assert body["state"] == "assigned"
    assert body["assigned_to"] == "seal-a"
    assert body["ghosted_commits"] == 1


def test_wrong_nonce_rejected(client):
    _register(client, "seal-a")
    task_id = _sealed_task(client)
    assert _commit(client, task_id, "seal-a", 1.0, "aa" * 32).status_code == 201
    _force_reveal_window(task_id)

    r = _reveal(client, task_id, "seal-a", 1.0, "ff" * 32)
    assert r.status_code == 400
    assert "mismatch" in r.get_json()["error"]

    # Wrong bid amount also fails the commitment check.
    r = _reveal(client, task_id, "seal-a", 2.0, "aa" * 32)
    assert r.status_code == 400


def test_late_commit_rejected(client):
    _register(client, "seal-a")
    task_id = _sealed_task(client)
    get_fabric().tasks[task_id]["commit_deadline"] = _now_ms() - 1

    r = _commit(client, task_id, "seal-a", 1.0, "aa" * 32)
    assert r.status_code == 403
    assert "commit window closed" in r.get_json()["error"]


def test_late_reveal_rejected(client):
    _register(client, "seal-a")
    task_id = _sealed_task(client)
    assert _commit(client, task_id, "seal-a", 1.0, "aa" * 32).status_code == 201
    get_fabric().tasks[task_id]["commit_deadline"] = _now_ms() - 60_000
    _force_past_reveal_deadline(task_id)

    r = _reveal(client, task_id, "seal-a", 1.0, "aa" * 32)
    assert r.status_code == 403
    assert "reveal window closed" in r.get_json()["error"]


def test_double_commit_and_double_reveal_rejected(client):
    _register(client, "seal-a")
    task_id = _sealed_task(client)
    assert _commit(client, task_id, "seal-a", 1.0, "aa" * 32).status_code == 201
    assert _commit(client, task_id, "seal-a", 1.0, "aa" * 32).status_code == 409

    _force_reveal_window(task_id)
    assert _reveal(client, task_id, "seal-a", 1.0, "aa" * 32).status_code == 201
    r = _reveal(client, task_id, "seal-a", 1.0, "aa" * 32)
    assert r.status_code == 409
    assert "already revealed" in r.get_json()["error"]


def test_reveal_without_commit_rejected(client):
    _register(client, "seal-a")
    task_id = _sealed_task(client)
    _force_reveal_window(task_id)
    r = _reveal(client, task_id, "seal-a", 1.0, "aa" * 32)
    assert r.status_code == 404


def test_plaintext_bid_rejected_on_sealed_task(client):
    _register(client, "seal-a")
    task_id = _sealed_task(client)
    r = client.post(
        "/v1/a2a/bids",
        json={"task_id": task_id, "agent_id": "seal-a", "bid_axm": 1.0,
              "estimated_seconds": 300},
    )
    assert r.status_code == 409


def test_legacy_plaintext_bid_still_works(client):
    # Backward compat: non-sealed tasks keep the old plaintext flow untouched.
    _register(client, "legacy-a")
    r = client.post(
        "/v1/a2a/tasks",
        json={"skill": "lead-enrichment", "tags": ["lead-enrichment"], "bounty_axm": 1.0},
    )
    assert r.status_code == 201
    task_id = r.get_json()["task_id"]
    assert r.get_json()["sealed"] is False

    bid = client.post(
        "/v1/a2a/bids",
        json={"task_id": task_id, "agent_id": "legacy-a", "bid_axm": 1.0,
              "estimated_seconds": 300},
    )
    assert bid.status_code == 201
    assert bid.get_json()["revealed"] is True

    get_fabric().tasks[task_id]["auction_closes_at"] = _now_ms() - 1
    body = client.post(f"/v1/a2a/tasks/{task_id}/close").get_json()
    assert body["state"] == "assigned"
    assert body["assigned_to"] == "legacy-a"


def test_close_unknown_task_404(client):
    assert client.post("/v1/a2a/tasks/nope/close").status_code == 404


def test_sealed_close_before_reveal_deadline_is_noop(client):
    _register(client, "seal-a")
    task_id = _sealed_task(client)
    assert _commit(client, task_id, "seal-a", 1.0, "aa" * 32).status_code == 201
    # Permissionless close attempted too early: task stays in auction.
    body = client.post(f"/v1/a2a/tasks/{task_id}/close").get_json()
    assert body["state"] == "auction"
    assert body["assigned_to"] is None
