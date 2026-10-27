"""Directive #2 Branch C — fulfillment hardening.

Pins:
  ITEM 5a: when the Celery broker is DOWN (apply_async raises a
      connection error), _queue_pack_agent_job falls back to the
      existing task_queue.enqueue path — the job still lands and the
      settle succeeds. A NON-connection queue failure (RuntimeError)
      stays a REAL fulfillment failure (retryable, not papered over).
  ITEM 5b: a pack-email failure (provider stub status -> _send_pack_email
      raises) must NOT fail the settle: the failure is logged and a
      retryable failed state is persisted on the rail event's details
      (the codebase's state store). Re-settle never duplicates the
      entitlement, and a later retry after a REAL failure re-attempts
      the email (the thank-you flag is only set on success).
  ITEM 4: download responses carry Cache-Control: no-store and a
      Content-Disposition attachment header with the delivered
      filename; the download route accepts X-Delivery-Token as an
      alternative credential, validated exactly like the query token
      (invalid header token -> 404, same as an invalid query token);
      the existing query-token path is unchanged.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

import sincor2.rail_routes as rr
import sincor2.platform_payments as pp
import sincor2.x402_payments as x402
from sincor2 import payment_ledger as pl
from sincor2 import task_queue
from sincor2.governance.money_gate import reset_money_boundary

TX_HASH = "0x" + "ab" * 32

from eth_account import Account as _Account
from eth_account.messages import encode_defunct as _encode_defunct

_PAYER_ACCT = _Account.from_key(
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80")
PAYER = _PAYER_ACCT.address


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
def comms(monkeypatch):
    """Controllable email + celery queueing. state['email_mode']:
    'sent' -> provider confirms; 'stub' -> provider stub status (the
    _send_pack_email raise path)."""
    state = {"email_mode": "sent", "email_attempts": 0, "sent": [],
             "queued": []}

    def fake_send_email(self, to_email, to_name="", subject="",
                        html_content="", text_content=None, metadata=None):
        state["email_attempts"] += 1
        if state["email_mode"] == "stub":
            return {"status": "stub", "error": "provider not configured"}
        state["sent"].append({"to": to_email, "subject": subject})
        return {"status": "sent", "message_id": "msg-d2-1"}

    class _FakeResult:
        id = "celery-job-d2-1"

    def fake_apply_async(args=None, kwargs=None, queue=None, **kw):
        state["queued"].append({"args": args, "kwargs": kwargs,
                                 "queue": queue})
        return _FakeResult()

    monkeypatch.setattr(
        "sincor2.email_sender.EmailSender.send_email", fake_send_email)
    monkeypatch.setattr(
        rr.fulfill_pack_agent, "apply_async", fake_apply_async)
    state["fake_apply_async"] = fake_apply_async
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


# ---------------------------------------------------------------------------
# ITEM 5a: Celery broker down -> task_queue.enqueue fallback
# ---------------------------------------------------------------------------

def _redis_connection_error():
    from redis.exceptions import ConnectionError as RedisConnectionError
    return RedisConnectionError("Error connecting to localhost:6379")


@pytest.mark.parametrize("broker_exc", [
    ConnectionError("broker unavailable: redis://localhost:6379/0 refused"),
    _redis_connection_error(),
], ids=["builtin-connection-error", "redis-connection-error"])
def test_settle_succeeds_broker_down_job_lands_via_task_queue_enqueue(
        rail_app, rail_env, comms, monkeypatch, broker_exc):
    """apply_async raises a connection error -> fallback enqueue is used,
    its job id is the fulfillment job id, and the settle is delivered."""
    def _broker_down(*a, **k):
        raise broker_exc

    monkeypatch.setattr(rr.fulfill_pack_agent, "apply_async", _broker_down)

    enqueued = []

    def fake_enqueue(kind, payload=None, **kw):
        enqueued.append({"kind": kind, "payload": payload or {}, "kw": kw})
        return SimpleNamespace(id="taskqueue-job-d2-1")

    monkeypatch.setattr(task_queue, "enqueue", fake_enqueue)

    client = rail_app.test_client()
    cid = _checkout(client)
    res = _settle(client, cid)
    body = res.get_json()
    assert res.status_code == 200, body
    assert body["ok"] is True
    assert body["fulfillment"]["status"] == "delivered"
    assert body["fulfillment"]["job_id"] == "taskqueue-job-d2-1"

    assert len(enqueued) == 1, "job must land via task_queue.enqueue exactly once"
    assert enqueued[0]["payload"]["intake_id"] == f"intake-rail-{cid}"
    assert enqueued[0]["payload"]["pack_sku"] == "credentialing_launch"
    assert enqueued[0]["payload"]["customer_email"] == "buyer@example.com"

    db_path = rail_env["tmp"] / "revenue.db"
    assert _db_counts(db_path) == (1, 1)
    assert len(comms["sent"]) == 1
    log_lines = (rail_env["tmp"] / "platform_payments.jsonl"
                 ).read_text().splitlines()
    assert len(log_lines) == 1


def test_settle_broker_down_real_task_queue_lands_job(
        rail_app, rail_env, comms, monkeypatch):
    """Same fallback against the REAL task_queue (eager in FLASK_ENV=test):
    the returned job exists in the queue store for the pack payload."""
    def _broker_down(*a, **k):
        raise ConnectionError("broker unavailable")

    monkeypatch.setattr(rr.fulfill_pack_agent, "apply_async", _broker_down)

    client = rail_app.test_client()
    cid = _checkout(client)
    res = _settle(client, cid)
    body = res.get_json()
    assert res.status_code == 200, body
    assert body["fulfillment"]["status"] == "delivered"
    job_id = body["fulfillment"]["job_id"]
    assert job_id and job_id != "celery-job-d2-1"

    job = task_queue.get_job(job_id)
    assert job is not None, "fallback job must be recorded in the task queue"
    assert job.payload["intake_id"] == f"intake-rail-{cid}"
    assert job.payload["pack_sku"] == "credentialing_launch"
    assert job.state == task_queue.COMPLETED


# ---------------------------------------------------------------------------
# ITEM 5b: email failure is a graceful half — settle still succeeds
# ---------------------------------------------------------------------------

def test_settle_succeeds_when_pack_email_fails_retryable_state_persisted(
        rail_app, rail_env, comms, caplog):
    comms["email_mode"] = "stub"  # provider stub -> _send_pack_email raises
    client = rail_app.test_client()
    cid = _checkout(client)
    with caplog.at_level(logging.WARNING, logger="sincor.rail"):
        res = _settle(client, cid)
    body = res.get_json()
    assert res.status_code == 200, body
    assert body["ok"] is True
    assert body["fulfillment"]["status"] == "delivered"

    # persisted retryable failed state on the rail event (state store)
    details = rr._rail_event_details(f"rail-{cid}")
    assert details.get("pack_email_status") == "failed"
    assert details.get("pack_email_failed") is True
    assert details.get("pack_email_retryable") is True
    assert "stub" in str(details.get("pack_email_error") or "")
    assert not details.get("thank_you_email_sent")

    # a log was emitted for the email failure
    assert any("email" in r.getMessage().lower()
               for r in caplog.records if r.levelno >= logging.WARNING)

    # the rest of fulfillment still happened exactly once
    db_path = rail_env["tmp"] / "revenue.db"
    assert _db_counts(db_path) == (1, 1)
    alerts = pl.list_alerts(db_path)
    assert any(a.kind == "pack_email_failed" for a in alerts), alerts
    assert len(comms["queued"]) == 1
    assert comms["email_attempts"] == 1
    assert len(comms["sent"]) == 0


def test_resettle_after_email_failure_no_duplicate_entitlement(
        rail_app, rail_env, comms):
    comms["email_mode"] = "stub"
    client = rail_app.test_client()
    cid = _checkout(client)
    first = _settle(client, cid)
    assert first.get_json()["ok"] is True

    second = _settle(client, cid)
    body = second.get_json()
    assert second.status_code == 200
    assert body["status"] == "already_fulfilled"
    assert body["duplicate"] is True

    db_path = rail_env["tmp"] / "revenue.db"
    assert _db_counts(db_path) == (1, 1), "no duplicate intake/entitlement"
    assert len(comms["queued"]) == 1, "no second agent job"
    assert comms["email_attempts"] == 1, "no duplicate email on the ack path"
    log_lines = (rail_env["tmp"] / "platform_payments.jsonl"
                 ).read_text().splitlines()
    assert len(log_lines) == 1, "payment finalized exactly once"


def test_email_retried_on_resettle_after_real_fulfillment_failure(
        rail_app, rail_env, comms, monkeypatch):
    """Email fails AND the queue raises a REAL (non-connection) failure:
    fulfillment stays failed/retryable (existing pinned semantics), and
    the re-settle re-attempts the email — it was never marked sent."""
    comms["email_mode"] = "stub"

    def _boom(*a, **k):
        raise RuntimeError("broker down")

    monkeypatch.setattr(rr.fulfill_pack_agent, "apply_async", _boom)
    client = rail_app.test_client()
    cid = _checkout(client)
    first = _settle(client, cid)
    first_body = first.get_json()
    assert first_body["ok"] is False
    assert "broker down" in first_body["fulfillment"]["error"]
    event = rr._get_rail_event(f"rail-{cid}")
    assert event["fulfillment_status"] == "failed"
    assert comms["email_attempts"] == 1

    # provider + broker recover: re-settle retries fulfillment incl. email
    comms["email_mode"] = "sent"
    monkeypatch.setattr(
        rr.fulfill_pack_agent, "apply_async", comms["fake_apply_async"])
    second = _settle(client, cid)
    second_body = second.get_json()
    assert second_body["ok"] is True
    assert second_body["fulfillment"]["status"] == "delivered"
    assert comms["email_attempts"] == 2, "failed email must be retried"
    assert len(comms["sent"]) == 1

    details = rr._rail_event_details(f"rail-{cid}")
    assert details.get("thank_you_email_sent") is True
    assert details.get("pack_email_status") == "sent"
    assert not details.get("pack_email_failed")

    db_path = rail_env["tmp"] / "revenue.db"
    assert _db_counts(db_path) == (1, 1), "retry must not duplicate entitlement"
    log_lines = (rail_env["tmp"] / "platform_payments.jsonl"
                 ).read_text().splitlines()
    assert len(log_lines) == 1


# ---------------------------------------------------------------------------
# ITEM 4: download headers + X-Delivery-Token header credential
# ---------------------------------------------------------------------------

ASSET = "money_assets/instant_bi_template_pack.md"
TOKEN = "d2-delivery-token-0123456789abcdef"


@pytest.fixture()
def download_env(tmp_path, monkeypatch):
    db = tmp_path / "revenue.db"
    monkeypatch.setenv("SINCOR_REVENUE_DB", str(db))
    pl.record_pack_intake(
        db,
        intake_id="intake-d2-download",
        customer_email="buyer@example.com",
        pack_sku="digital_bi_template_pack",
        event_id="evt-d2-download",
        intake_url=f"https://getsincor.com/download/intake-d2-download?token={TOKEN}",
        details={"asset": ASSET, "delivery_token": TOKEN, "digital": True},
    )
    return {"db": db}


@pytest.fixture(scope="module")
def download_app():
    from flask import Flask
    from sincor2.rail_delivery import init_rail_delivery_routes

    app = Flask(__name__)
    app.config["TESTING"] = True
    init_rail_delivery_routes(app)
    return app


def _asset_bytes() -> bytes:
    repo_root = Path(pl.__file__).resolve().parents[2]
    return (repo_root / ASSET).read_bytes()


def test_download_response_carries_no_store_and_content_disposition(
        download_app, download_env):
    resp = download_app.test_client().get(
        f"/download/intake-d2-download?token={TOKEN}")
    assert resp.status_code == 200
    assert resp.data == _asset_bytes()
    assert resp.headers["Content-Type"] == "text/markdown; charset=utf-8"
    assert resp.headers["Cache-Control"] == "no-store"
    disposition = resp.headers["Content-Disposition"]
    assert disposition.startswith("attachment")
    assert "instant_bi_template_pack.md" in disposition


def test_download_succeeds_with_x_delivery_token_header(
        download_app, download_env):
    resp = download_app.test_client().get(
        "/download/intake-d2-download",
        headers={"X-Delivery-Token": TOKEN})
    assert resp.status_code == 200
    assert resp.data == _asset_bytes()
    assert resp.headers["Cache-Control"] == "no-store"
    assert "instant_bi_template_pack.md" in resp.headers["Content-Disposition"]


def test_download_invalid_header_token_refused_like_invalid_query_token(
        download_app, download_env):
    client = download_app.test_client()
    header_resp = client.get(
        "/download/intake-d2-download",
        headers={"X-Delivery-Token": "wrong-token"})
    query_resp = client.get("/download/intake-d2-download?token=wrong-token")
    assert header_resp.status_code == 404
    assert query_resp.status_code == 404
    # no credential at all still fails closed
    assert client.get("/download/intake-d2-download").status_code == 404
