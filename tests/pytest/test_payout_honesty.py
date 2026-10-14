"""Payout claim-honesty: staged payouts must never present as paid.

A staged digest (0x + sha256 hex) is shape-identical to a real on-chain
transaction hash. The summary surface — proof ``status`` and the task's
``payout_tx`` field — must only claim "paid" when an actual broadcast
produced a real ``tx_hash``. Full-loop integration: register -> task ->
bid -> close -> proof.
"""

from __future__ import annotations

from conftest import signed_task_auth

import re

import pytest
from flask import Flask

from sincor2.a2a_inbound import get_fabric, reset_fabric
from sincor2.a2a_inbound_market import close_auction
from sincor2.contract_net import (
    ContractNetEvaluator,
    MemoryHashStore,
    payout_broadcast_status,
)

TX_RE = re.compile(r"^0x[0-9a-f]{64}$")
REAL_TX = "0x" + "12" * 32
STAGED_DIGEST = "0x" + "34" * 32


def _signed_task_body(skill, tags, bounty_axm, key="0x" + "cc" * 32):
    return {
        "skill": skill,
        "tags": tags,
        "bounty_axm": bounty_axm,
        **signed_task_auth(skill, key=key),
    }


@pytest.fixture
def client():
    reset_fabric()
    app = Flask(__name__)
    from sincor2.a2a_inbound import register as register_inbound

    register_inbound(app)
    from sincor2.a2a_integration import A2ARouter

    app.register_blueprint(A2ARouter().blueprint)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def store():
    return MemoryHashStore()


@pytest.fixture
def evaluator(store):
    return ContractNetEvaluator(store)


def _loop_to_proof(client):
    """Drive register -> task -> bids -> close -> proof. Returns task_id."""
    client.post(
        "/v1/a2a/register",
        json={
            "agent_id": "fast",
            "capability_tags": ["deal-scoring"],
            "rpc_callback": "https://a.example/rpc",
        },
    )
    client.post(
        "/v1/a2a/register",
        json={
            "agent_id": "slow",
            "capability_tags": ["deal-scoring"],
            "rpc_callback": "https://b.example/rpc",
        },
    )
    task = client.post(
        "/v1/a2a/tasks",
        json=_signed_task_body("deal-scoring", ["deal-scoring"], 2.0),
    ).get_json()
    client.post(
        "/v1/a2a/bids",
        json={
            "task_id": task["task_id"],
            "agent_id": "slow",
            "bid_axm": 9.0,
            "estimated_seconds": 400,
        },
    )
    client.post(
        "/v1/a2a/bids",
        json={
            "task_id": task["task_id"],
            "agent_id": "fast",
            "bid_axm": 1.1,
            "estimated_seconds": 30,
        },
    )
    assigned = close_auction(task["task_id"])
    if assigned and assigned.get("state") != "assigned":
        fabric = get_fabric()
        with fabric.lock:
            fabric.tasks[task["task_id"]]["auction_closes_at"] = 0
        assigned = close_auction(task["task_id"])
    assert assigned["assigned_to"] == "fast"
    proof = client.post(
        "/v1/a2a/proofs",
        json={
            "task_id": task["task_id"],
            "agent_id": "fast",
            "receipt_hash": "0x" + "ab" * 32,
        },
    )
    assert proof.status_code == 202
    return task["task_id"], proof.get_json()


def test_staged_settlement_is_not_paid(client):
    """The real end-to-end path: stage_payout never broadcasts, so the
    proof must say payout_staged and no tx-shaped digest may sit under
    the payout_tx field name."""
    task_id, body = _loop_to_proof(client)
    assert body["status"] == "payout_staged"
    assert body["status"] != "paid"
    assert body["payout"]["mode"] == "staged"

    task = get_fabric().tasks[task_id]
    assert task["state"] == "settled"
    assert "payout_tx" not in task, "staged digest must never occupy payout_tx"
    assert TX_RE.match(task["payout_staged_digest"] or "")


def test_broadcast_receipt_earns_paid_and_payout_tx(client, monkeypatch):
    """If a real broadcast path ever produces a tx_hash, the surface must
    honestly flip to paid/payout_tx. Gate works in both directions."""

    def fake_broadcast(**kwargs):
        return {
            "ok": True,
            "mode": "broadcast",
            "tx_hash": REAL_TX,
            "staged_tx_digest": STAGED_DIGEST,
            "task_id": kwargs.get("task_id"),
        }

    monkeypatch.setattr(
        "sincor2.a2a_inbound_market.stage_payout", fake_broadcast
    )
    task_id, body = _loop_to_proof(client)
    assert body["status"] == "paid"
    task = get_fabric().tasks[task_id]
    assert task["payout_tx"] == REAL_TX
    assert "payout_staged_digest" not in task


def test_live_mode_without_tx_hash_is_not_paid(client, monkeypatch):
    """Adversarial: mode='live' (signer present) still performs no
    broadcast — it must NOT earn paid."""

    def fake_live(**kwargs):
        return {
            "ok": True,
            "mode": "live",
            "tx_hash": None,
            "staged_tx_digest": STAGED_DIGEST,
        }

    monkeypatch.setattr("sincor2.a2a_inbound_market.stage_payout", fake_live)
    _, body = _loop_to_proof(client)
    assert body["status"] == "payout_staged"


def test_empty_tx_hash_is_not_paid(client, monkeypatch):
    """Adversarial: empty-string tx_hash is falsy — must not earn paid."""

    def fake_empty(**kwargs):
        return {
            "ok": True,
            "mode": "broadcast",
            "tx_hash": "",
            "staged_tx_digest": STAGED_DIGEST,
        }

    monkeypatch.setattr("sincor2.a2a_inbound_market.stage_payout", fake_empty)
    task_id, body = _loop_to_proof(client)
    assert body["status"] == "payout_staged"
    assert "payout_tx" not in get_fabric().tasks[task_id]


def test_complete_task_meta_is_honest(evaluator, store):
    """ContractNetEvaluator.complete_task must not store the staged digest
    under the tx-shaped payout_tx key."""
    task_id = "task_honest_501"
    store.hset(
        f"task:{task_id}:meta",
        mapping={
            "status": "assigned",
            "assigned_agent": "agent_win",
            "winning_bid_axm": "1.5",
        },
    )
    receipt = evaluator.complete_task(task_id, "0x" + "ab" * 32, "0x" + "11" * 20)
    assert receipt["ok"] is True
    assert receipt["mode"] == "staged"
    meta = store.hgetall(f"task:{task_id}:meta")
    assert meta["status"] == "settled"
    assert "payout_tx" not in meta
    assert TX_RE.match(meta["payout_staged_digest"] or "")


@pytest.mark.parametrize(
    "receipt,expected_status,expected_broadcasted",
    [
        ({"ok": True, "tx_hash": REAL_TX}, "paid", True),
        ({"ok": True, "mode": "broadcast", "tx_hash": REAL_TX}, "paid", True),
        ({"ok": True, "mode": "staged"}, "payout_staged", False),
        ({"ok": True, "mode": "live", "tx_hash": None}, "payout_staged", False),
        ({"ok": True, "tx_hash": ""}, "payout_staged", False),
        # Adversarial: garbage / malformed / padded hashes fail closed —
        # only exactly 0x + 64 hex earns broadcasted/paid.
        ({"ok": True, "tx_hash": "garbage"}, "payout_staged", False),
        ({"ok": True, "tx_hash": "0xZZZ"}, "payout_staged", False),
        ({"ok": True, "tx_hash": "  " + REAL_TX + "  "}, "payout_staged", False),
        ({"ok": False}, "rejected", False),
        # Contradictory input, honest output: money moved (broadcasted)
        # but settlement failed (rejected) — needs human eyes, not a lie.
        ({"ok": False, "tx_hash": REAL_TX}, "rejected", True),
        ({}, "rejected", False),
        (None, "rejected", False),
    ],
)
def test_payout_broadcast_status_truth_table(
    receipt, expected_status, expected_broadcasted
):
    """Unit truth table for the honesty gate. broadcasted tracks money
    movement; proof_status tracks settlement outcome — independent."""
    result = payout_broadcast_status(receipt)
    assert result["proof_status"] == expected_status
    assert result["broadcasted"] == expected_broadcasted
