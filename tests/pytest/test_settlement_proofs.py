"""Tests for real settlement proofs (G2.4 / backlog item 17).

The old /api/a2a/settle route promised a *signed* proof but returned plain
unsigned JSON.  The new route anchors proofs on server-side chain
verification of the payment tx, and requires an adjudicator-signed EIP-191
ruling for adjudicated tasks (fail-closed).
"""
import time

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from sincor2 import a2a_integration as a2a
from sincor2.onchain.stake_ledger import (
    ADJUDICATOR_ENV,
    reset_stake_ledger,
    stake_ledger,
)
from sincor2.settlement_proofs import (
    build_settle_ruling_message,
    canonical_statement_hash,
    verify_proof_of_settlement,
    verify_settle_ruling,
)

ONE_AXM = 10 ** 18
TX_A = "0x" + "aa" * 32
TX_B = "0x" + "bb" * 32


def _seed_task(task_id, tx_hash=TX_A, axm_paid_wei=ONE_AXM, free_call=False):
    task = a2a.A2ATask(
        id=task_id,
        context_id="ctx-" + task_id,
        skill_id="lead-enrichment",
        input_text="test",
        caller_id="0xCaller",
        state=a2a.TaskState.COMPLETED,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:01Z",
        output="done",
        axm_paid=axm_paid_wei,
        tx_hash=tx_hash,
        metadata={"free_call": free_call},
    )
    with a2a._store_lock:
        a2a._tasks[task_id] = task
    return task


@pytest.fixture
def _clean_tasks():
    yield
    with a2a._store_lock:
        for key in [k for k in a2a._tasks if k.startswith("settle-proof-")]:
            del a2a._tasks[key]


@pytest.fixture
def adjudicator():
    return Account.create()


@pytest.fixture
def _adjudicator_env(monkeypatch, adjudicator, tmp_path):
    monkeypatch.setenv(ADJUDICATOR_ENV, adjudicator.address)
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    yield adjudicator
    reset_stake_ledger()


def _signed_ruling(key, task_id, tx_hash=TX_A, axm_paid_wei=ONE_AXM,
                   skew_ms=60_000):
    expires = int(time.time() * 1000) + skew_ms
    message = build_settle_ruling_message(
        task_id, tx_hash, axm_paid_wei, key.address, expires)
    sig = Account.sign_message(
        encode_defunct(text=message), private_key=key.key).signature.hex()
    return {
        "task_id": task_id,
        "tx_hash": tx_hash,
        "axm_paid_wei": str(axm_paid_wei),
        "adjudicator_address": key.address,
        "expires_at_ms": expires,
        "signature": "0x" + sig,
    }


# ── unit: ruling verification ────────────────────────────────────────────────

def test_ruling_message_binds_all_fields():
    base = build_settle_ruling_message("t1", TX_A, ONE_AXM, "0xabc", 123)
    assert base != build_settle_ruling_message("t2", TX_A, ONE_AXM, "0xabc", 123)
    assert base != build_settle_ruling_message("t1", TX_B, ONE_AXM, "0xabc", 123)
    assert base != build_settle_ruling_message("t1", TX_A, 2 * ONE_AXM, "0xabc", 123)


def test_verify_settle_ruling_happy_path(adjudicator):
    ruling = _signed_ruling(adjudicator, "t1")
    ok, reason = verify_settle_ruling(
        ruling, task_id="t1", tx_hash=TX_A, axm_paid_wei=ONE_AXM,
        expected_adjudicator=adjudicator.address)
    assert (ok, reason) == (True, "ok")


def test_verify_settle_ruling_wrong_signer(adjudicator):
    impostor = Account.create()
    ruling = _signed_ruling(impostor, "t1")
    ok, reason = verify_settle_ruling(
        ruling, task_id="t1", tx_hash=TX_A, axm_paid_wei=ONE_AXM,
        expected_adjudicator=adjudicator.address)
    assert not ok and reason == "not the adjudicator"


def test_verify_settle_ruling_expired(adjudicator):
    ruling = _signed_ruling(adjudicator, "t1", skew_ms=-60_000)
    ok, reason = verify_settle_ruling(
        ruling, task_id="t1", tx_hash=TX_A, axm_paid_wei=ONE_AXM,
        expected_adjudicator=adjudicator.address)
    assert not ok and "expired" in reason


def test_verify_settle_ruling_replay_on_other_task(adjudicator):
    ruling = _signed_ruling(adjudicator, "t1")
    ok, _ = verify_settle_ruling(
        ruling, task_id="t2", tx_hash=TX_A, axm_paid_wei=ONE_AXM,
        expected_adjudicator=adjudicator.address)
    assert not ok


# ── unit: offline proof verification ─────────────────────────────────────────

def _statement(task_id="t1", **overrides):
    stmt = {
        "task_id": task_id, "tx_hash": TX_A, "axm_paid_wei": str(ONE_AXM),
        "payment_verification": "dev_bypass", "disputed": False,
    }
    stmt.update(overrides)
    return stmt


def _proof(stmt):
    return {"proof_of_settlement": {**stmt,
                                   "proof_hash": canonical_statement_hash(stmt)}}


def test_verify_proof_happy_path():
    ok, reason = verify_proof_of_settlement(_proof(_statement()))
    assert ok and "dev_bypass" in reason  # honest non-onchain label


def test_verify_proof_tamper_rejected():
    proof = _proof(_statement())
    proof["proof_of_settlement"]["axm_paid_wei"] = str(999 * ONE_AXM)
    ok, reason = verify_proof_of_settlement(proof)
    assert not ok and "tampered" in reason


def test_verify_proof_smuggled_ruling_rejected(adjudicator):
    # A ruling validly signed for this proof, but with its declared tx_hash
    # tampered after signing: the binding check must catch the mismatch.
    ruling = _signed_ruling(adjudicator, "t1", tx_hash=TX_A)
    ruling["tx_hash"] = TX_B
    stmt = _statement(disputed=True, adjudicator_ruling=ruling)
    ok, reason = verify_proof_of_settlement(_proof(stmt))
    assert not ok and "ruling tx_hash does not match proof" in reason


def test_verify_proof_requires_verification_label():
    stmt = _statement()
    del stmt["payment_verification"]
    ok, reason = verify_proof_of_settlement(_proof(stmt))
    assert not ok and "payment_verification" in reason


# ── route: fail-closed behavior ──────────────────────────────────────────────

def test_settle_unknown_task_still_404(client):
    resp = client.post("/api/a2a/settle",
                       json={"task_id": "nope", "tx_hash": TX_A})
    assert resp.status_code == 404


def test_settle_mismatched_tx_hash_400(client, _clean_tasks):
    _seed_task("settle-proof-1", tx_hash=TX_A)
    resp = client.post("/api/a2a/settle",
                       json={"task_id": "settle-proof-1", "tx_hash": TX_B})
    assert resp.status_code == 400


def test_settle_malformed_tx_hash_400(client, _clean_tasks):
    _seed_task("settle-proof-2", tx_hash=TX_A)
    resp = client.post("/api/a2a/settle",
                       json={"task_id": "settle-proof-2", "tx_hash": "0xabc"})
    assert resp.status_code == 400


def test_settle_free_task_400(client, _clean_tasks):
    _seed_task("settle-proof-3", tx_hash="", axm_paid_wei=0, free_call=True)
    resp = client.post("/api/a2a/settle",
                       json={"task_id": "settle-proof-3", "tx_hash": TX_A})
    assert resp.status_code == 400


def test_settle_simulated_tx_400(client, _clean_tasks):
    _seed_task("settle-proof-4", tx_hash="0xSIMULATED-123", axm_paid_wei=ONE_AXM)
    resp = client.post("/api/a2a/settle",
                       json={"task_id": "settle-proof-4",
                             "tx_hash": "0xSIMULATED-123"})
    assert resp.status_code == 400


# ── route: happy path + disputed path ────────────────────────────────────────

def test_settle_undisputed_paid_task_200_and_verifies(client, _clean_tasks):
    _seed_task("settle-proof-5", tx_hash=TX_A)
    resp = client.post("/api/a2a/settle",
                       json={"task_id": "settle-proof-5", "tx_hash": TX_A})
    assert resp.status_code == 200
    proof = resp.get_json()
    pos = proof["proof_of_settlement"]
    assert pos["task_id"] == "settle-proof-5"
    assert pos["tx_hash"] == TX_A
    # Test env bypasses the RPC: the proof must say so honestly.
    assert pos["payment_verification"] == "dev_bypass"
    ok, reason = verify_proof_of_settlement(proof)
    assert ok and "dev_bypass" in reason  # honest non-onchain label


def test_settle_disputed_task_requires_ruling_403(client, _clean_tasks,
                                                  _adjudicator_env):
    task_id = "settle-proof-6"
    _seed_task(task_id, tx_hash=TX_A)
    stake_ledger()._event("adjudicated", agent_id="winner-a", task_id=task_id,
                          upheld=True)
    resp = client.post("/api/a2a/settle",
                       json={"task_id": task_id, "tx_hash": TX_A})
    assert resp.status_code == 403


def test_settle_disputed_task_forged_ruling_403(client, _clean_tasks,
                                                _adjudicator_env):
    task_id = "settle-proof-7"
    _seed_task(task_id, tx_hash=TX_A)
    stake_ledger()._event("adjudicated", agent_id="winner-a", task_id=task_id,
                          upheld=True)
    impostor = Account.create()
    resp = client.post(
        "/api/a2a/settle",
        json={"task_id": task_id, "tx_hash": TX_A,
              "adjudicator_ruling": _signed_ruling(impostor, task_id)})
    assert resp.status_code == 403


def test_settle_disputed_task_valid_ruling_200_and_verifies(
        client, _clean_tasks, _adjudicator_env):
    task_id = "settle-proof-8"
    _seed_task(task_id, tx_hash=TX_A)
    stake_ledger()._event("adjudicated", agent_id="winner-a", task_id=task_id,
                          upheld=True)
    ruling = _signed_ruling(_adjudicator_env, task_id)
    resp = client.post(
        "/api/a2a/settle",
        json={"task_id": task_id, "tx_hash": TX_A,
              "adjudicator_ruling": ruling})
    assert resp.status_code == 200
    proof = resp.get_json()
    pos = proof["proof_of_settlement"]
    assert pos["disputed"] is True
    assert pos["adjudicator_ruling"]["adjudicator_address"] == \
        _adjudicator_env.address
    ok, reason = verify_proof_of_settlement(proof)
    assert ok and "dev_bypass" in reason  # honest non-onchain label


def test_settle_smuggled_ruling_on_clean_task_403(client, _clean_tasks,
                                                  _adjudicator_env):
    task_id = "settle-proof-9"
    _seed_task(task_id, tx_hash=TX_A)
    impostor = Account.create()
    resp = client.post(
        "/api/a2a/settle",
        json={"task_id": task_id, "tx_hash": TX_A,
              "adjudicator_ruling": _signed_ruling(impostor, task_id)})
    assert resp.status_code == 403
