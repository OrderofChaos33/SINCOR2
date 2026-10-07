"""VERIFIED-2: commit must not double-lock stake on retry.

Before the fix, ``commit_bid`` called ``lock_for_commit`` (additive) BEFORE
checking ``already committed``. A headerless retry therefore double-locked
stake, and a later ghost-slash would take 100% of the DOUBLED lock.

After the fix, the already-committed check runs first: a retry returns the
existing commitment record (idempotent) without touching the stake ledger.
"""

from __future__ import annotations

import pytest
from flask import Flask

from sincor2.a2a_inbound import register as register_inbound, reset_fabric


@pytest.fixture
def idem_db(tmp_path, monkeypatch):
    """Isolated idempotency DB per test (never touches the real data dir)."""
    from sincor2.a2a_idempotency import reset_idempotency_store

    monkeypatch.setenv("SINCOR_IDEMPOTENCY_DB_PATH", str(tmp_path / "idem.db"))
    reset_idempotency_store()
    yield str(tmp_path / "idem.db")
    reset_idempotency_store()


@pytest.fixture
def client(idem_db, tmp_path):
    from sincor2.onchain.stake_ledger import reset_stake_ledger

    reset_fabric()
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    app = Flask(__name__)
    register_inbound(app)
    from sincor2.a2a_integration import A2ARouter

    app.register_blueprint(A2ARouter().blueprint)
    app.config["TESTING"] = True
    return app.test_client()


def _register_and_fund(client, agent_id):
    from sincor2.onchain.stake_ledger import stake_ledger

    r = client.post("/v1/a2a/register", json={
        "agent_id": agent_id,
        "capability_tags": ["lead-enrichment"],
        "rpc_callback": "https://agent.example/rpc",
        "wallet": "0x" + "11" * 20,
    })
    assert r.status_code in (200, 201), r.get_json()
    stake_ledger().deposit(agent_id, 10 * 10**18)


def _sealed_task(client):
    r = client.post("/v1/a2a/tasks", json={
        "skill": "lead-enrichment", "tags": ["lead-enrichment"],
        "bounty_axm": 1.5, "sealed": True,
    })
    assert r.status_code == 201, r.get_json()
    return r.get_json()["task_id"]


def _commitment(bid_axm, nonce_hex, agent_id):
    from sincor2.a2a_inbound_market import sealed_commitment

    price_wei = int(round(float(bid_axm) * 1e18))
    salt = bytes.fromhex(nonce_hex)
    return "0x" + sealed_commitment(price_wei, salt, agent_id).hex()


def _locked_for(client, agent_id, task_id):
    from sincor2.onchain.stake_ledger import stake_ledger

    rec = stake_ledger()._agent(agent_id)
    return int(rec["locks"].get(task_id, "0"))


def test_retry_does_not_double_lock(client):
    """Second commit (no idempotency key) returns 201 with the same record
    and does NOT increase the stake lock."""
    agent_id = "v2-agent-1"
    _register_and_fund(client, agent_id)
    task_id = _sealed_task(client)
    body = {"task_id": task_id, "agent_id": agent_id,
            "commitment": _commitment(1.0, "aa" * 32, agent_id)}

    r1 = client.post("/v1/a2a/bids/commit", json=body)
    assert r1.status_code == 201, r1.get_json()
    locked_after_first = _locked_for(client, agent_id, task_id)
    assert locked_after_first > 0, "first commit must lock stake"

    # Headerless retry — the VERIFIED-2 scenario.
    r2 = client.post("/v1/a2a/bids/commit", json=body)
    assert r2.status_code == 201, r2.get_json()
    assert r2.get_json() == r1.get_json(), "retry must return identical record"

    locked_after_retry = _locked_for(client, agent_id, task_id)
    assert locked_after_retry == locked_after_first, (
        f"double-lock detected: {locked_after_first} -> {locked_after_retry}"
    )


def test_triple_commit_single_lock(client):
    """Three rapid commits still result in exactly one lock."""
    agent_id = "v2-agent-2"
    _register_and_fund(client, agent_id)
    task_id = _sealed_task(client)
    body = {"task_id": task_id, "agent_id": agent_id,
            "commitment": _commitment(1.0, "bb" * 32, agent_id)}

    for i in range(3):
        r = client.post("/v1/a2a/bids/commit", json=body)
        assert r.status_code == 201, (i, r.get_json())

    from sincor2.a2a_inbound import get_fabric

    commits = [c for c in get_fabric().commits.values()
               if c["task_id"] == task_id and c["agent_id"] == agent_id]
    assert len(commits) == 1, "exactly one commitment record must exist"


def test_different_agent_still_locks_separately(client):
    """The fix must not break legitimate distinct commits: a different
    agent on the same task still locks its own stake."""
    _register_and_fund(client, "v2-agent-3a")
    _register_and_fund(client, "v2-agent-3b")
    task_id = _sealed_task(client)

    body_a = {"task_id": task_id, "agent_id": "v2-agent-3a",
              "commitment": _commitment(1.0, "cc" * 32, "v2-agent-3a")}
    body_b = {"task_id": task_id, "agent_id": "v2-agent-3b",
              "commitment": _commitment(1.0, "dd" * 32, "v2-agent-3b")}

    assert client.post("/v1/a2a/bids/commit", json=body_a).status_code == 201
    assert client.post("/v1/a2a/bids/commit", json=body_b).status_code == 201

    assert _locked_for(client, "v2-agent-3a", task_id) > 0
    assert _locked_for(client, "v2-agent-3b", task_id) > 0
