"""Wren PR 2 — buyer delivery recovery via EIP-191 replay signature.

Scenario: the buyer's post-payment fulfillment session drops (tab
crash, closed page, lost response) after the onchain USDC payment has
settled and the pack was fulfilled — the buyer holds no download /
intake link, and the challenge is already fulfilled, so a naive
re-settle is exactly the W-115 entitlement-oracle shape.

Recovery contract pinned here (production gates unchanged — W-103
replay message + W-115 settle payer proof):

  * GET /rail/replay-message?challenge_id=.. hands the client the
    canonical replay message and the server-stamped ts_ms to sign
    (the format is public; the helper reads/writes no state and is
    not an existence oracle).
  * An UNSIGNED re-settle of the fulfilled challenge returns the
    W-115 status-only ack: status + identifiers, NEVER a URL, and no
    email is sent.
  * A re-settle signed by the recorded payer wallet (personal_sign of
    build_replay_message(challenge_id, ts_ms), inside the freshness
    window) returns the full existing result — the ORIGINAL
    token-gated intake_url — without duplicating the payment, the
    intake, or the delivery email. This test is the full loop:
    checkout -> bind -> pay/settle -> fulfill -> recover.
  * A signature from any other wallet is refused the same way as no
    signature at all (status-only ack, no URL).
  * templates/rail_pay.html carries the "Recover my delivery" action:
    it pulls challenge_id / tx_hash from sessionStorage (stashed by
    the pay flow) or the URL query, fetches the replay message,
    personal_signs it, and re-posts to /rail/settle.

Chain is a canned stub and keys are Hardhat dev accounts #0/#1 —
test-only, never funded, zero network.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from flask import Flask

import sincor2.platform_payments as pp
import sincor2.rail_routes as rr
import sincor2.x402_payments as x402
from sincor2.governance.money_gate import reset_money_boundary

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RAIL_PAY_HTML = REPO_ROOT / "templates" / "rail_pay.html"

TX_HASH = "0x" + "ab" * 32

# Hardhat dev accounts #0 (payer) and #1 (attacker) — test-only.
PAYER_ACCT = Account.from_key(
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80")
ATTACKER_ACCT = Account.from_key(
    "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d")
PAYER = PAYER_ACCT.address
ATTACKER = ATTACKER_ACCT.address

SKU = "credentialing_launch"
BUYER_EMAIL = "buyer@example.com"


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
    # No Celery in the test env: fulfillment delivers through the
    # in-process task_queue fallback, like the pack-queue suite.
    monkeypatch.setattr(rr, "_celery_app", None)
    # Hermetic sanctions screen (the W-115 suite's shape): the OFAC
    # gate itself is pinned elsewhere; here it must not depend on the
    # vendored snapshot's age.
    monkeypatch.setattr(pp, "screen_payer_sanctions",
                        lambda w: {"verdict": "pass", "detail": "clean"})
    pp._ofac_oracle = None

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
    state = {"sent": [], "attempts": 0}

    def fake_send_email(self, to_email, to_name="", subject="",
                        html_content="", text_content=None, metadata=None):
        state["attempts"] += 1
        state["sent"].append({"to": to_email, "subject": subject})
        return {"status": "sent", "message_id": "msg-wren-pr2-1"}

    monkeypatch.setattr(
        "sincor2.email_sender.EmailSender.send_email", fake_send_email)
    return state


@pytest.fixture()
def rail_app():
    from sincor2.blueprints.rail_pay import rail_pay_bp
    from sincor2.rail_delivery import init_rail_delivery_routes

    app = Flask(__name__, template_folder=str(REPO_ROOT / "templates"))
    app.config["TESTING"] = True
    rr.init_rail_routes(app)
    init_rail_delivery_routes(app)
    app.register_blueprint(rail_pay_bp)
    return app


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _bind_challenge(challenge_id, resource_id, acct=PAYER_ACCT):
    sig = acct.sign_message(encode_defunct(
        text=x402.challenge_bind_message(
            challenge_id, resource_id, acct.address))).signature.hex()
    bound = x402.bind_challenge_payer(challenge_id, acct.address, sig)
    assert bound.get("ownership_proven") is True, bound
    return bound


def _checkout(client):
    res = client.post("/rail/checkout", json={
        "pack_sku": SKU, "payer_wallet": PAYER,
        "buyer_email": BUYER_EMAIL, "buyer_name": "Test Buyer"})
    assert res.status_code == 402, res.get_json()
    body = res.get_json()
    _bind_challenge(body["challenge_id"], body["resource_id"])
    return body["challenge_id"]


def _settle(client, cid, **extra):
    body = {"challenge_id": cid, "tx_hash": TX_HASH,
            "payer_wallet": PAYER, "buyer_email": BUYER_EMAIL,
            "buyer_name": "Test Buyer"}
    body.update(extra)
    return client.post("/rail/settle", json=body)


def _buy_delivered(client):
    """Full purchase loop: checkout -> bind -> settle -> delivered."""
    cid = _checkout(client)
    res = _settle(client, cid)
    body = res.get_json()
    assert res.status_code == 200, body
    assert body["ok"] is True, body
    fulfillment = body.get("fulfillment") or {}
    assert fulfillment.get("status") == "delivered", body
    intake_url = fulfillment.get("intake_url") or ""
    assert "?token=" in intake_url, body
    return cid, body


def _replay_proof(acct, message, ts_ms):
    """Sign exactly the message the recovery helper handed out."""
    sig = "0x" + acct.sign_message(
        encode_defunct(text=message)).signature.hex()
    return {"replay_signature": sig, "replay_ts_ms": ts_ms}


def _fetch_replay_hint(client, cid):
    res = client.get("/rail/replay-message",
                     query_string={"challenge_id": cid})
    assert res.status_code == 200, res.get_json()
    return res.get_json()


def _db_counts(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return (conn.execute("SELECT COUNT(*) FROM rail_events").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM pack_intakes").fetchone()[0])
    finally:
        conn.close()


def _assert_no_entitlement(body):
    fulfillment = body.get("fulfillment") or {}
    assert not fulfillment.get("download_url"), body
    assert not fulfillment.get("intake_url"), body
    assert "token=" not in json.dumps(body), body


# ---------------------------------------------------------------------------
# the replay-message helper
# ---------------------------------------------------------------------------

def test_replay_message_helper_requires_challenge_id(rail_app, rail_env):
    client = rail_app.test_client()
    res = client.get("/rail/replay-message")
    assert res.status_code == 400
    assert res.get_json()["error"] == "challenge_id_required"


def test_replay_message_helper_returns_canonical_message(
        rail_app, rail_env):
    client = rail_app.test_client()
    hint = _fetch_replay_hint(client, "x402-deadbeef")
    assert hint["ok"] is True
    assert hint["challenge_id"] == "x402-deadbeef"
    # The handed-out message is byte-identical to the canonical W-103
    # message for the handed-out timestamp — the client signs exactly
    # what the settle will verify.
    assert hint["replay_message"] == x402.build_replay_message(
        "x402-deadbeef", hint["replay_ts_ms"])
    assert hint["replay_message"].startswith("SINCOR-X402-REPLAY\n")
    now_ms = int(time.time() * 1000)
    assert abs(now_ms - hint["replay_ts_ms"]) < 10_000
    assert hint.get("replay_hint")


# ---------------------------------------------------------------------------
# unsigned re-settle: status-only ack, never a URL
# ---------------------------------------------------------------------------

def test_unsigned_resettle_status_only_ack_never_a_url(
        rail_app, rail_env, email_ok):
    client = rail_app.test_client()
    cid, _first = _buy_delivered(client)
    assert len(email_ok["sent"]) == 1

    dup = _settle(client, cid)
    body = dup.get_json()
    assert dup.status_code == 200, body
    assert body["status"] == "already_fulfilled", body
    assert body["duplicate"] is True, body
    assert body["payer_proof_required"] is True, body
    _assert_no_entitlement(body)
    assert len(email_ok["sent"]) == 1, "ack path must never send email"


# ---------------------------------------------------------------------------
# signed re-settle: the full recovery loop
# ---------------------------------------------------------------------------

def test_signed_resettle_returns_intake_url_with_token(
        rail_app, rail_env, email_ok):
    client = rail_app.test_client()
    cid, first = _buy_delivered(client)
    original_url = first["fulfillment"]["intake_url"]

    # Exactly the browser recovery flow: fetch the canonical message,
    # personal_sign it with the payer key, re-post to /rail/settle.
    hint = _fetch_replay_hint(client, cid)
    proof = _replay_proof(PAYER_ACCT, hint["replay_message"],
                          hint["replay_ts_ms"])
    res = _settle(client, cid, **proof)
    body = res.get_json()
    assert res.status_code == 200, body
    assert body["ok"] is True, body
    assert body["duplicate"] is True, body
    recovered_url = (body.get("fulfillment") or {}).get("intake_url") or ""
    assert "?token=" in recovered_url, body
    assert recovered_url == original_url, body

    # Recovery re-issues the existing entitlement — it never
    # duplicates the payment, the intake, or the delivery email.
    db_path = rail_env["tmp"] / "revenue.db"
    assert _db_counts(db_path) == (1, 1)
    log_lines = (rail_env["tmp"] / "platform_payments.jsonl"
                 ).read_text().splitlines()
    assert len(log_lines) == 1, "payment still recorded exactly once"
    assert len(email_ok["sent"]) == 1, "no second delivery email"


# ---------------------------------------------------------------------------
# incorrect signers are refused
# ---------------------------------------------------------------------------

def test_attacker_signed_resettle_refused(rail_app, rail_env, email_ok):
    client = rail_app.test_client()
    cid, _first = _buy_delivered(client)

    hint = _fetch_replay_hint(client, cid)
    proof = _replay_proof(ATTACKER_ACCT, hint["replay_message"],
                          hint["replay_ts_ms"])

    # Attacker signs AND names their own wallet.
    res = _settle(client, cid, payer_wallet=ATTACKER, **proof)
    body = res.get_json()
    assert res.status_code == 200, body
    assert body["status"] == "already_fulfilled", body
    _assert_no_entitlement(body)

    # Attacker signs but claims the payer's wallet string — a bare
    # address is public data, not proof of control.
    res = _settle(client, cid, payer_wallet=PAYER, **proof)
    body = res.get_json()
    assert res.status_code == 200, body
    _assert_no_entitlement(body)
    assert len(email_ok["sent"]) == 1


def test_stale_replay_proof_refused(rail_app, rail_env, email_ok):
    client = rail_app.test_client()
    cid, _first = _buy_delivered(client)

    stale_ts = int(time.time() * 1000) - 660_000  # 11 min; window is 10
    message = x402.build_replay_message(cid, stale_ts)
    proof = _replay_proof(PAYER_ACCT, message, stale_ts)
    res = _settle(client, cid, **proof)
    body = res.get_json()
    assert res.status_code == 200, body
    _assert_no_entitlement(body)


# ---------------------------------------------------------------------------
# the pay page carries the recovery action
# ---------------------------------------------------------------------------

def test_recovery_handler_present_in_pay_page_html():
    html = RAIL_PAY_HTML.read_text()
    assert 'id="btnRecover"' in html
    assert "Recover my delivery" in html
    # Pulls the challenge from sessionStorage or URL parameters.
    assert "sessionStorage" in html
    assert "URLSearchParams" in html
    assert "sincor_rail_challenge_id" in html
    assert "sincor_rail_tx_hash" in html
    # Fetches the canonical replay message, personal_signs it, and
    # re-posts the proof to /rail/settle.
    assert "/rail/replay-message" in html
    assert "personal_sign" in html
    assert "replay_signature" in html
    assert "replay_ts_ms" in html
    assert 'postJSON("/rail/settle"' in html
    # The pay flow stashes the identifiers the recovery reads back.
    assert "stashRecovery" in html


def test_recovery_section_renders_on_the_pay_page(rail_app, rail_env):
    client = rail_app.test_client()
    res = client.get("/rail/pay/rail-pack-credentialing_launch")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert 'id="btnRecover"' in html
    assert "Recover my delivery" in html
