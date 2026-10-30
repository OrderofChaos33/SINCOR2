"""WREN PR 10 — pack job queue hardening.

Pins:
  * celery_app conf bounds broker + result-backend waits so an
    unreachable/hung broker fails in ~2s, never ~19s: the Redis result
    backend's default reconnect policy (20 retries x 1s) otherwise
    stalls apply_async inside send_task before the publish is even
    attempted (measured on this tree).
  * _celery_app is None (no Celery configured) -> _queue_pack_agent_job
    falls back to the in-process task_queue exactly like the
    broker-down path: settle is 200/delivered, job enqueued once.
  * A REAL unreachable broker (real Celery app, real apply_async,
    production conf) -> broker-unavailable fallback engages fast
    (< 5s), settle 200/delivered, exactly one fallback enqueue.
  * Retry settle after a no-Celery settle is idempotent: no duplicate
    job, intake, payment, or email.
  * When the job cannot be queued ANYWHERE (Celery absent AND the
    task_queue fallback raises), the verified payment stays recorded,
    fulfillment_status stays 'pending' (NOT 'failed'), the settle is a
    structured non-5xx, and a later re-settle re-queues and delivers.
    (Distinct from the Directive #2 pin: a NON-connection apply_async
    error with Celery present is still a real fulfillment failure.)
"""
from __future__ import annotations

import sqlite3
import time
from types import SimpleNamespace

import pytest

import sincor2.rail_routes as rr
import sincor2.platform_payments as pp
import sincor2.x402_payments as x402
from sincor2 import task_queue
from sincor2.governance.money_gate import reset_money_boundary

TX_HASH = "0x" + "ab" * 32

from eth_account import Account as _Account
from eth_account.messages import encode_defunct as _encode_defunct

_PAYER_ACCT = _Account.from_key(
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80")
PAYER = _PAYER_ACCT.address

UNREACHABLE = "redis://127.0.0.1:6390/0"  # nothing listens on 6390


def _bind_challenge(challenge_id, resource_id, acct=_PAYER_ACCT):
    sig = acct.sign_message(_encode_defunct(
        text=x402.challenge_bind_message(
            challenge_id, resource_id, acct.address))).signature.hex()
    bound = x402.bind_challenge_payer(challenge_id, acct.address, sig)
    assert bound.get("ownership_proven") is True, bound
    return bound


@pytest.fixture(autouse=True)
def _fresh_boundary():
    reset_money_boundary()
    yield
    reset_money_boundary()


@pytest.fixture()
def rail_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    monkeypatch.setenv("SINCOR_REVENUE_DB", str(tmp_path / "revenue.db"))
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        "sincor2.treasury_inflow._LEDGER_PATH", tmp_path / "inflow.jsonl")
    monkeypatch.setattr("sincor2.agent_billing._log_path",
                        lambda: tmp_path / "platform_payments.jsonl")

    treasury = x402.get_resource(
        "rail-pack-credentialing_launch")["treasury"]
    token_addr = pp.token_address("USDC")
    amount_atomic = 297_000_000  # $297.00 in 6-decimal USDC

    def _receipt():
        return {
            "status": "0x1",
            "blockNumber": hex(100),
            "logs": [{
                "address": token_addr,
                "topics": [
                    pp.TRANSFER_TOPIC,
                    "0x" + "0" * 24 + PAYER[2:],
                    "0x" + "0" * 24 + treasury[2:].lower(),
                ],
                "data": hex(amount_atomic),
            }],
        }

    def _fake_rpc(method, params, attempts=3):
        if method == "eth_getTransactionReceipt":
            assert params == [TX_HASH], params
            return _receipt()
        if method == "eth_blockNumber":
            return hex(112)
        raise AssertionError(f"unexpected RPC method {method}")

    monkeypatch.setattr(pp, "_rpc_call", _fake_rpc)
    x402.init_x402_db()
    return {"tx_hash": TX_HASH, "payer": PAYER, "tmp": tmp_path}


@pytest.fixture()
def email_ok(monkeypatch):
    """Buyer email always sends; apply_async is NOT stubbed here."""
    state = {"sent": [], "attempts": 0}

    def fake_send_email(self, to_email, to_name="", subject="",
                        html_content="", text_content=None, metadata=None):
        state["attempts"] += 1
        state["sent"].append({"to": to_email, "subject": subject})
        return {"status": "sent", "message_id": "msg-wren10-1"}

    monkeypatch.setattr(
        "sincor2.email_sender.EmailSender.send_email", fake_send_email)
    return state


@pytest.fixture(scope="module")
def rail_app():
    from flask import Flask
    from sincor2.rail_delivery import init_rail_delivery_routes

    app = Flask(__name__)
    app.config["TESTING"] = True
    rr.init_rail_routes(app)
    init_rail_delivery_routes(app)
    return app


def _checkout(client, sku="credentialing_launch"):
    res = client.post("/rail/checkout", json={
        "pack_sku": sku, "payer_wallet": PAYER,
        "buyer_email": "buyer@example.com", "buyer_name": "Test Buyer"})
    assert res.status_code == 402, res.get_json()
    body = res.get_json()
    _bind_challenge(body["challenge_id"], body["resource_id"])
    return body["challenge_id"]


def _settle(client, cid):
    return client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": TX_HASH, "payer_wallet": PAYER,
        "buyer_email": "buyer@example.com", "buyer_name": "Test Buyer"})


def _db_counts(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return (conn.execute("SELECT COUNT(*) FROM rail_events").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM pack_intakes").fetchone()[0])
    finally:
        conn.close()


def _counting_enqueue(monkeypatch, real_enqueue=None):
    """Wrap the REAL task_queue.enqueue with a counter (calls through).

    Pass real_enqueue explicitly if task_queue.enqueue is currently
    patched (the wrapper must not wrap the patch)."""
    if real_enqueue is None:
        real_enqueue = task_queue.enqueue
    calls = []

    def counting(kind, payload=None, **kw):
        calls.append({"kind": kind, "payload": payload or {}})
        return real_enqueue(kind, payload, **kw)

    monkeypatch.setattr(task_queue, "enqueue", counting)
    return calls


# ---------------------------------------------------------------------------
# celery_app conf: broker + backend waits are bounded
# ---------------------------------------------------------------------------

def test_celery_conf_bounds_broker_and_backend_waits():
    from sincor2 import celery_app

    conf = celery_app.celery.conf
    assert conf["broker_transport_options"] == {
        "socket_timeout": 2, "socket_connect_timeout": 2}
    assert conf["broker_connection_timeout"] == 2
    assert conf["task_publish_retry_policy"] == {
        "max_retries": 1, "interval_start": 0, "interval_max": 0.2}
    # The ~19s stall lives in the Redis result backend's reconnect
    # policy (base default: 20 retries, 1s steps). It must be bounded
    # via the backend transport options.
    backend_policy = conf["result_backend_transport_options"]["retry_policy"]
    assert backend_policy["max_retries"] <= 1
    assert backend_policy["interval_max"] <= 0.2


# ---------------------------------------------------------------------------
# No Celery configured -> task_queue fallback, settle succeeds
# ---------------------------------------------------------------------------

def test_settle_no_celery_app_falls_back_to_task_queue_once(
        rail_app, rail_env, email_ok, monkeypatch):
    monkeypatch.setattr(rr, "_celery_app", None)
    enqueued = _counting_enqueue(monkeypatch)

    client = rail_app.test_client()
    cid = _checkout(client)
    start = time.monotonic()
    res = _settle(client, cid)
    elapsed = time.monotonic() - start

    body = res.get_json()
    assert res.status_code == 200, body
    assert elapsed < 5, f"settle took {elapsed:.1f}s with no Celery"
    assert body["ok"] is True
    assert body["fulfillment"]["status"] == "delivered"

    assert len(enqueued) == 1, "job must land via task_queue exactly once"
    assert enqueued[0]["payload"]["intake_id"] == f"intake-rail-{cid}"
    assert enqueued[0]["payload"]["pack_sku"] == "credentialing_launch"
    assert enqueued[0]["payload"]["customer_email"] == "buyer@example.com"

    job_id = body["fulfillment"]["job_id"]
    assert job_id and job_id != "celery-job-d2-1"
    job = task_queue.get_job(job_id)
    assert job is not None, "fallback job must be recorded in the task queue"
    assert job.state == task_queue.COMPLETED

    db_path = rail_env["tmp"] / "revenue.db"
    assert _db_counts(db_path) == (1, 1)
    assert len(email_ok["sent"]) == 1


def test_resettle_after_no_celery_settle_is_idempotent(
        rail_app, rail_env, email_ok, monkeypatch):
    monkeypatch.setattr(rr, "_celery_app", None)
    enqueued = _counting_enqueue(monkeypatch)

    client = rail_app.test_client()
    cid = _checkout(client)
    first = _settle(client, cid)
    assert first.status_code == 200, first.get_json()
    assert first.get_json()["ok"] is True

    second = _settle(client, cid)
    body = second.get_json()
    assert second.status_code == 200, body
    assert body["status"] == "already_fulfilled"
    assert body["duplicate"] is True

    assert len(enqueued) == 1, "retry must not enqueue a second job"
    db_path = rail_env["tmp"] / "revenue.db"
    assert _db_counts(db_path) == (1, 1), "no duplicate intake/entitlement"
    assert len(email_ok["sent"]) == 1, "no duplicate email"
    log_lines = (rail_env["tmp"] / "platform_payments.jsonl"
                 ).read_text().splitlines()
    assert len(log_lines) == 1, "payment finalized exactly once"


# ---------------------------------------------------------------------------
# Real unreachable broker -> bounded stall, fallback engages
# ---------------------------------------------------------------------------

def _unreachable_broker_task(monkeypatch):
    """A REAL Celery app + task pointed at an unreachable broker, using
    the production celery_app conf (so this test tracks the real
    timeout settings). Returns the task object to patch in."""
    from celery import Celery

    from sincor2 import celery_app as prod

    app = Celery("sincor2-wren10-probe", broker=UNREACHABLE,
                 backend=UNREACHABLE)
    keys = ("task_serializer", "accept_content", "result_serializer",
            "broker_connection_retry_on_startup",
            "broker_transport_options", "broker_connection_timeout",
            "task_publish_retry_policy",
            "result_backend_transport_options")
    app.conf.update({k: prod.celery.conf[k] for k in keys
                     if prod.celery.conf.get(k) is not None})

    @app.task(bind=True, name="sincor2.fulfill_pack_agent",
              queue=rr.PACK_AGENT_QUEUE)
    def _probe_task(self=None, intake_id="", pack_sku="",
                    customer_email=""):
        raise AssertionError("worker body must not run in this test")

    monkeypatch.setattr(rr, "_celery_app", app)
    monkeypatch.setattr(rr, "fulfill_pack_agent", _probe_task)
    return _probe_task


def test_settle_unreachable_broker_falls_back_fast(
        rail_app, rail_env, email_ok, monkeypatch):
    _unreachable_broker_task(monkeypatch)

    enqueued = []

    def fake_enqueue(kind, payload=None, **kw):
        enqueued.append({"kind": kind, "payload": payload or {}})
        return SimpleNamespace(id="taskqueue-job-wren10-1")

    monkeypatch.setattr(task_queue, "enqueue", fake_enqueue)

    client = rail_app.test_client()
    cid = _checkout(client)
    start = time.monotonic()
    res = _settle(client, cid)
    elapsed = time.monotonic() - start

    body = res.get_json()
    assert res.status_code == 200, body
    assert elapsed < 5, (
        f"unreachable-broker settle took {elapsed:.1f}s — the broker/"
        "backend wait bounds are not effective")
    assert body["ok"] is True
    assert body["fulfillment"]["status"] == "delivered"
    assert body["fulfillment"]["job_id"] == "taskqueue-job-wren10-1"
    assert len(enqueued) == 1, "exactly one fallback enqueue"
    assert enqueued[0]["payload"]["intake_id"] == f"intake-rail-{cid}"


# ---------------------------------------------------------------------------
# Queue failure everywhere -> payment recorded, status stays pending,
# later re-queue succeeds
# ---------------------------------------------------------------------------

def test_queue_failure_leaves_pending_then_requeue_succeeds(
        rail_app, rail_env, email_ok, monkeypatch):
    monkeypatch.setattr(rr, "_celery_app", None)
    real_enqueue = task_queue.enqueue  # capture BEFORE patching it dead

    def dead_enqueue(kind, payload=None, **kw):
        raise RuntimeError("task_queue store unavailable")

    monkeypatch.setattr(task_queue, "enqueue", dead_enqueue)

    client = rail_app.test_client()
    cid = _checkout(client)
    res = _settle(client, cid)
    body = res.get_json()

    # Never a 5xx after a verified payment; the payment is recorded and
    # the event stays pending (re-queueable), NOT failed.
    assert res.status_code < 500, body
    assert body["ok"] is False
    event = rr._get_rail_event(f"rail-{cid}")
    assert event["fulfillment_status"] == "pending", event
    log_lines = (rail_env["tmp"] / "platform_payments.jsonl"
                 ).read_text().splitlines()
    assert len(log_lines) == 1, "verified payment recorded exactly once"
    db_path = rail_env["tmp"] / "revenue.db"
    assert _db_counts(db_path) == (1, 1), "intake recorded, no duplicates"

    # The queue recovers: a re-settle re-queues and delivers, still
    # without duplicating the payment, the intake, or the email.
    enqueued = _counting_enqueue(monkeypatch, real_enqueue=real_enqueue)
    second = _settle(client, cid)
    second_body = second.get_json()
    assert second.status_code == 200, second_body
    assert second_body["ok"] is True
    assert second_body["fulfillment"]["status"] == "delivered"
    assert len(enqueued) == 1, "re-queue enqueues exactly one job"

    event = rr._get_rail_event(f"rail-{cid}")
    assert event["fulfillment_status"] == "delivered"
    assert _db_counts(db_path) == (1, 1)
    log_lines = (rail_env["tmp"] / "platform_payments.jsonl"
                 ).read_text().splitlines()
    assert len(log_lines) == 1, "payment still recorded exactly once"
    assert len(email_ok["sent"]) == 1, "buyer email sent exactly once"
