"""Idempotency keys on A2A writes (G2.7 / backlog item 20).

- Same Idempotency-Key + identical request -> original response replayed,
  exactly one write.
- Same key + different request (or different endpoint scope) -> 422.
- Different keys -> independent writes.
- Error responses never consume a key (client can fix and retry).
- Keys expire (TTL); expired keys are treated as fresh.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid

import pytest
from flask import Flask

from sincor2.a2a_idempotency import (
    IdempotencyStore,
    idempotency_store,
    request_fingerprint,
    reset_idempotency_store,
    valid_key,
)
from sincor2.a2a_inbound import register as register_inbound, reset_fabric


@pytest.fixture
def idem_db(tmp_path, monkeypatch):
    """Isolated idempotency DB per test (never touches the real data dir)."""
    monkeypatch.setenv("SINCOR_IDEMPOTENCY_DB_PATH", str(tmp_path / "idem.db"))
    reset_idempotency_store()
    yield str(tmp_path / "idem.db")
    reset_idempotency_store()


@pytest.fixture
def store(idem_db):
    return IdempotencyStore(idem_db)


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


# ── key format ─────────────────────────────────────────────────────────────

def test_valid_key_format():
    assert valid_key("550e8400-e29b-41d4-a716-446655440000")
    assert valid_key("key_1.2-3")
    assert not valid_key("")
    assert not valid_key("has space")
    assert not valid_key("semi;colon")
    assert not valid_key("x" * 129)


def test_fingerprint_stable_and_sensitive():
    body_a = {"skill": "x", "bounty_axm": 1.5}
    body_b = {"bounty_axm": 1.5, "skill": "x"}  # key order differs
    body_c = {"skill": "x", "bounty_axm": 1.6}
    assert request_fingerprint("s", body_a) == request_fingerprint("s", body_b)
    assert request_fingerprint("s", body_a) != request_fingerprint("s", body_c)
    assert request_fingerprint("s", body_a) != request_fingerprint("other", body_a)


# ── store protocol ─────────────────────────────────────────────────────────

def test_store_replay_returns_original(store):
    fp = request_fingerprint("settle", {"task_id": "t1"})
    outcome, stored = store.begin("k1", "settle", fp)
    assert outcome == "fresh"
    store.complete("k1", 200, {"proof": "abc"})
    outcome, stored = store.begin("k1", "settle", fp)
    assert outcome == "replay"
    assert stored == {"status": 200, "body": {"proof": "abc"}}


def test_store_conflict_on_different_request(store):
    fp1 = request_fingerprint("settle", {"task_id": "t1"})
    fp2 = request_fingerprint("settle", {"task_id": "t2"})
    assert store.begin("k1", "settle", fp1)[0] == "fresh"
    store.complete("k1", 200, {"ok": True})
    # Same key, different write -> conflict, never the other request's result.
    assert store.begin("k1", "settle", fp2)[0] == "conflict"


def test_store_conflict_on_different_scope(store):
    fp = request_fingerprint("settle", {"task_id": "t1"})
    assert store.begin("k1", "settle", fp)[0] == "fresh"
    store.complete("k1", 200, {"ok": True})
    # A settle key replayed as a bid -> conflict (keys never cross scopes).
    assert store.begin("k1", "bids.commit", fp)[0] == "conflict"


def test_store_inflight_second_begin(store):
    fp = request_fingerprint("settle", {"task_id": "t1"})
    assert store.begin("k1", "settle", fp)[0] == "fresh"
    # Response not yet stored: concurrent duplicate must not execute twice.
    assert store.begin("k1", "settle", fp)[0] == "inflight"


def test_store_abandoned_inflight_is_reclaimed(store):
    # An inflight row left by a crashed first attempt (created long ago)
    # is reclaimed so the key is not wedged forever.
    conn = sqlite3.connect(store._db_path)
    conn.execute(
        "INSERT INTO idempotency_keys (key, scope, request_hash, status, "
        "created_at, expires_at) "
        "VALUES (?, ?, ?, 'inflight', ?, ?)",
        ("k-crash", "settle", "fp", time.time() - 10**6, time.time() + 3600),
    )
    conn.commit()
    conn.close()
    fp = request_fingerprint("settle", {"task_id": "t1"})
    assert store.begin("k-crash", "settle", fp)[0] == "fresh"


def test_store_discard_frees_key_for_retry(store):
    fp = request_fingerprint("tasks.create", {"skill": "x"})
    assert store.begin("k1", "tasks.create", fp)[0] == "fresh"
    store.discard("k1")  # e.g. the view returned 4xx
    assert store.begin("k1", "tasks.create", fp)[0] == "fresh"


def test_store_expired_key_is_fresh(store):
    # Insert an expired completed row directly.
    conn = sqlite3.connect(store._db_path)
    conn.execute(
        "INSERT INTO idempotency_keys (key, scope, request_hash, status, "
        "response_status, response_json, created_at, expires_at) "
        "VALUES (?, ?, ?, 'complete', 200, '{}', ?, ?)",
        ("k-old", "settle", "fp", time.time() - 9999, time.time() - 10),
    )
    conn.commit()
    conn.close()
    fp = request_fingerprint("settle", {"task_id": "t1"})
    assert store.begin("k-old", "settle", fp)[0] == "fresh"


def test_store_persists_across_instances(idem_db):
    fp = request_fingerprint("settle", {"task_id": "t1"})
    s1 = IdempotencyStore(idem_db)
    assert s1.begin("k1", "settle", fp)[0] == "fresh"
    s1.complete("k1", 200, {"proof": "abc"})
    # A new instance (new process / worker) sees the same row.
    s2 = IdempotencyStore(idem_db)
    outcome, stored = s2.begin("k1", "settle", fp)
    assert outcome == "replay"
    assert stored["body"] == {"proof": "abc"}


# ── concurrency ────────────────────────────────────────────────────────────

def test_concurrent_duplicate_keys_execute_exactly_once(store):
    """N threads racing with the same key: exactly one may execute."""
    import threading

    fp = request_fingerprint("bids.commit", {"task_id": "t1"})
    outcomes = []
    barrier = threading.Barrier(16)

    def worker():
        barrier.wait()
        outcome, _ = store.begin("k-race", "bids.commit", fp)
        outcomes.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("fresh") == 1
    assert set(outcomes) <= {"fresh", "inflight"}
    store.complete("k-race", 201, {"ok": True})
    outcome, stored = store.begin("k-race", "bids.commit", fp)
    assert outcome == "replay"
    assert stored["body"] == {"ok": True}


# ── route: task create ─────────────────────────────────────────────────────

def _task_count():
    from sincor2.a2a_inbound import get_fabric

    return len(get_fabric().tasks)


def test_task_create_idempotent(client):
    key = str(uuid.uuid4())
    body = {"skill": "lead-enrichment", "bounty_axm": 2.0}
    before = _task_count()
    r1 = client.post("/v1/a2a/tasks", json=body,
                     headers={"Idempotency-Key": key})
    assert r1.status_code == 201, r1.get_json()
    r2 = client.post("/v1/a2a/tasks", json=body,
                     headers={"Idempotency-Key": key})
    assert r2.status_code == 201
    assert r2.headers.get("Idempotent-Replayed") == "true"
    assert r2.get_json()["task_id"] == r1.get_json()["task_id"]
    assert _task_count() == before + 1  # exactly one write


def test_task_create_different_keys_two_tasks(client):
    body = {"skill": "lead-enrichment", "bounty_axm": 2.0}
    r1 = client.post("/v1/a2a/tasks", json=body,
                     headers={"Idempotency-Key": str(uuid.uuid4())})
    r2 = client.post("/v1/a2a/tasks", json=body,
                     headers={"Idempotency-Key": str(uuid.uuid4())})
    assert r1.status_code == r2.status_code == 201
    assert r1.get_json()["task_id"] != r2.get_json()["task_id"]
    assert "Idempotent-Replayed" not in r2.headers


def test_task_create_key_conflict_on_different_request(client):
    key = str(uuid.uuid4())
    r1 = client.post("/v1/a2a/tasks", json={"skill": "lead-enrichment"},
                     headers={"Idempotency-Key": key})
    assert r1.status_code == 201
    # Same key, different write -> 422, never a replay of the first task.
    r2 = client.post("/v1/a2a/tasks", json={"skill": "deal-scoring"},
                     headers={"Idempotency-Key": key})
    assert r2.status_code == 422


def test_task_create_error_does_not_consume_key(client):
    key = str(uuid.uuid4())
    r1 = client.post("/v1/a2a/tasks", json={"bounty_axm": 2.0},  # no skill -> 400
                     headers={"Idempotency-Key": key})
    assert r1.status_code == 400
    # Client fixes the request and retries with the SAME key -> works.
    r2 = client.post("/v1/a2a/tasks", json={"skill": "lead-enrichment"},
                     headers={"Idempotency-Key": key})
    assert r2.status_code == 201


def test_task_create_invalid_key_rejected(client):
    r = client.post("/v1/a2a/tasks", json={"skill": "lead-enrichment"},
                    headers={"Idempotency-Key": "not a valid key!!"})
    assert r.status_code == 400


def test_task_create_no_key_unchanged_behavior(client):
    before = _task_count()
    r1 = client.post("/v1/a2a/tasks", json={"skill": "lead-enrichment"})
    r2 = client.post("/v1/a2a/tasks", json={"skill": "lead-enrichment"})
    assert r1.status_code == r2.status_code == 201
    assert r1.get_json()["task_id"] != r2.get_json()["task_id"]
    assert _task_count() == before + 2  # legacy: no key -> no dedupe


# ── route: sealed-bid commit / reveal ──────────────────────────────────────

def _register_and_fund(client, agent_id):
    from sincor2.onchain.stake_ledger import stake_ledger

    r = client.post("/v1/a2a/register", json={
        "agent_id": agent_id,
        "capability_tags": ["lead-enrichment"],
        "rpc_callback": "https://agent.example/rpc",
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


def test_commit_idempotent(client):
    agent_id = "idem-agent-1"
    _register_and_fund(client, agent_id)
    task_id = _sealed_task(client)
    body = {"task_id": task_id, "agent_id": agent_id,
            "commitment": _commitment(1.0, "aa" * 32, agent_id)}
    key = str(uuid.uuid4())
    r1 = client.post("/v1/a2a/bids/commit", json=body,
                     headers={"Idempotency-Key": key})
    assert r1.status_code == 201, r1.get_json()
    r2 = client.post("/v1/a2a/bids/commit", json=body,
                     headers={"Idempotency-Key": key})
    assert r2.status_code == 201  # replay, NOT 409 "already committed"
    assert r2.headers.get("Idempotent-Replayed") == "true"
    assert r2.get_json() == r1.get_json()
    from sincor2.a2a_inbound import get_fabric

    commits = [c for c in get_fabric().commits.values()
               if c["task_id"] == task_id]
    assert len(commits) == 1


def test_commit_without_key_second_attempt_409(client):
    """Without a key the retry is idempotent: returns the existing
    commitment (201) WITHOUT re-locking stake (VERIFIED-2 fix)."""
    agent_id = "idem-agent-2"
    _register_and_fund(client, agent_id)
    task_id = _sealed_task(client)
    body = {"task_id": task_id, "agent_id": agent_id,
            "commitment": _commitment(1.0, "bb" * 32, agent_id)}
    r1 = client.post("/v1/a2a/bids/commit", json=body)
    assert r1.status_code == 201
    r2 = client.post("/v1/a2a/bids/commit", json=body)
    assert r2.status_code == 201  # idempotent, NOT 409
    assert r2.get_json() == r1.get_json()


def test_commit_key_conflict_on_different_commitment(client):
    agent_id = "idem-agent-3"
    _register_and_fund(client, agent_id)
    task_id = _sealed_task(client)
    key = str(uuid.uuid4())
    body1 = {"task_id": task_id, "agent_id": agent_id,
             "commitment": _commitment(1.0, "cc" * 32, agent_id)}
    body2 = {"task_id": task_id, "agent_id": agent_id,
             "commitment": _commitment(2.0, "dd" * 32, agent_id)}
    assert client.post("/v1/a2a/bids/commit", json=body1,
                       headers={"Idempotency-Key": key}).status_code == 201
    r2 = client.post("/v1/a2a/bids/commit", json=body2,
                     headers={"Idempotency-Key": key})
    assert r2.status_code == 422


def test_reveal_idempotent(client):
    agent_id = "idem-agent-4"
    _register_and_fund(client, agent_id)
    task_id = _sealed_task(client)
    nonce = "ee" * 32
    commit_body = {"task_id": task_id, "agent_id": agent_id,
                   "commitment": _commitment(1.0, nonce, agent_id)}
    assert client.post("/v1/a2a/bids/commit", json=commit_body).status_code == 201
    # Move into the reveal window (commit phase over, reveal phase open).
    from sincor2.a2a_inbound import _now_ms, get_fabric

    get_fabric().tasks[task_id]["commit_deadline"] = _now_ms() - 1000
    reveal_body = {"task_id": task_id, "agent_id": agent_id,
                   "bid_axm": 1.0, "nonce": nonce, "estimated_seconds": 300}
    key = str(uuid.uuid4())
    r1 = client.post("/v1/a2a/bids/reveal", json=reveal_body,
                     headers={"Idempotency-Key": key})
    assert r1.status_code == 201, r1.get_json()
    r2 = client.post("/v1/a2a/bids/reveal", json=reveal_body,
                     headers={"Idempotency-Key": key})
    assert r2.status_code == 201  # replay, NOT a double-reveal error
    assert r2.headers.get("Idempotent-Replayed") == "true"
    from sincor2.a2a_inbound import get_fabric

    bids = [b for b in get_fabric().bids.values() if b["task_id"] == task_id]
    assert len(bids) == 1


# ── route: settle ──────────────────────────────────────────────────────────

def _completed_task():
    """A terminal paid task in the legacy integration store."""
    from sincor2 import a2a_integration as integ

    task = integ._new_task("lead-enrichment", "hello", "idem-caller",
                           "ctx-idem-1", axm_paid=10**18, tx_hash="0x" + "ab" * 32)
    integ._update_task(task, state=integ.TaskState.COMPLETED, output="done")
    return task


def test_settle_idempotent_single_fee_record(client, monkeypatch):
    from sincor2 import a2a_integration as integ

    calls = []
    monkeypatch.setattr(
        integ, "record_platform_fee_inflow",
        lambda **kw: calls.append(kw) or {"recorded": True},
    )
    task = _completed_task()
    body = {"task_id": task.id, "tx_hash": task.tx_hash,
            "caller_id": "idem-caller"}
    key = str(uuid.uuid4())
    r1 = client.post("/api/a2a/settle", json=body,
                     headers={"Idempotency-Key": key})
    assert r1.status_code == 200, r1.get_json()
    r2 = client.post("/api/a2a/settle", json=body,
                     headers={"Idempotency-Key": key})
    assert r2.status_code == 200
    assert r2.headers.get("Idempotent-Replayed") == "true"
    assert r2.get_json() == r1.get_json()
    # Exactly one fee write for the two same-key settles.
    assert len(calls) == 1
    assert calls[0]["task_id"] == task.id


def test_settle_key_conflict_on_different_task(client):
    t1 = _completed_task()
    t2 = _completed_task()
    key = str(uuid.uuid4())
    r1 = client.post("/api/a2a/settle",
                     json={"task_id": t1.id, "tx_hash": t1.tx_hash},
                     headers={"Idempotency-Key": key})
    assert r1.status_code == 200
    # Same key aimed at a different task -> 422, not t1's proof.
    r2 = client.post("/api/a2a/settle",
                     json={"task_id": t2.id, "tx_hash": t2.tx_hash},
                     headers={"Idempotency-Key": key})
    assert r2.status_code == 422


def test_settle_key_does_not_cross_scopes(client):
    """A settle key replayed on the task-create endpoint -> 422."""
    task = _completed_task()
    key = str(uuid.uuid4())
    r1 = client.post("/api/a2a/settle",
                     json={"task_id": task.id, "tx_hash": task.tx_hash},
                     headers={"Idempotency-Key": key})
    assert r1.status_code == 200
    r2 = client.post("/v1/a2a/tasks", json={"skill": "lead-enrichment"},
                     headers={"Idempotency-Key": key})
    assert r2.status_code == 422
