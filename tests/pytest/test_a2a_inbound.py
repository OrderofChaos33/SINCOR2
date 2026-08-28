"""Inbound A2A register + contract-net evaluator — no 250 SINC gate."""

from __future__ import annotations

import json
import time

import pytest
from flask import Flask

from sincor2.contract_net import (
    ContractNetEvaluator,
    MemoryHashStore,
    calculate_bid_score,
)
from sincor2.a2a_inbound import (
    MERIT_THRESHOLD_AXM,
    close_auction,
    register as register_inbound,
    reset_fabric,
)


@pytest.fixture
def store():
    return MemoryHashStore()


@pytest.fixture
def evaluator(store):
    return ContractNetEvaluator(store)


@pytest.fixture
def client():
    reset_fabric()
    app = Flask(__name__)
    register_inbound(app)
    app.config["TESTING"] = True
    return app.test_client()


def _card(name="Scout Agent"):
    return {
        "agent_card": {
            "name": name,
            "description": "External scout worker",
            "version": "1.0.0",
            "skills": [
                {"id": "lead-enrichment", "name": "Lead Enrichment", "tags": ["leads"]}
            ],
            "supportedInterfaces": [{"url": "https://scout.example.com/rpc"}],
        },
        "agent_url": "https://scout.example.com",
        "sinc_stake": 0,
    }


def test_concurrent_bid_submissions(store):
    task_id = "task_cnc_001"
    bids_key = f"task:{task_id}:bids"
    for idx in range(50):
        agent_id = f"0xAgent_{idx:02d}"
        payload = json.dumps(
            {"bid_amount": 5.0 + idx, "estimated_seconds": 300 - (idx * 2)}
        )
        store.hset(bids_key, mapping={agent_id: payload})
    assert len(store.hgetall(bids_key)) == 50


def test_auction_evaluator_winner_selection(store, evaluator):
    task_id = "task_eval_101"
    store.hset(f"task:{task_id}:meta", mapping={"status": "open", "created_at": "1700000000"})
    agents = {
        "agent_expensive": {"rep": 50.0, "bid": 10.0, "time": 120},
        "agent_optimal": {"rep": 80.0, "bid": 2.0, "time": 60},
        "agent_slow": {"rep": 100.0, "bid": 50.0, "time": 600},
    }
    for agent_id, data in agents.items():
        store.hset(f"agent:{agent_id}:stats", reputation=str(data["rep"]))
        store.hset(
            f"task:{task_id}:bids",
            mapping={
                agent_id: json.dumps(
                    {"bid_amount": data["bid"], "estimated_seconds": data["time"]}
                )
            },
        )
    winning_bid = evaluator.evaluate_task_bids(task_id)
    assert winning_bid is not None
    assert winning_bid["bid_amount"] == 2.0
    meta = store.hgetall(f"task:{task_id}:meta")
    assert meta["status"] == "assigned"
    assert meta["assigned_agent"] == "agent_optimal"
    scores = {
        aid: calculate_bid_score(d["bid"], d["time"], d["rep"]) for aid, d in agents.items()
    }
    assert max(scores, key=scores.get) == "agent_optimal"


def test_pubsub_broadcast_on_assignment(store, evaluator):
    task_id = "task_pubsub_202"
    store.hset(f"task:{task_id}:meta", status="open")
    store.hset("agent:agent_win:stats", reputation="90")
    store.hset(
        f"task:{task_id}:bids",
        mapping={"agent_win": json.dumps({"bid_amount": 1.5, "estimated_seconds": 45})},
    )
    evaluator.evaluate_task_bids(task_id)
    assert store.published
    channel, raw = store.published[-1]
    assert channel == "tasks:broadcast"
    payload = json.loads(raw)
    assert payload["event"] == "task_assigned"
    assert payload["data"]["assigned_agent"] == "agent_win"


def test_no_bids_transitions_to_expired(store, evaluator):
    task_id = "task_empty_303"
    store.hset(f"task:{task_id}:meta", status="open")
    assert evaluator.evaluate_task_bids(task_id) is None
    assert store.hget(f"task:{task_id}:meta", "status") == "expired"


def test_complete_task_stages_escrow_payout(store, evaluator):
    task_id = "task_pay_404"
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
    assert receipt["tx_hash"].startswith("0x")
    assert store.hget(f"task:{task_id}:meta", "status") == "settled"


def test_register_agent_card_no_sinc_gate(client):
    response = client.post("/api/marketplace/register", json=_card())
    assert response.status_code == 201
    data = response.get_json()
    assert data["status"] == "registered"
    assert data["probation"] is True
    assert data["routing_priority"] == "probation"
    assert data["merit_threshold_axm"] == MERIT_THRESHOLD_AXM
    assert data["paymaster"]["sponsored"] is True


def test_v1_register_manifest(client):
    response = client.post(
        "/v1/a2a/register",
        json={
            "agent_id": "scout-1",
            "name": "Scout",
            "capability_tags": ["lead-enrichment"],
            "wallet": "0x" + "11" * 20,
            "rpc_callback": "https://scout.example.com/rpc",
        },
    )
    assert response.status_code == 201
    data = response.get_json()
    assert data["agent_id"] == "scout-1"
    listed = client.get("/v1/a2a/agents")
    assert listed.status_code == 200
    ids = {a["agent_id"] for a in listed.get_json()["agents"]}
    assert "scout-1" in ids


def test_heartbeat_and_directory(client):
    client.post("/v1/a2a/register", json={
        "agent_id": "hb-1",
        "capability_tags": ["deal-scoring"],
        "rpc_callback": "https://hb.example/rpc",
        "wallet": "0x" + "22" * 20,
    })
    beat = client.post("/v1/a2a/heartbeat", json={"agent_id": "hb-1"})
    assert beat.status_code == 200
    assert beat.get_json()["ok"] is True
    directory = client.get("/v1/a2a/directory")
    assert directory.status_code == 200
    kpis = directory.get_json()["kpis"]
    assert kpis["merit_threshold_axm"] == 5
    assert kpis["heartbeat_ttl_s"] == 60
    assert kpis["probation_open"] >= 1


def test_probation_agent_bids_micro_task(client):
    client.post("/v1/a2a/register", json={
        "agent_id": "prob-1",
        "capability_tags": ["lead-enrichment"],
        "rpc_callback": "https://p.example/rpc",
        "wallet": "0x" + "33" * 20,
    })
    task = client.post(
        "/v1/a2a/tasks",
        json={"skill": "lead-enrichment", "tags": ["lead-enrichment"], "bounty_axm": 1.5},
    )
    assert task.status_code == 201
    assert task.get_json()["requires_merit"] is False
    bid = client.post(
        "/v1/a2a/bids",
        json={
            "task_id": task.get_json()["task_id"],
            "agent_id": "prob-1",
            "bid_axm": 1.2,
            "estimated_seconds": 45,
        },
    )
    assert bid.status_code == 201
    assert bid.get_json()["agent_id"] == "prob-1"


def test_merit_gate_blocks_probation_on_large_bounty(client):
    client.post("/v1/a2a/register", json={
        "agent_id": "prob-2",
        "capability_tags": ["compliance-sbom"],
        "rpc_callback": "https://p2.example/rpc",
        "wallet": "0x" + "44" * 20,
    })
    task = client.post(
        "/v1/a2a/tasks",
        json={"skill": "compliance-sbom", "tags": ["compliance-sbom"], "bounty_axm": 8.0},
    )
    assert task.get_json()["requires_merit"] is True
    bid = client.post(
        "/v1/a2a/bids",
        json={
            "task_id": task.get_json()["task_id"],
            "agent_id": "prob-2",
            "bid_axm": 4.0,
            "estimated_seconds": 90,
        },
    )
    assert bid.status_code == 403


def test_docs_a2a(client):
    response = client.get("/docs/a2a")
    assert response.status_code == 200
    assert b"SINCOR inbound A2A" in response.data


def test_close_auction_assigns_lowest_composite(client):
    client.post("/v1/a2a/register", json={
        "agent_id": "fast",
        "capability_tags": ["deal-scoring"],
        "rpc_callback": "https://a.example/rpc",
        "wallet": "0x" + "55" * 20,
    })
    client.post("/v1/a2a/register", json={
        "agent_id": "slow",
        "capability_tags": ["deal-scoring"],
        "rpc_callback": "https://b.example/rpc",
        "wallet": "0x" + "66" * 20,
    })
    task = client.post(
        "/v1/a2a/tasks",
        json={"skill": "deal-scoring", "tags": ["deal-scoring"], "bounty_axm": 2.0},
    ).get_json()
    client.post("/v1/a2a/bids", json={
        "task_id": task["task_id"], "agent_id": "slow", "bid_axm": 9.0, "estimated_seconds": 400,
    })
    client.post("/v1/a2a/bids", json={
        "task_id": task["task_id"], "agent_id": "fast", "bid_axm": 1.1, "estimated_seconds": 30,
    })
    # Force the 500ms window closed.
    assigned = close_auction(task["task_id"])
    # Window may still be open — jump it.
    if assigned and assigned.get("state") != "assigned":
        from sincor2.a2a_inbound import get_fabric

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
    body = proof.get_json()
    assert body["status"] == "paid"
    assert body["payout"]["mode"] == "staged"
