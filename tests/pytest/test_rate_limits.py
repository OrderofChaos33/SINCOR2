"""WREN PR 4: per-route rate limits + open-unbound challenge caps.

Money-rail routes (/rail/checkout, /rail/settle, /download/<id>) previously
had only the app-wide default limiter (10000/day, 1000/hour). This suite
pins, against the real production app (sincor2.mvp_app):

  - /rail/checkout is limited to 30/hour per client IP (the limiter's
    default key: get_remote_address behind ProxyFix x_for=1); the 31st
    call answers 429 JSON {"error": "rate_limited"} and a different IP
    is unaffected.
  - A spoofed X-Forwarded-For prefix does NOT reset the bucket: with
    x_for=1 only the last (proxy-appended) hop keys the bucket.
  - /rail/settle keeps working within limits — an idempotent re-settle
    of a fulfilled challenge still returns 200.
  - The first GET /download/<unknown> is still the fail-closed 404,
    not a 429.
  - Challenge minting is capped per source (wallet when known, else
    IP): the 21st OPEN UNBOUND challenge from one source is refused
    with 429 {"error": "rate_limited"}; another source can still mint.
  - Expired, never-fulfilled x402_challenges rows are purged by
    purge_expired_challenges(), wired into the daily-ops scheduler job.

The "chain" is a canned _rpc_call stub: zero network, zero keys.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

import sincor2.platform_payments as pp
import sincor2.rail_routes as rr
import sincor2.x402_payments as x402
from sincor2.governance.money_gate import reset_money_boundary

# Well-known PUBLIC Hardhat dev account #0 — test-only, never funded.
_PAYER_ACCT = Account.from_key(
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80")
PAYER = _PAYER_ACCT.address
TX_HASH = "0x" + "ab" * 32
PACK_SKU = "digital_bi_template_pack"  # $97 digital pack


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def mvp_app_obj(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("wren-pr4")
    os.environ["ORDERS_DB_PATH"] = str(tmp / "orders.db")
    os.environ["SINCOR_REVENUE_DB"] = str(tmp / "revenue.db")
    os.environ["SINCOR_DATA_DIR"] = str(tmp / "data")
    from sincor2.mvp_app import app as mvp_app
    mvp_app.config["TESTING"] = True
    mvp_app.config["SERVER_NAME"] = None
    return mvp_app


@pytest.fixture(autouse=True)
def _fresh_boundary():
    reset_money_boundary()
    yield
    reset_money_boundary()


@pytest.fixture()
def client(mvp_app_obj, tmp_path, monkeypatch):
    """Hermetic stores + a clean limiter slate per test."""
    from sincor2.mvp_app import limiter

    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    monkeypatch.setenv("SINCOR_REVENUE_DB", str(tmp_path / "revenue.db"))
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        "sincor2.treasury_inflow._LEDGER_PATH", tmp_path / "inflow.jsonl")
    monkeypatch.setattr("sincor2.agent_billing._log_path",
                        lambda: tmp_path / "platform_payments.jsonl")
    limiter.reset()
    x402.init_x402_db()
    yield mvp_app_obj.test_client()
    limiter.reset()


@pytest.fixture()
def chain(monkeypatch):
    """Canned RPC receipt for a $97 USDC payment PAYER -> treasury,
    plus captured outbound email (nothing leaves the process)."""
    treasury = x402.get_resource(
        "rail-pack-digital_bi_template_pack")["treasury"]
    token_addr = pp.token_address("USDC")

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
                "data": hex(97_000_000),  # $97.00 in 6-decimal USDC
            }],
        }

    def _fake_rpc(method, params, attempts=3):
        if method == "eth_getTransactionReceipt":
            return _receipt()
        if method == "eth_blockNumber":
            return hex(112)  # 13 confirmations — past finality depth
        raise AssertionError(f"unexpected RPC method {method}")

    monkeypatch.setattr(pp, "_rpc_call", _fake_rpc)

    sent = []

    def fake_send_email(self, to_email, to_name="", subject="",
                        html_content="", text_content=None, metadata=None):
        sent.append({"to": to_email, "subject": subject})
        return {"status": "sent", "message_id": "msg-test-pr4"}

    def boom_apply_async(*a, **k):
        raise AssertionError(
            "digital fulfillment must not queue a vertical-agent job")

    monkeypatch.setattr(
        "sincor2.email_sender.EmailSender.send_email", fake_send_email)
    monkeypatch.setattr(rr.fulfill_pack_agent, "apply_async",
                        boom_apply_async)
    return {"sent": sent}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _checkout(client, *, ip=None, wallet=None, sku=PACK_SKU, headers=None):
    body = {"pack_sku": sku}
    if wallet:
        body["payer_wallet"] = wallet
    kwargs = {}
    if ip:
        kwargs["environ_base"] = {"REMOTE_ADDR": ip}
    if headers:
        kwargs["headers"] = headers
    return client.post("/rail/checkout", json=body, **kwargs)


def _wallet(i: int) -> str:
    return f"0x{i:040x}"


# ---------------------------------------------------------------------------
# per-route limiter: /rail/checkout 30/hour per IP
# ---------------------------------------------------------------------------

def test_checkout_31st_from_one_ip_is_429_json_other_ip_unaffected(client):
    ip = "203.0.113.10"
    for i in range(30):
        res = _checkout(client, ip=ip, wallet=_wallet(i + 1))
        assert res.status_code == 402, (i, res.get_json())
    res = _checkout(client, ip=ip, wallet=_wallet(10_001))
    assert res.status_code == 429, res.get_json()
    assert res.get_json()["error"] == "rate_limited"
    # A different client IP has its own bucket and is unaffected.
    res = _checkout(client, ip="203.0.113.11", wallet=_wallet(10_002))
    assert res.status_code == 402, res.get_json()


def test_xff_spoof_prefix_does_not_reset_checkout_bucket(client):
    # Peer is the single trusted proxy hop; the client IP is the LAST
    # X-Forwarded-For entry (the one the proxy appended). Rotating the
    # spoofed prefix must not mint a fresh bucket.
    peer = "198.51.100.7"
    client_ip = "203.0.113.99"
    for i in range(30):
        res = _checkout(
            client, ip=peer, wallet=_wallet(20_000 + i),
            headers={"X-Forwarded-For": f"10.1.0.{i}, {client_ip}"})
        assert res.status_code == 402, (i, res.get_json())
    res = _checkout(
        client, ip=peer, wallet=_wallet(30_000),
        headers={"X-Forwarded-For": f"192.0.2.55, {client_ip}"})
    assert res.status_code == 429, res.get_json()
    assert res.get_json()["error"] == "rate_limited"


# ---------------------------------------------------------------------------
# settle stays usable within limits (idempotent retry)
# ---------------------------------------------------------------------------

def test_settle_idempotent_retry_within_limits_returns_200(client, chain):
    res = _checkout(client, wallet=PAYER)
    assert res.status_code == 402, res.get_json()
    body = res.get_json()
    challenge_id, resource_id = body["challenge_id"], body["resource_id"]

    sig = _PAYER_ACCT.sign_message(encode_defunct(
        text=x402.challenge_bind_message(
            challenge_id, resource_id, PAYER))).signature.hex()
    bound = client.post("/api/x402/challenge-bind", json={
        "challenge_id": challenge_id, "payer_wallet": PAYER,
        "signature": sig})
    assert bound.get_json().get("ownership_proven") is True, bound.get_json()

    settle_body = {
        "challenge_id": challenge_id, "tx_hash": TX_HASH,
        "payer_wallet": PAYER, "buyer_email": "buyer@example.com",
    }
    first = client.post("/rail/settle", json=settle_body)
    assert first.status_code == 200, first.get_json()
    # Idempotent retry inside the 60/hour settle budget: still 200.
    retry = client.post("/rail/settle", json=settle_body)
    assert retry.status_code == 200, retry.get_json()


# ---------------------------------------------------------------------------
# download: fail-closed 404 comes before any rate limiting
# ---------------------------------------------------------------------------

def test_download_first_get_is_404_not_429(client):
    res = client.get("/download/test")
    assert res.status_code == 404, res.status_code
    res = client.get("/download/test")
    assert res.status_code == 404, res.status_code


def test_download_route_limited_at_30_per_hour(client):
    # Pins the 30/hour download wrap — and that the defensive /intake
    # scan did NOT also wrap this view (its rule is /download/<intake_id>,
    # which does not start with /intake): at 20/hour the 21st call would
    # already refuse.
    ip = "203.0.113.20"
    for i in range(30):
        res = client.get("/download/test", environ_base={"REMOTE_ADDR": ip})
        assert res.status_code == 404, (i, res.status_code)
    res = client.get("/download/test", environ_base={"REMOTE_ADDR": ip})
    assert res.status_code == 429, res.status_code
    assert res.get_json()["error"] == "rate_limited"


# ---------------------------------------------------------------------------
# open-unbound challenge cap per source
# ---------------------------------------------------------------------------

def test_21st_open_unbound_challenge_from_one_wallet_is_429(client):
    wallet = "0x" + "f0" * 20
    for i in range(20):
        res = _checkout(client, wallet=wallet)
        assert res.status_code == 402, (i, res.get_json())
        assert res.get_json()["challenge_id"]
    res = _checkout(client, wallet=wallet)
    assert res.status_code == 429, res.get_json()
    assert res.get_json()["error"] == "rate_limited"
    assert not res.get_json().get("challenge_id")
    # Another source can still mint.
    res = _checkout(client, wallet="0x" + "0f" * 20)
    assert res.status_code == 402, res.get_json()
    assert res.get_json()["challenge_id"]


# ---------------------------------------------------------------------------
# expired-challenge purge + scheduler wiring
# ---------------------------------------------------------------------------

def _insert_challenge(db_path, challenge_id, *, status, expires_at):
    now = datetime.now(timezone.utc)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """INSERT INTO x402_challenges
               (challenge_id, resource_id, amount_atomic, amount_display,
                payer_wallet, status, created_at, expires_at)
               VALUES (?, 'rail-pack-digital_bi_template_pack', '97000000',
                       97.0, '', ?, ?, ?)""",
            (challenge_id, status, now.isoformat(), expires_at.isoformat()))
        conn.commit()
    finally:
        conn.close()


def test_purge_expired_challenges_deletes_only_expired_pending(client):
    db_path = os.environ["ORDERS_DB_PATH"]
    now = datetime.now(timezone.utc)
    _insert_challenge(db_path, "x402-expired-pending", status="pending",
                      expires_at=now - timedelta(hours=1))
    _insert_challenge(db_path, "x402-expired-fulfilled", status="fulfilled",
                      expires_at=now - timedelta(hours=1))
    _insert_challenge(db_path, "x402-live-pending", status="pending",
                      expires_at=now + timedelta(hours=1))

    purged = x402.purge_expired_challenges()
    assert purged == 1

    conn = sqlite3.connect(db_path)
    try:
        remaining = {r[0] for r in conn.execute(
            "SELECT challenge_id FROM x402_challenges")}
    finally:
        conn.close()
    assert "x402-expired-pending" not in remaining
    assert "x402-expired-fulfilled" in remaining
    assert "x402-live-pending" in remaining


def test_daily_ops_job_purges_expired_challenges(monkeypatch):
    from sincor2 import scheduler

    calls = []
    monkeypatch.setattr(
        "sincor2.daily_ops.run_daily",
        lambda include_buyers=False: {"wallet_watch": {"alerts": []}})
    monkeypatch.setattr(
        x402, "purge_expired_challenges",
        lambda: calls.append(1) or 0)
    scheduler._daily_ops_job()
    assert calls == [1]
