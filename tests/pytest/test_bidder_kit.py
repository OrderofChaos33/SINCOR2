"""Routes for the bidder wallet flow.

- GET /v1/a2a/auctions: lists sealed tasks with an onchain anchor.
- GET /v1/a2a/tasks/<task_id>/bidder-kit: the full wallet-bidding kit.

The kit needs COMMIT_REVEAL_AUCTION_ADDRESS configured (AUCTION_CHAIN_ID
pins the chain without an RPC round-trip).
"""

from __future__ import annotations

import os

import pytest
from conftest import hb_headers
from flask import Flask

from sincor2.a2a_inbound import _now_ms, get_fabric, reset_fabric
from sincor2.a2a_inbound import register as register_inbound
from sincor2.a2a_inbound_market import create_task, sealed_commitment
from sincor2.a2a_rate_limits import reset_a2a_limits
from sincor2.onchain.bidder_client import UINT96_MAX
from sincor2.onchain.stake_ledger import reset_stake_ledger, stake_ledger


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
    # The board scheme rides along: an anchored task is ALSO biddable
    # through the board API, and the kit must say so — with the board's
    # own (different) commitment scheme, not the onchain one.
    assert kit["mode"] == "onchain+board"
    assert kit["onchain"]["auction_id"] == "0x" + "11" * 32
    board = kit["board"]
    assert board["mode"] == "sealed"
    assert board["currency"] == "AXM"
    assert "auctionId" not in board["commitment_scheme"]
    assert board["commit_endpoint"]["path"] == "/v1/a2a/bids/commit"
    assert board["reveal_endpoint"]["path"] == "/v1/a2a/bids/reveal"


def test_bidder_kit_unknown_task_404s(client):
    resp = client.get("/v1/a2a/tasks/nope/bidder-kit")
    assert resp.status_code == 404


def test_auctions_empty_without_config(client, monkeypatch):
    _sealed_task_with_anchor()
    monkeypatch.delenv("COMMIT_REVEAL_AUCTION_ADDRESS")
    resp = client.get("/v1/a2a/auctions")
    assert resp.status_code == 200
    entry = resp.get_json()["auctions"][0]
    assert entry["chain_id"] is None
    assert entry["auction_contract"] is None


# ── Board kit: the scheme that actually commits/reveals on the board ──────
#
# Regression coverage for the outsider trap (sweep findings N2/N3,
# 2026-11-20): the kit used to 404 for every unanchored task — i.e.
# every real board task — and taught only the onchain commitment
# scheme, whose hashes can never reveal against the board's offchain
# shim (the reveal fails and the commit's stake lock is ghost-slashed).

@pytest.fixture
def board_client(tmp_path):
    reset_fabric()
    reset_a2a_limits()
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    app = Flask(__name__)
    register_inbound(app)
    app.config["TESTING"] = True
    return app.test_client()


def test_bidder_kit_board_for_unanchored_sealed_task(board_client):
    task = create_task("web", ["x"], 2.0, sealed=True, poster_id="poster")
    resp = board_client.get(
        f"/v1/a2a/tasks/{task['task_id']}/bidder-kit?agent_id=agent-7")
    assert resp.status_code == 200
    kit = resp.get_json()
    assert kit["mode"] == "board-sealed"
    assert kit["currency"] == "AXM"
    # The published scheme is the OFFCHAIN board scheme: no auctionId,
    # no chainId in the preimage, prices in AXM wei.
    assert kit["commitment_scheme"] == (
        "keccak256(abi.encodePacked(bytes32(price_wei), "
        "bytes32(salt), keccak256(utf8(agent_id))))")
    assert kit["price_bounds_wei"] == {"min": "1", "max": str(UINT96_MAX)}
    assert kit["commit_deadline_ms"] == task["commit_deadline"]
    assert kit["reveal_deadline_ms"] == task["reveal_deadline"]
    assert kit["agent_id_hash"].startswith("0x")
    board = kit["board"]
    assert board["commit_endpoint"]["path"] == "/v1/a2a/bids/commit"
    assert board["reveal_endpoint"]["path"] == "/v1/a2a/bids/reveal"
    assert "NOT" in board["warning"]


def test_bidder_kit_example_commitment_verifies(board_client):
    """The kit's worked example must hash-verify against the board's
    own commitment function — an outsider reproducing the published
    scheme gets exactly the published hash."""
    task = create_task("web", ["x"], 2.0, sealed=True, poster_id="poster")
    kit = board_client.get(
        f"/v1/a2a/tasks/{task['task_id']}/bidder-kit").get_json()
    example = kit["board"]["example"]
    salt = bytes.fromhex(example["salt"][2:])
    recomputed = sealed_commitment(
        int(example["price_wei"]), salt, example["agent_id"])
    assert "0x" + recomputed.hex() == example["commitment"]
    assert example["price_wei"] == str(int(example["bid_axm"] * 1e18))


def test_bidder_kit_board_for_open_task(board_client):
    task = create_task("web", ["x"], 1.5, sealed=False, poster_id="poster")
    resp = board_client.get(f"/v1/a2a/tasks/{task['task_id']}/bidder-kit")
    assert resp.status_code == 200
    kit = resp.get_json()
    assert kit["mode"] == "board-open"
    assert kit["board"]["bid_endpoint"]["path"] == "/v1/a2a/bids"


def test_bidder_kit_anchored_without_config_serves_board_kit(
        board_client, monkeypatch):
    # No onchain config on this fixture at all: an anchored task used to
    # dead-end at 503 even though the board path works fine.
    task = create_task("web", ["x"], 2.0, sealed=True, poster_id="poster")
    fabric = get_fabric()
    with fabric.lock:
        fabric.tasks[task["task_id"]]["auction_id"] = "0x" + "11" * 32
    resp = board_client.get(f"/v1/a2a/tasks/{task['task_id']}/bidder-kit")
    assert resp.status_code == 200
    kit = resp.get_json()
    assert kit["mode"] == "board-sealed"
    assert kit["onchain"]["configured"] is False


def _register_board_agent(client, agent_id, tags=("x",)):
    r = client.post("/v1/a2a/register", json={
        "agent_id": agent_id,
        "capability_tags": list(tags),
        "rpc_callback": "https://agent.example/rpc",
    })
    assert r.status_code in (200, 201), r.get_json()
    r = client.post("/v1/a2a/heartbeat", json={"agent_id": agent_id},
                    headers=hb_headers())
    assert r.status_code == 200
    stake_ledger().deposit(agent_id, 10 * 10**18)


def test_kit_faithful_commit_reveals_end_to_end(board_client):
    """Follow ONLY the kit: build the commitment from its published
    scheme, commit and reveal at its published endpoints. The reveal
    must succeed — before the fix this flow produced an unrevealable
    commit and a 100% ghost slash."""
    _register_board_agent(board_client, "kit-bidder")
    task = create_task("web", ["x"], 2.0, sealed=True, poster_id="poster")
    kit = board_client.get(
        f"/v1/a2a/tasks/{task['task_id']}/bidder-kit"
        "?agent_id=kit-bidder").get_json()
    board = kit["board"]

    salt = bytes.fromhex("ab" * 32)
    bid_axm = 1.5
    price_wei = int(round(bid_axm * 1e18))
    commitment = "0x" + sealed_commitment(price_wei, salt, "kit-bidder").hex()

    commit_path = board["commit_endpoint"]["path"]
    resp = board_client.post(commit_path, json={
        "task_id": task["task_id"], "agent_id": "kit-bidder",
        "commitment": commitment}, headers=hb_headers())
    assert resp.status_code == 201, resp.get_json()

    # Move into the reveal window (commit phase over, reveal still open).
    fabric = get_fabric()
    with fabric.lock:
        fabric.tasks[task["task_id"]]["commit_deadline"] = _now_ms() - 1000

    reveal_path = board["reveal_endpoint"]["path"]
    resp = board_client.post(reveal_path, json={
        "task_id": task["task_id"], "agent_id": "kit-bidder",
        "bid_axm": bid_axm, "nonce": "0x" + salt.hex(),
        "estimated_seconds": 600}, headers=hb_headers())
    assert resp.status_code == 201, resp.get_json()
    assert resp.get_json()["revealed"] is True


def test_onchain_style_commitment_cannot_reveal(board_client):
    """Negative control: a commitment built with the OLD kit's onchain
    scheme (auctionId + chainId bound, ETH wei) must NOT verify against
    the board — proving the two schemes are not interchangeable and the
    board kit had to teach the offchain one."""
    from sincor2.a2a_inbound_market import _keccak256

    _register_board_agent(board_client, "trap-bidder")
    task = create_task("web", ["x"], 2.0, sealed=True, poster_id="poster")
    salt = bytes.fromhex("cd" * 32)
    price_wei = int(round(1.5 * 1e18))
    auction_id = bytes.fromhex("11" * 32)
    chain_id = 8453
    agent_hash = _keccak256(b"trap-bidder")
    onchain_commitment = "0x" + _keccak256(
        auction_id + chain_id.to_bytes(32, "big")
        + price_wei.to_bytes(32, "big") + salt + agent_hash).hex()

    resp = board_client.post("/v1/a2a/bids/commit", json={
        "task_id": task["task_id"], "agent_id": "trap-bidder",
        "commitment": onchain_commitment}, headers=hb_headers())
    assert resp.status_code == 201, resp.get_json()

    fabric = get_fabric()
    with fabric.lock:
        fabric.tasks[task["task_id"]]["commit_deadline"] = _now_ms() - 1000

    resp = board_client.post("/v1/a2a/bids/reveal", json={
        "task_id": task["task_id"], "agent_id": "trap-bidder",
        "bid_axm": 1.5, "nonce": "0x" + salt.hex(),
        "estimated_seconds": 600}, headers=hb_headers())
    assert resp.status_code == 400
    assert "commitment mismatch" in resp.get_json()["error"]
