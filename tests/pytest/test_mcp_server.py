"""MCP server (sincor2.mcp_server) + marketplace manifest tests.

The MCP server is tested as a JSON-RPC dispatcher whose HTTP transport is
monkeypatched onto a Flask test client running the REAL routes — every tool
exercises the production endpoint it wraps. Write tools must prove
default-deny: no confirmation token, no execution.
"""

from __future__ import annotations

import json

import pytest
from flask import Flask

from sincor2.a2a_inbound import _now_ms, get_fabric, reset_fabric
from sincor2.a2a_inbound import register as register_inbound
from sincor2.a2a_inbound_market import create_task, sealed_commitment
from sincor2.a2a_rate_limits import reset_a2a_limits
from sincor2.onchain.stake_ledger import reset_stake_ledger, stake_ledger

import sincor2.mcp_server as ms


@pytest.fixture
def env(tmp_path, monkeypatch):
    reset_fabric()
    reset_a2a_limits()
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    # Bidder-kit needs an onchain config; env-only config avoids RPC reads.
    monkeypatch.setenv("COMMIT_REVEAL_AUCTION_ADDRESS", "0x" + "aa" * 20)
    monkeypatch.setenv("EXECUTION_ESCROW_ADDRESS", "0x" + "bb" * 20)
    monkeypatch.setenv("AUCTION_CHAIN_ID", "8453")
    app = Flask(__name__)
    register_inbound(app)
    from sincor2.a2a_integration import A2ARouter

    app.register_blueprint(A2ARouter().blueprint)
    app.config["TESTING"] = True
    client = app.test_client()

    def fake_request(method, path, body=None):
        if method.upper() == "GET":
            resp = client.get(path)
        else:
            resp = client.post(path, json=body)
        try:
            data = resp.get_json()
        except Exception:
            data = None
        return {"ok": 200 <= resp.status_code < 300,
                "status": resp.status_code, "data": data}

    monkeypatch.setattr(ms, "_request", fake_request)
    return client


def _call(name, arguments, rpc_id=1):
    """One tools/call round-trip; returns (parsed_text_payload, is_error)."""
    resp = ms.handle_message({
        "jsonrpc": "2.0", "id": rpc_id, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
    assert resp is not None and "result" in resp, resp
    assert resp["result"]["content"][0]["type"] == "text"
    return json.loads(resp["result"]["content"][0]["text"]), \
        resp["result"].get("isError", False)


def _register_agent(client, agent_id, tags=("lead-enrichment",)):
    r = client.post("/v1/a2a/register", json={
        "agent_id": agent_id,
        "capability_tags": list(tags),
        "rpc_callback": "https://agent.example/rpc",
        "wallet": "0x" + "11" * 20,
    })
    assert r.status_code in (200, 201), r.get_json()
    r = client.post("/v1/a2a/heartbeat", json={"agent_id": agent_id})
    assert r.status_code == 200
    stake_ledger().deposit(agent_id, 10 * 10**18)


def _anchored_sealed_task(skill="lead-enrichment", bounty=2.0):
    """Sealed task with a stamped auction_id (simulates an onchain anchor)."""
    task = create_task(skill, tags=[skill], bounty_axm=bounty, sealed=True)
    task_id = task["task_id"]
    fabric = get_fabric()
    with fabric.lock:
        fabric.tasks[task_id]["auction_id"] = "0x" + "cc" * 32
    return task_id


# ── Protocol handshake ─────────────────────────────────────────────────────

def test_initialize_and_tools_list(env):
    resp = ms.handle_message({"jsonrpc": "2.0", "id": 1,
                              "method": "initialize", "params": {}})
    assert resp["result"]["protocolVersion"] == ms.MCP_PROTOCOL_VERSION
    assert resp["result"]["serverInfo"]["name"] == ms.SERVER_NAME

    resp = ms.handle_message({"jsonrpc": "2.0", "id": 2,
                              "method": "tools/list", "params": {}})
    names = [t["name"] for t in resp["result"]["tools"]]
    assert names == ["list_tasks", "get_task", "get_agent_card",
                     "get_quote", "register_agent", "submit_bid"]


def test_unknown_tool_is_jsonrpc_error(env):
    resp = ms.handle_message({"jsonrpc": "2.0", "id": 9,
                              "method": "tools/call",
                              "params": {"name": "nope", "arguments": {}}})
    assert resp["error"]["code"] == -32601


# ── Manifest ───────────────────────────────────────────────────────────────

def test_marketplace_manifest(env):
    r = env.get("/.well-known/sincor-marketplace.json")
    assert r.status_code == 200
    assert r.content_type.startswith("application/json")
    manifest = r.get_json()
    assert manifest["mcp"]["module"] == "sincor2.mcp_server"
    assert set(manifest["mcp"]["tools"]) == {
        "list_tasks", "get_task", "get_agent_card",
        "get_quote", "register_agent", "submit_bid"}
    assert manifest["endpoints"]["auctions"].endswith("/v1/a2a/auctions")
    assert "{task_id}" in manifest["endpoints"]["bidder_kit"]
    assert "upgrade_path" in manifest


# ── Read tools return real platform data ───────────────────────────────────

def test_list_tasks_returns_anchored_auction(env):
    task_id = _anchored_sealed_task()
    payload, is_error = _call("list_tasks", {"skill": "lead-enrichment"})
    assert not is_error, payload
    assert payload["source"] == "GET /v1/a2a/auctions"
    ids = [a["task_id"] for a in payload["auctions"]]
    assert task_id in ids
    entry = next(a for a in payload["auctions"] if a["task_id"] == task_id)
    assert entry["bounty_axm"] == 2.0
    assert entry["bidder_kit"] == f"/v1/a2a/tasks/{task_id}/bidder-kit"

    # Skill filter excludes non-matching auctions.
    payload, _ = _call("list_tasks", {"skill": "dental-billing-scrub"})
    assert task_id not in [a["task_id"] for a in payload["auctions"]]


def test_get_task_returns_bidder_kit(env):
    task_id = _anchored_sealed_task()
    payload, is_error = _call("get_task", {"task_id": task_id})
    assert not is_error, payload
    assert payload["task_id"] == task_id
    assert payload["auction_id"] == "0x" + "cc" * 32
    assert "commitment_scheme" in payload
    assert payload["chain_id"] == 8453


def test_get_task_unknown_is_error_payload(env):
    payload, is_error = _call("get_task", {"task_id": "tsk_nope"})
    assert is_error
    assert payload["upstream"]["status"] == 404


def test_get_agent_card(env):
    payload, is_error = _call("get_agent_card", {"skill_id": "lead-enrichment"})
    assert not is_error, payload
    assert payload["count"] >= 1
    card = payload["cards"][0]
    skill_ids = [s["id"] for s in card["skills"]]
    assert "lead-enrichment" in skill_ids
    assert all("quoteUrl" in s for s in card["skills"])


def test_get_quote(env):
    payload, is_error = _call(
        "get_quote", {"skill_id": "lead-enrichment", "caller_id": "mcp-test"})
    assert not is_error, payload
    assert payload["skill_id"] == "lead-enrichment"
    assert "axm_price_wei" in payload
    assert payload["platform_fee_bps"] >= 0


# ── Write tools: default-deny confirmation gate ────────────────────────────

def test_register_agent_requires_confirmation(env):
    args = {"agent_id": "mcp-agent-1", "name": "MCP Agent",
            "capability_tags": ["lead-enrichment"],
            "rpc_callback": "https://agent.example/rpc"}
    payload, is_error = _call("register_agent", args)
    assert not is_error, payload
    assert payload["status"] == "confirmation_required"
    assert payload["http_request"]["method"] == "POST"
    assert payload["http_request"]["url"].endswith("/v1/a2a/register")
    assert payload["confirmation_token"]
    assert payload["expires_in_s"] == 300
    # Default-deny proven: nothing was registered.
    assert "mcp-agent-1" not in get_fabric().agents

    # Confirm with identical arguments + token + action_id → executes.
    confirm_args = dict(args, action_id=payload["action_id"],
                        confirmation_token=payload["confirmation_token"])
    payload2, is_error2 = _call("register_agent", confirm_args, rpc_id=2)
    assert not is_error2, payload2
    assert payload2["ok"] and payload2["status"] == 201
    agent = get_fabric().agents["mcp-agent-1"]
    # Earned-only reputation intact: starts at 0.0 (probation).
    assert agent["reputation"] == 0.0
    assert agent["probation"] is True


def test_register_agent_ignores_declared_reputation(env):
    args = {"agent_id": "mcp-agent-rep", "name": "Rep Agent",
            "capability_tags": ["lead-enrichment"],
            "reputation": 9.9}  # not a real registration field; must not leak in
    payload, _ = _call("register_agent", args)
    confirm_args = dict(args, action_id=payload["action_id"],
                        confirmation_token=payload["confirmation_token"])
    payload2, _ = _call("register_agent", confirm_args, rpc_id=2)
    assert payload2["ok"]
    assert get_fabric().agents["mcp-agent-rep"]["reputation"] == 0.0


def test_write_denied_on_bad_token(env):
    args = {"agent_id": "mcp-agent-bad", "name": "Bad",
            "capability_tags": ["lead-enrichment"]}
    payload, _ = _call("register_agent", args)
    bad = dict(args, action_id=payload["action_id"],
               confirmation_token="wrong-token")
    payload2, is_error2 = _call("register_agent", bad, rpc_id=2)
    assert is_error2
    assert payload2["status"] == "denied"
    assert "mcp-agent-bad" not in get_fabric().agents


def test_confirmation_token_is_single_use(env):
    args = {"agent_id": "mcp-agent-once", "name": "Once",
            "capability_tags": ["lead-enrichment"]}
    payload, _ = _call("register_agent", args)
    confirm = dict(args, action_id=payload["action_id"],
                   confirmation_token=payload["confirmation_token"])
    payload2, is_error2 = _call("register_agent", confirm, rpc_id=2)
    assert not is_error2 and payload2["ok"]
    # Replay the same confirmation → denied, no double registration side effect.
    payload3, is_error3 = _call("register_agent", confirm, rpc_id=3)
    assert is_error3
    assert payload3["status"] == "denied"


def test_confirmation_rejects_changed_arguments(env):
    args = {"agent_id": "mcp-agent-chg", "name": "Chg",
            "capability_tags": ["lead-enrichment"]}
    payload, _ = _call("register_agent", args)
    tampered = dict(args, action_id=payload["action_id"],
                    confirmation_token=payload["confirmation_token"],
                    name="Chg-TAMPERED")
    payload2, is_error2 = _call("register_agent", tampered, rpc_id=2)
    assert is_error2
    assert payload2["status"] == "denied"
    assert "mcp-agent-chg" not in get_fabric().agents


# ── submit_bid: sealed commit/reveal through the confirmation gate ──────────

def _confirm_submit(args):
    """Run the two-phase confirm for submit_bid; returns final payload."""
    payload, is_error = _call("submit_bid", args)
    assert not is_error, payload
    assert payload["status"] == "confirmation_required", payload
    confirm = dict(args,
                   action_id=payload["action_id"],
                   confirmation_token=payload["confirmation_token"])
    if payload.get("generated_salt_hex"):
        confirm["nonce"] = payload["generated_salt_hex"]
    payload2, is_error2 = _call("submit_bid", confirm, rpc_id=2)
    return payload2, is_error2, payload


def test_submit_bid_sealed_commit_flow(env):
    _register_agent(env, "mcp-bidder")
    task_id = _anchored_sealed_task(bounty=2.0)

    args = {"task_id": task_id, "agent_id": "mcp-bidder", "bid_axm": 1.5}
    # Phase 1: default-deny — no commit recorded.
    payload, _ = _call("submit_bid", args)
    assert payload["status"] == "confirmation_required"
    assert payload["http_request"]["url"].endswith("/v1/a2a/bids/commit")
    assert payload["generated_salt_hex"]
    fabric = get_fabric()
    assert not any(k for k in fabric.commits)

    # Phase 2: confirm → the commit executes.
    payload2, is_error2, phase1 = _confirm_submit(args)
    assert not is_error2, payload2
    upstream = payload2["upstream"]
    assert upstream["ok"] and upstream["status"] == 201
    record = upstream["data"]
    assert record["agent_id"] == "mcp-bidder"
    # Commitment matches the canonical scheme (platform will verify it).
    salt = bytes.fromhex(phase1["generated_salt_hex"][2:])
    expected = "0x" + sealed_commitment(int(1.5 * 1e18), salt,
                                        "mcp-bidder").hex()
    assert record["commitment"] == expected


def test_submit_bid_reveal_flow(env):
    _register_agent(env, "mcp-revealer")
    task_id = _anchored_sealed_task(bounty=2.0)
    args = {"task_id": task_id, "agent_id": "mcp-revealer", "bid_axm": 1.5}
    _, _, phase1 = _confirm_submit(args)
    nonce = phase1["generated_salt_hex"]

    # Move into the reveal window (commit phase over, reveal open).
    get_fabric().tasks[task_id]["commit_deadline"] = _now_ms() - 1000

    reveal_args = {"task_id": task_id, "agent_id": "mcp-revealer",
                   "bid_axm": 1.5, "mode": "reveal", "nonce": nonce}
    payload, is_error = _call("submit_bid", reveal_args)
    assert payload["status"] == "confirmation_required"
    confirm = dict(reveal_args, action_id=payload["action_id"],
                   confirmation_token=payload["confirmation_token"])
    payload2, is_error2 = _call("submit_bid", confirm, rpc_id=2)
    assert not is_error2, payload2
    assert payload2["ok"] and payload2["status"] == 201
    assert payload2["data"]["revealed"] is True


def test_submit_bid_legacy_flow(env):
    _register_agent(env, "mcp-legacy")
    task = create_task("lead-enrichment", tags=["lead-enrichment"],
                       bounty_axm=1.5, sealed=False)
    args = {"task_id": task["task_id"], "agent_id": "mcp-legacy",
            "bid_axm": 1.0, "mode": "legacy"}
    payload, _ = _call("submit_bid", args)
    assert payload["http_request"]["url"].endswith("/v1/a2a/bids")
    confirm = dict(args, action_id=payload["action_id"],
                   confirmation_token=payload["confirmation_token"])
    payload2, is_error2 = _call("submit_bid", confirm, rpc_id=2)
    assert not is_error2, payload2
    assert payload2["ok"] and payload2["status"] == 201


def test_submit_bid_changed_args_denied(env):
    _register_agent(env, "mcp-tamper")
    task_id = _anchored_sealed_task(bounty=2.0)
    args = {"task_id": task_id, "agent_id": "mcp-tamper", "bid_axm": 1.5}
    payload, _ = _call("submit_bid", args)
    tampered = dict(args, bid_axm=0.01,  # price changed after confirmation
                    nonce=payload["generated_salt_hex"],
                    action_id=payload["action_id"],
                    confirmation_token=payload["confirmation_token"])
    payload2, is_error2 = _call("submit_bid", tampered, rpc_id=2)
    assert is_error2
    assert payload2["status"] == "denied"
    assert not any(k for k in get_fabric().commits)
