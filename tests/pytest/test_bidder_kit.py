"""Routes for the bidder wallet flow.

- GET /v1/a2a/auctions: lists sealed tasks with an onchain anchor.
- GET /v1/a2a/tasks/<task_id>/bidder-kit: the full wallet-bidding kit.

The kit needs COMMIT_REVEAL_AUCTION_ADDRESS configured (AUCTION_CHAIN_ID
pins the chain without an RPC round-trip).
"""

from __future__ import annotations

import os

import pytest
from flask import Flask

from sincor2.a2a_inbound import _now_ms, get_fabric, reset_fabric
from sincor2.a2a_inbound import register as register_inbound
from sincor2.a2a_inbound_market import create_task
from sincor2.onchain.bidder_client import UINT96_MAX


@pytest.fixture
def client(monkeypatch):
    reset_fabric()
    monkeypatch.setenv("COMMIT_REVEAL_AUCTION_ADDRESS",
                       "0x" + "ab" * 20)
    monkeypatch.setenv("EXECUTION_ESCROW_ADDRESS", "0x" + "cd" * 20)
    monkeypatch.setenv("AUCTION_CHAIN_ID", "84532")
    app = Flask(__name__)
    register_inbound(app)
    app.config["TESTING"] = True
    return app.test_client()


def _sealed_task_with_anchor():
    task = create_task("web", ["x"], 2.0, sealed=True, poster_id="poster")
    fabric = get_fabric()
    with fabric.lock:
        fabric.tasks[task["task_id"]]["auction_id"] = "0x" + "11" * 32
        fabric.tasks[task["task_id"]]["onchain_open_tx"] = "0x" + "22" * 32
    return task


def test_auctions_lists_anchored_sealed_tasks(client):
    create_task("web", ["x"], 2.0, sealed=False, poster_id="p")
    anchored = _sealed_task_with_anchor()
    # Sealed but no onchain anchor -> not listed.
    create_task("web", ["x"], 2.0, sealed=True, poster_id="p")

    resp = client.get("/v1/a2a/auctions")
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body["auctions"]) == 1
    entry = body["auctions"][0]
    assert entry["task_id"] == anchored["task_id"]
    assert entry["auction_id"] == "0x" + "11" * 32
    assert entry["chain_id"] == 84532
    assert entry["auction_contract"] == "0x" + "ab" * 20
    assert entry["commit_deadline_ms"] is not None
    assert entry["reveal_deadline_ms"] is not None
    assert body["escrow_contract"] == "0x" + "cd" * 20


def test_bidder_kit_full_payload(client):
    task = _sealed_task_with_anchor()
    resp = client.get(f"/v1/a2a/tasks/{task['task_id']}/bidder-kit"
                      "?agent_id=agent-7")
    assert resp.status_code == 200
    kit = resp.get_json()
    assert kit["auction_id"] == "0x" + "11" * 32
    assert kit["chain_id"] == 84532
    assert kit["rpc_hint"] == "https://sepolia.base.org"
    assert kit["auction_contract"] == "0x" + "ab" * 20
    assert "keccak256(abi.encodePacked" in kit["commitment_scheme"]
    assert kit["price_bounds_wei"]["max"] == str(UINT96_MAX)
    assert any(fn.startswith("commit(bytes32") for fn in kit["functions"])
    assert any(fn.startswith("reveal(bytes32") for fn in kit["functions"])
    assert any("vickreyResult" in fn for fn in kit["functions"])
    assert kit["agent_id"] == "agent-7"
    assert kit["agent_id_hash"].startswith("0x")
    assert len(kit["agent_id_hash"]) == 66


def test_bidder_kit_404s(client):
    task = create_task("web", ["x"], 2.0, sealed=True, poster_id="p")
    # Sealed but never anchored.
    resp = client.get(f"/v1/a2a/tasks/{task['task_id']}/bidder-kit")
    assert resp.status_code == 404
    resp = client.get("/v1/a2a/tasks/nope/bidder-kit")
    assert resp.status_code == 404


def test_bidder_kit_503_without_config(client, monkeypatch):
    task = _sealed_task_with_anchor()
    monkeypatch.delenv("COMMIT_REVEAL_AUCTION_ADDRESS")
    resp = client.get(f"/v1/a2a/tasks/{task['task_id']}/bidder-kit")
    assert resp.status_code == 503


def test_auctions_empty_without_config(client, monkeypatch):
    _sealed_task_with_anchor()
    monkeypatch.delenv("COMMIT_REVEAL_AUCTION_ADDRESS")
    resp = client.get("/v1/a2a/auctions")
    assert resp.status_code == 200
    entry = resp.get_json()["auctions"][0]
    assert entry["chain_id"] is None
    assert entry["auction_contract"] is None
