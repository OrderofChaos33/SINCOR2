"""WREN PR 4: per-route rate limits + open-unbound challenge caps.

Money-rail routes (/rail/checkout, /rail/settle, /download/<id>) previously
had only the app-wide default limiter (10000/day, 1000/hour). This suite
pins, against the real production app (sincor2.mvp_app):

  - /rail/checkout is limited to 30/hour per client IP (the limiter's
    default key: get_remote_address behind ProxyFix x_for=1); the 31st
    call answers 429 JSON {"error": "rate_limited"} and a different IP
    is unaffected.
  - A spoofed X-Forwarded-For prefix does NOT reset the bucket: with
    ProxyFix ACTIVE (x_for=1) only the last (proxy-appended) hop keys
    the bucket, and a different last hop keys a different bucket.
  - /rail/settle keeps working within limits — an idempotent re-settle
    of a fulfilled challenge still returns 200.
  - REPAIR pins: settle limiting is keyed per (client, challenge_id),
    so junk settles against OTHER challenges cannot strand a buyer
    whose challenge TTL (900s) is shorter than the limiter window
    (1h); settles against an already-fulfilled challenge (the
    proven-duplicate ack path) get a bounded but generous exemption
    (600/hour per (client, challenge) — far beyond any real retry
    pattern, finite while the ack path is not yet proof-gated);
    first settles against one unfulfilled challenge are still capped
    at 60/hour per (client, challenge). The limiter extracts
    challenge_id exactly as the settle view does (force-parsed JSON,
    no form fallback), so re-sending the same body under another
    Content-Type cannot switch buckets and dilute the cap.
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

import json
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


def test_xff_spoof_prefix_does_not_reset_checkout_bucket(client,
                                                          mvp_app_obj):
    # ProxyFix is wrapped around the WSGI app at import ONLY when
    # TRUST_PROXY / RAILWAY_ENVIRONMENT is set (mvp_app top), which the
    # test process does not set — without wrapping it here exactly as
    # production does, remote_addr never varies with the XFF header and
    # this test pins nothing. Empirically (werkzeug ProxyFix, x_for=1):
    # remote_addr becomes the LAST X-Forwarded-For entry — the address
    # Railway's single appending edge itself observed — so rotating
    # client-supplied prefix entries cannot mint a fresh bucket, while
    # a different last hop keys a different bucket.
    from werkzeug.middleware.proxy_fix import ProxyFix

    original_wsgi = mvp_app_obj.wsgi_app
    if not isinstance(original_wsgi, ProxyFix):
        mvp_app_obj.wsgi_app = ProxyFix(
            original_wsgi, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    try:
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
        # The bucket keys on the LAST hop: a request whose last hop
        # differs is a different client and is unaffected. (On an app
        # WITHOUT ProxyFix this request would key on the constant peer
        # instead and 429 — that is what makes this test non-vacuous.)
        res = _checkout(
            client, ip=peer, wallet=_wallet(30_001),
            headers={"X-Forwarded-For": "10.9.9.9, 203.0.113.100"})
        assert res.status_code == 402, res.get_json()
    finally:
        mvp_app_obj.wsgi_app = original_wsgi


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
# REPAIR: settle limiting is per (client, challenge) + fulfilled acks get
# a bounded, generous exemption (600/hour). The challenge TTL is 900s
# but the limiter window is 1h, so a per-address settle bucket shared
# across challenges lets 60 junk settles strand a paid buyer whose first
# settle then 429s past expiry. Round 2: the limiter's challenge_id
# extraction mirrors the view's (force-parsed JSON, no form fallback) —
# a mimetype-gated parse lets a Content-Type switch dilute the cap 2x —
# and the fulfilled exemption is finite, not a nominal 1000000/hour.
# ---------------------------------------------------------------------------

def _settle(client, body, *, ip=None):
    kwargs = {}
    if ip:
        kwargs["environ_base"] = {"REMOTE_ADDR": ip}
    return client.post("/rail/settle", json=body, **kwargs)


def _checkout_and_bind(client, *, ip=None):
    """Mint + ownership-bind a pack challenge for PAYER; return its id."""
    res = _checkout(client, ip=ip, wallet=PAYER)
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
    return challenge_id


def test_junk_settles_on_other_challenges_do_not_strand_buyer(client,
                                                               chain):
    ip = "203.0.113.30"
    challenge_id = _checkout_and_bind(client, ip=ip)
    # 60 junk settles from the SAME client address, all against OTHER
    # challenge ids. Under the old per-address bucket these exhaust the
    # buyer's settle budget entirely.
    for i in range(60):
        res = _settle(client, {
            "challenge_id": f"x402-junk-{i}", "tx_hash": TX_HASH,
            "payer_wallet": PAYER}, ip=ip)
        assert res.status_code != 429, (i, res.status_code)
    # The buyer's FIRST settle on their own challenge still succeeds.
    res = _settle(client, {
        "challenge_id": challenge_id, "tx_hash": TX_HASH,
        "payer_wallet": PAYER, "buyer_email": "buyer@example.com"}, ip=ip)
    assert res.status_code == 200, res.get_json()
    assert res.get_json().get("ok") is True, res.get_json()


def test_fulfilled_challenge_duplicate_acks_not_throttled_at_buyer_scale(
        client, chain):
    ip = "203.0.113.31"
    challenge_id = _checkout_and_bind(client, ip=ip)
    settle_body = {
        "challenge_id": challenge_id, "tx_hash": TX_HASH,
        "payer_wallet": PAYER, "buyer_email": "buyer@example.com",
    }
    first = _settle(client, settle_body, ip=ip)
    assert first.status_code == 200, first.get_json()
    # 60 further settles against the now-FULFILLED challenge: every one
    # is the proven-duplicate ack path and must answer 200 — the ack is
    # the paid buyer's only delivery-recovery path and must not be
    # throttled into the 900s-TTL stranding window. The exemption is
    # bounded (600/hour per (client, challenge), pinned directly in
    # test_settle_limit_callable_bounds_fulfilled_exemption below) but
    # sits far above any real buyer retry pattern.
    for i in range(60):
        res = _settle(client, settle_body, ip=ip)
        assert res.status_code == 200, (i, res.status_code, res.get_json())


def test_first_settles_on_one_unfulfilled_challenge_still_capped(client,
                                                                   chain):
    ip = "203.0.113.32"
    challenge_id = _checkout_and_bind(client, ip=ip)
    # 60 first-settle attempts against this one unfulfilled challenge
    # (no tx_hash → refused 400 by the handler, challenge stays
    # unfulfilled, but the limiter counts pre-dispatch).
    for i in range(60):
        res = _settle(client, {"challenge_id": challenge_id}, ip=ip)
        assert res.status_code == 400, (i, res.status_code)
    # The 61st is the rate limiter speaking: the per-(client, challenge)
    # limit still exists where it should.
    res = _settle(client, {"challenge_id": challenge_id}, ip=ip)
    assert res.status_code == 429, res.status_code
    assert res.get_json()["error"] == "rate_limited"


def test_settle_text_plain_resend_does_not_dilute_challenge_cap(client,
                                                                 chain):
    # B delta re-check F2, exact PoC: the limiter must extract
    # challenge_id exactly as the view does (force-parsed JSON). With a
    # mimetype-gated parse, re-sending the exhausted body as text/plain
    # misses the per-challenge bucket, lands in the plain-address
    # bucket, and reaches the view another 60 times (cap diluted 2x).
    ip = "203.0.113.34"
    challenge_id = _checkout_and_bind(client, ip=ip)
    body = {"challenge_id": challenge_id}
    for i in range(60):
        res = _settle(client, body, ip=ip)
        assert res.status_code == 400, (i, res.status_code)
    res = _settle(client, body, ip=ip)
    assert res.status_code == 429, res.status_code
    # Identical JSON body, Content-Type: text/plain. The SAME
    # per-challenge bucket must answer — every resend is a 429, never a
    # view-reaching 400.
    for i in range(10):
        res = client.post(
            "/rail/settle", data=json.dumps(body),
            content_type="text/plain",
            environ_base={"REMOTE_ADDR": ip})
        assert res.status_code == 429, (i, res.status_code)
        assert res.get_json()["error"] == "rate_limited"


def test_settle_form_body_keys_plain_address_bucket_not_challenge(client,
                                                                    chain):
    # The view never reads form data, so the limiter must not either:
    # a form-encoded challenge_id keys the PLAIN address bucket. If the
    # limiter read the form (the old fallback), the 60 posts below
    # would burn the per-challenge bucket and the final JSON settle on
    # that challenge would 429 instead of reaching the view.
    ip = "203.0.113.35"
    challenge_id = _checkout_and_bind(client, ip=ip)
    for i in range(60):
        res = client.post(
            "/rail/settle", data={"challenge_id": challenge_id},
            environ_base={"REMOTE_ADDR": ip})
        assert res.status_code == 400, (i, res.status_code)
    res = client.post(
        "/rail/settle", data={"challenge_id": challenge_id},
        environ_base={"REMOTE_ADDR": ip})
    assert res.status_code == 429, res.status_code
    assert res.get_json()["error"] == "rate_limited"
    # The per-challenge bucket is untouched: a JSON settle on the same
    # challenge still reaches the view (400 here — no tx_hash).
    res = _settle(client, {"challenge_id": challenge_id}, ip=ip)
    assert res.status_code == 400, res.status_code


def test_settle_limit_callable_bounds_fulfilled_exemption(client, chain,
                                                          mvp_app_obj,
                                                          monkeypatch):
    # Direct pin on the dynamic-limit callable (B delta re-check F1):
    # fulfilled challenges get a BOUNDED generous ceiling — 600/hour,
    # not the nominal 1000000/hour — so the exemption cannot be
    # harvested at full rate on trees where the ack path is not yet
    # proof-gated. Everything else, including lookup failures, applies
    # the normal limit and never crashes the callable.
    from sincor2.mvp_app import _settle_limit

    def _limit_for(body):
        with mvp_app_obj.test_request_context(
                "/rail/settle", method="POST", json=body):
            return _settle_limit()

    # Unfulfilled (minted + bound, never settled): normal limit.
    unfulfilled_id = _checkout_and_bind(client)
    assert _limit_for({"challenge_id": unfulfilled_id}) == "60 per hour"
    # Unknown id, missing id, non-string id: normal limit, no crash.
    assert _limit_for({"challenge_id": "x402-no-such"}) == "60 per hour"
    assert _limit_for({}) == "60 per hour"
    assert _limit_for({"challenge_id": 12345}) == "60 per hour"

    # Fulfilled: bounded generous ceiling.
    fulfilled_id = _checkout_and_bind(client)
    settle_body = {
        "challenge_id": fulfilled_id, "tx_hash": TX_HASH,
        "payer_wallet": PAYER, "buyer_email": "buyer@example.com",
    }
    first = _settle(client, settle_body)
    assert first.status_code == 200, first.get_json()
    assert _limit_for({"challenge_id": fulfilled_id}) == "600 per hour"

    # Lookup failure fails toward the normal limit, never toward a
    # crash or an exemption.
    def _boom(_cid):
        raise RuntimeError("status store unavailable")

    monkeypatch.setattr(x402, "challenge_status", _boom)
    assert _limit_for({"challenge_id": fulfilled_id}) == "60 per hour"


def test_settle_without_challenge_id_falls_back_to_address_bucket(client):
    ip = "203.0.113.33"
    # No challenge_id in the body → the plain per-address bucket applies
    # (a body-less settle cannot ride a per-challenge allowance).
    for i in range(60):
        res = _settle(client, {}, ip=ip)
        assert res.status_code == 400, (i, res.status_code)
    res = _settle(client, {}, ip=ip)
    assert res.status_code == 429, res.status_code
    assert res.get_json()["error"] == "rate_limited"


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
