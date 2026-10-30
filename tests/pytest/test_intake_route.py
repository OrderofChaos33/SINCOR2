"""Wren PR 1: token-gated pack intake route (/intake/<intake_id>).

Before this change every paid B2B pack ($297/$997/$2,997) and WebBuilder
($497) buyer was emailed a link to /intake/<intake_id> — a route that did
not exist (404 for every buyer), whose id was guessable (derived from the
challenge id), and whose backing record could be wiped by the
fulfillment job because payment_ledger.update_pack_intake_status
REPLACED the whole details JSON on every write.

Covered here:
  * GET /intake/<id> fails closed exactly like /download: unknown id,
    missing token, wrong token -> 404; a digital pack's intake id -> 404.
  * GET with the right token renders the pack name, the status, and the
    per-SKU intake form.
  * POST validates per-SKU required fields server-side (allowlist, HTML
    stripped, enum/length enforced, unknown keys rejected with 400
    naming the field), stores answers in details["intake_answers"],
    moves status to intake_complete, notifies the owner exactly once,
    and never touches the recorded customer_email or payer_wallet.
  * POST is idempotent while status < in_progress (updates answers, no
    second owner notification) and refuses once work is in progress.
  * payment_ledger.update_pack_intake_status MERGES details
    (read-merge-write) and never lets status regress along
    created -> agent_kickoff -> intake_complete -> in_progress ->
    delivered.
  * fulfill_pack_agent running AFTER intake completion preserves
    intake_token, payer_wallet and intake_answers (merge regression).
  * A full rail settle for credentialing_launch returns an intake_url
    carrying ?token=..., the emailed link carries the same token, and a
    duplicate settle ack returns the identical URL (no token rotation).

The "chain" is the same canned _rpc_call stub pattern as
test_rail_routes.py: zero network, zero keys, nothing broadcast.
"""
from __future__ import annotations

import re
import sqlite3
import threading
import time
from pathlib import Path

import pytest

import sincor2.rail_routes as rr
import sincor2.platform_payments as pp
import sincor2.x402_payments as x402
from sincor2 import payment_ledger as pl
from sincor2.governance.money_gate import reset_money_boundary

from eth_account import Account as _Account
from eth_account.messages import encode_defunct as _encode_defunct

_PAYER_ACCT = _Account.from_key(
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80")
PAYER = _PAYER_ACCT.address
TX_HASH = "0x" + "ab" * 32

OWNER_EMAIL = "owner@example.com"
BUYER_EMAIL = "buyer@example.com"
INTAKE_TOKEN = "tok-test-unguessable-123"

CRED_ANSWERS = {
    "business_name": "Acme Dental LLC",
    "contact_name": "Amy Acme",
    "contact_email": "amy@acmedental.example",
    "website_or_none": "https://acmedental.example",
    "practice_type": "dental practice",
    "state": "IA",
    "specialties": "general dentistry, implants",
    "current_payer_list": "Delta Dental, Cigna",
}

WEBBUILDER_ANSWERS = {
    "business_name": "Acme Dental LLC",
    "contact_name": "Amy Acme",
    "contact_email": "amy@acmedental.example",
    "website_or_none": "none",
    "phone": "+1 555 123 4567",
    "niche": "dental",
    "territory": "Clinton, IA",
    "prompt": "Clean modern site with online booking",
}

CHROMA_ANSWERS = {
    "business_name": "Acme Software Inc",
    "contact_name": "Chris Acme",
    "contact_email": "chris@acmesoftware.example",
    "website_or_none": "https://acmesoftware.example",
    "repo_or_product_url": "https://github.com/acme/widget",
    "sbom_format_needed": "CycloneDX",
    "compliance_target": "SOC 2",
}


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
def intake_env(tmp_path, monkeypatch):
    """Hermetic stores + stubbed RPC + owner/support alert addresses."""
    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    monkeypatch.setenv("SINCOR_REVENUE_DB", str(tmp_path / "revenue.db"))
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LAUNCH_REVIEW_ALERT_EMAIL", OWNER_EMAIL)
    monkeypatch.setenv("SUPPORT_EMAIL", OWNER_EMAIL)
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
    return {"db": str(tmp_path / "revenue.db"), "tx_hash": TX_HASH,
            "payer": PAYER, "tmp": tmp_path}


@pytest.fixture()
def mock_comms(monkeypatch):
    """Capture outbound email + Celery queueing; nothing leaves."""
    sent = []
    queued = []

    def fake_send_email(self, to_email, to_name="", subject="",
                        html_content="", text_content=None, metadata=None):
        sent.append({
            "to": to_email, "to_name": to_name, "subject": subject,
            "html": html_content, "text": text_content,
            "metadata": metadata or {},
        })
        return {"status": "sent", "message_id": "msg-test-1"}

    class _FakeResult:
        id = "celery-job-test-1"

    def fake_apply_async(args=None, kwargs=None, queue=None, **kw):
        queued.append({"args": args, "kwargs": kwargs, "queue": queue})
        return _FakeResult()

    monkeypatch.setattr(
        "sincor2.email_sender.EmailSender.send_email", fake_send_email)
    monkeypatch.setattr(
        rr.fulfill_pack_agent, "apply_async", fake_apply_async)
    return {"sent": sent, "queued": queued}


@pytest.fixture()
def intake_app():
    """Minimal Flask app: real rail blueprint + the intake blueprint,
    with the repo template folder (mirrors the mvp_app mount)."""
    from flask import Flask

    from sincor2.blueprints.intake import init_intake_routes

    template_dir = Path(__file__).resolve().parents[2] / "templates"
    app = Flask(__name__, template_folder=str(template_dir))
    app.config["TESTING"] = True
    rr.init_rail_routes(app)
    init_intake_routes(app)
    return app


def _seed_intake(db, *, intake_id="intake-rail-x402-test0001",
                 pack_sku="credentialing_launch", status="created",
                 details=None, event_id="rail-x402-test0001"):
    """Persist a post-fulfillment intake row the way _fulfill_rail_pack
    leaves it: pack details + payer wallet + the buyer's intake token."""
    base_details = {
        "pack_name": rr.PACK_SKUS[pack_sku]["name"],
        "module": rr.PACK_SKUS[pack_sku].get("module", ""),
        "payer_wallet": PAYER.lower(),
        "rail": True,
        "intake_token": INTAKE_TOKEN,
    }
    if details:
        base_details.update(details)
    intake, _is_new = pl.record_pack_intake(
        db,
        intake_id=intake_id,
        customer_email=BUYER_EMAIL,
        pack_sku=pack_sku,
        event_id=event_id,
        intake_url=f"https://getsincor.com/intake/{intake_id}"
                   f"?token={INTAKE_TOKEN}",
        details=base_details,
    )
    if status != "created":
        pl.update_pack_intake_status(db, intake_id, status)
    return intake


def _owner_sends(mock_comms):
    return [s for s in mock_comms["sent"] if s["to"] == OWNER_EMAIL]


# ---------------------------------------------------------------------------
# GET /intake/<id>: fail closed, exactly like /download
# ---------------------------------------------------------------------------

def test_get_unknown_intake_404(intake_app, intake_env):
    client = intake_app.test_client()
    res = client.get("/intake/intake-rail-nope?token=whatever")
    assert res.status_code == 404


def test_get_known_intake_without_token_404(intake_app, intake_env):
    _seed_intake(intake_env["db"])
    client = intake_app.test_client()
    res = client.get("/intake/intake-rail-x402-test0001")
    assert res.status_code == 404


def test_get_known_intake_wrong_token_404(intake_app, intake_env):
    _seed_intake(intake_env["db"])
    client = intake_app.test_client()
    res = client.get("/intake/intake-rail-x402-test0001?token=wrong-token")
    assert res.status_code == 404
    # Fail closed means no info leak either: the pack name must not
    # appear in the rejection body.
    assert b"Credentialing Launch" not in res.data


def test_get_known_intake_right_token_200(intake_app, intake_env):
    _seed_intake(intake_env["db"])
    client = intake_app.test_client()
    res = client.get(
        f"/intake/intake-rail-x402-test0001?token={INTAKE_TOKEN}")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "Credentialing Launch" in html
    assert "Received" in html  # status label for a fresh intake
    # The per-SKU form is rendered server-side.
    assert 'name="practice_type"' in html
    assert 'name="business_name"' in html
    assert 'name="current_payer_list"' in html


def test_get_intake_token_via_header_200(intake_app, intake_env):
    _seed_intake(intake_env["db"])
    client = intake_app.test_client()
    res = client.get("/intake/intake-rail-x402-test0001",
                     headers={"X-Intake-Token": INTAKE_TOKEN})
    assert res.status_code == 200


def test_get_webbuilder_intake_shows_its_fields(intake_app, intake_env):
    _seed_intake(intake_env["db"], intake_id="intake-rail-x402-wb01",
                 pack_sku="webbuilder_site", event_id="rail-x402-wb01")
    client = intake_app.test_client()
    res = client.get(f"/intake/intake-rail-x402-wb01?token={INTAKE_TOKEN}")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert 'name="territory"' in html
    assert 'name="prompt"' in html
    assert 'name="practice_type"' not in html


def test_get_digital_pack_intake_404(intake_app, intake_env):
    """A digital pack's intake id is a download credential, never an
    intake page — even presenting its delivery token must 404."""
    pl.record_pack_intake(
        intake_env["db"],
        intake_id="intake-rail-x402-dig01",
        customer_email=BUYER_EMAIL,
        pack_sku="digital_ai_agent_bundle",
        event_id="rail-x402-dig01",
        intake_url="https://getsincor.com/download/intake-rail-x402-dig01"
                   "?token=delivery-token-xyz",
        details={"pack_name": "AI Agent Bundle", "digital": True,
                 "delivery_token": "delivery-token-xyz",
                 "asset": "money_assets/ai_agent_bundle.md",
                 "payer_wallet": PAYER.lower(), "rail": True},
    )
    client = intake_app.test_client()
    res = client.get(
        "/intake/intake-rail-x402-dig01?token=delivery-token-xyz")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# POST /intake/<id>: validation, storage, idempotency, owner notify
# ---------------------------------------------------------------------------

def test_post_valid_intake_completes_and_notifies_once(
        intake_app, intake_env, mock_comms):
    _seed_intake(intake_env["db"])
    client = intake_app.test_client()
    res = client.post(
        f"/intake/intake-rail-x402-test0001?token={INTAKE_TOKEN}",
        json=CRED_ANSWERS)
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert body["status"] == "intake_complete"

    row = pl.get_pack_intake(intake_env["db"], "intake-rail-x402-test0001")
    assert row.status == "intake_complete"
    assert row.details["intake_answers"] == CRED_ANSWERS
    # The write merged — it did not wipe the fulfillment details.
    assert row.details["intake_token"] == INTAKE_TOKEN
    assert row.details["payer_wallet"] == PAYER.lower()
    assert row.details["pack_name"] == "Credentialing Launch"
    # The recorded buyer identity never moves.
    assert row.customer_email == BUYER_EMAIL

    owner = _owner_sends(mock_comms)
    assert len(owner) == 1
    assert "intake-rail-x402-test0001" in (
        owner[0]["subject"] + owner[0]["text"] + owner[0]["html"])


def test_post_missing_required_fields_400_names_them(
        intake_app, intake_env, mock_comms):
    _seed_intake(intake_env["db"])
    client = intake_app.test_client()
    res = client.post(
        f"/intake/intake-rail-x402-test0001?token={INTAKE_TOKEN}",
        json={"business_name": "Acme Dental LLC"})
    assert res.status_code == 400
    body = res.get_json()
    assert body["ok"] is False
    named = set(body.get("fields") or [])
    assert {"contact_name", "contact_email", "practice_type",
            "state", "specialties", "current_payer_list"} <= named
    # Nothing was stored and the owner was not notified.
    row = pl.get_pack_intake(intake_env["db"], "intake-rail-x402-test0001")
    assert row.status == "created"
    assert "intake_answers" not in (row.details or {})
    assert _owner_sends(mock_comms) == []


def test_post_unknown_key_400(intake_app, intake_env, mock_comms):
    _seed_intake(intake_env["db"])
    client = intake_app.test_client()
    payload = dict(CRED_ANSWERS)
    payload["payer_wallet"] = "0x" + "11" * 20  # not an intake field
    res = client.post(
        f"/intake/intake-rail-x402-test0001?token={INTAKE_TOKEN}",
        json=payload)
    assert res.status_code == 400
    assert "payer_wallet" in (res.get_json().get("fields") or [])
    row = pl.get_pack_intake(intake_env["db"], "intake-rail-x402-test0001")
    assert row.details["payer_wallet"] == PAYER.lower()
    assert row.status == "created"


def test_post_second_submission_updates_without_renotify(
        intake_app, intake_env, mock_comms):
    _seed_intake(intake_env["db"])
    client = intake_app.test_client()
    url = f"/intake/intake-rail-x402-test0001?token={INTAKE_TOKEN}"
    res1 = client.post(url, json=CRED_ANSWERS)
    assert res1.status_code == 200

    updated = dict(CRED_ANSWERS, business_name="Acme Dental West",
                   specialties="orthodontics")
    res2 = client.post(url, json=updated)
    assert res2.status_code == 200
    assert res2.get_json()["status"] == "intake_complete"

    row = pl.get_pack_intake(intake_env["db"], "intake-rail-x402-test0001")
    assert row.details["intake_answers"]["business_name"] == \
        "Acme Dental West"
    assert row.details["intake_answers"]["specialties"] == "orthodontics"
    assert len(_owner_sends(mock_comms)) == 1  # still exactly one


def test_post_after_in_progress_does_not_update(
        intake_app, intake_env, mock_comms):
    _seed_intake(intake_env["db"])
    client = intake_app.test_client()
    url = f"/intake/intake-rail-x402-test0001?token={INTAKE_TOKEN}"
    assert client.post(url, json=CRED_ANSWERS).status_code == 200
    # Operator starts work: the intake locks.
    pl.update_pack_intake_status(
        intake_env["db"], "intake-rail-x402-test0001", "in_progress")
    changed = dict(CRED_ANSWERS, business_name="Changed Name LLC")
    res = client.post(url, json=changed)
    assert res.status_code == 409
    row = pl.get_pack_intake(intake_env["db"], "intake-rail-x402-test0001")
    assert row.status == "in_progress"
    assert row.details["intake_answers"]["business_name"] == \
        "Acme Dental LLC"


def test_post_form_encoded_webbuilder(intake_app, intake_env, mock_comms):
    _seed_intake(intake_env["db"], intake_id="intake-rail-x402-wb02",
                 pack_sku="webbuilder_site", event_id="rail-x402-wb02")
    client = intake_app.test_client()
    res = client.post(
        f"/intake/intake-rail-x402-wb02?token={INTAKE_TOKEN}",
        data=WEBBUILDER_ANSWERS)
    assert res.status_code == 200
    row = pl.get_pack_intake(intake_env["db"], "intake-rail-x402-wb02")
    assert row.status == "intake_complete"
    assert row.details["intake_answers"]["territory"] == "Clinton, IA"


def test_post_validation_enum_html_and_length(
        intake_app, intake_env, mock_comms):
    _seed_intake(intake_env["db"], intake_id="intake-rail-x402-ch01",
                 pack_sku="chroma_compliance", event_id="rail-x402-ch01")
    client = intake_app.test_client()
    url = f"/intake/intake-rail-x402-ch01?token={INTAKE_TOKEN}"

    bad_enum = dict(CHROMA_ANSWERS, sbom_format_needed="YAML")
    res = client.post(url, json=bad_enum)
    assert res.status_code == 400
    assert "sbom_format_needed" in (res.get_json().get("fields") or [])

    too_long = dict(CHROMA_ANSWERS, compliance_target="x" * 501)
    res = client.post(url, json=too_long)
    assert res.status_code == 400
    assert "compliance_target" in (res.get_json().get("fields") or [])

    non_string = dict(CHROMA_ANSWERS, business_name=42)
    res = client.post(url, json=non_string)
    assert res.status_code == 400
    assert "business_name" in (res.get_json().get("fields") or [])

    html_payload = dict(
        CHROMA_ANSWERS,
        repo_or_product_url="<script>alert(1)</script>https://example.com/r")
    res = client.post(url, json=html_payload)
    assert res.status_code == 200
    row = pl.get_pack_intake(intake_env["db"], "intake-rail-x402-ch01")
    stored = row.details["intake_answers"]["repo_or_product_url"]
    assert "<script>" not in stored and "alert(1)" not in stored


def test_post_wrong_token_404_and_no_write(intake_app, intake_env,
                                          mock_comms):
    _seed_intake(intake_env["db"])
    client = intake_app.test_client()
    res = client.post("/intake/intake-rail-x402-test0001?token=nope",
                      json=CRED_ANSWERS)
    assert res.status_code == 404
    row = pl.get_pack_intake(intake_env["db"], "intake-rail-x402-test0001")
    assert row.status == "created"
    assert "intake_answers" not in (row.details or {})


def test_post_owner_notify_failure_does_not_5xx(
        intake_app, intake_env, monkeypatch):
    _seed_intake(intake_env["db"])

    def boom(self, *a, **kw):
        raise RuntimeError("provider down")

    monkeypatch.setattr(
        "sincor2.email_sender.EmailSender.send_email", boom)
    client = intake_app.test_client()
    res = client.post(
        f"/intake/intake-rail-x402-test0001?token={INTAKE_TOKEN}",
        json=CRED_ANSWERS)
    assert res.status_code == 200
    row = pl.get_pack_intake(intake_env["db"], "intake-rail-x402-test0001")
    assert row.status == "intake_complete"


# ---------------------------------------------------------------------------
# payment_ledger: details merge + status transition guard
# ---------------------------------------------------------------------------

def test_update_status_merges_details(intake_env):
    db = intake_env["db"]
    _seed_intake(db)
    pl.update_pack_intake_status(
        db, "intake-rail-x402-test0001", "agent_kickoff",
        details={"agent": {"agent": "healthcare_rcm_agent"},
                 "customer_email": BUYER_EMAIL})
    row = pl.get_pack_intake(db, "intake-rail-x402-test0001")
    assert row.status == "agent_kickoff"
    assert row.details["agent"]["agent"] == "healthcare_rcm_agent"
    # Pre-existing keys survive the write.
    assert row.details["pack_name"] == "Credentialing Launch"
    assert row.details["module"] == "verticals/healthcare"
    assert row.details["payer_wallet"] == PAYER.lower()
    assert row.details["intake_token"] == INTAKE_TOKEN


def test_update_status_never_regresses(intake_env):
    db = intake_env["db"]
    _seed_intake(db)
    pl.update_pack_intake_status(db, "intake-rail-x402-test0001",
                                 "intake_complete")
    # A late agent-kickoff write must not drag the status backwards,
    # but its details still merge.
    pl.update_pack_intake_status(
        db, "intake-rail-x402-test0001", "agent_kickoff",
        details={"agent": {"agent": "healthcare_rcm_agent"}})
    row = pl.get_pack_intake(db, "intake-rail-x402-test0001")
    assert row.status == "intake_complete"
    assert row.details["agent"]["agent"] == "healthcare_rcm_agent"
    # Forward jumps remain legal.
    pl.update_pack_intake_status(db, "intake-rail-x402-test0001",
                                 "delivered")
    row = pl.get_pack_intake(db, "intake-rail-x402-test0001")
    assert row.status == "delivered"


def test_update_status_details_none_preserves_details(intake_env):
    db = intake_env["db"]
    _seed_intake(db)
    pl.update_pack_intake_status(db, "intake-rail-x402-test0001",
                                 "agent_kickoff")
    row = pl.get_pack_intake(db, "intake-rail-x402-test0001")
    assert row.details["intake_token"] == INTAKE_TOKEN
    assert row.details["payer_wallet"] == PAYER.lower()


# ---------------------------------------------------------------------------
# Merge regression: fulfill_pack_agent after intake completion
# ---------------------------------------------------------------------------

def test_fulfill_pack_agent_after_intake_keeps_token_and_wallet(
        intake_app, intake_env, mock_comms):
    _seed_intake(intake_env["db"])
    client = intake_app.test_client()
    res = client.post(
        f"/intake/intake-rail-x402-test0001?token={INTAKE_TOKEN}",
        json=CRED_ANSWERS)
    assert res.status_code == 200

    # The Celery worker entrypoint runs AFTER the buyer completed
    # intake (queue latency) — its kickoff write must not wipe the
    # token, the payer wallet, or the buyer's answers, and must not
    # regress the status back to agent_kickoff.
    receipt = rr.fulfill_pack_agent(
        intake_id="intake-rail-x402-test0001",
        pack_sku="credentialing_launch",
        customer_email=BUYER_EMAIL)
    assert receipt["status"] == "ok"

    row = pl.get_pack_intake(intake_env["db"], "intake-rail-x402-test0001")
    assert row.details["intake_token"] == INTAKE_TOKEN
    assert row.details["payer_wallet"] == PAYER.lower()
    assert row.details["pack_name"] == "Credentialing Launch"
    assert row.details["intake_answers"] == CRED_ANSWERS
    assert row.details["agent"]["agent"] == "healthcare_rcm_agent"
    assert row.status == "intake_complete"


# ---------------------------------------------------------------------------
# Full loop: settle mints the intake token and returns the tokenized URL
# ---------------------------------------------------------------------------

def _checkout_challenge(client, sku="credentialing_launch"):
    res = client.post("/rail/checkout", json={
        "pack_sku": sku, "payer_wallet": PAYER,
        "buyer_email": BUYER_EMAIL, "buyer_name": "Test Buyer"})
    assert res.status_code == 402
    body = res.get_json()
    _bind_challenge(body["challenge_id"], body["resource_id"])
    return body["challenge_id"]


def test_settle_returns_tokenized_intake_url(
        intake_app, intake_env, mock_comms):
    client = intake_app.test_client()
    cid = _checkout_challenge(client)
    res = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": TX_HASH,
        "payer_wallet": PAYER, "buyer_email": BUYER_EMAIL})
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["ok"] is True
    fulfillment = body["fulfillment"]
    intake_url = fulfillment["intake_url"]
    assert f"/intake/intake-rail-{cid}" in intake_url
    assert "?token=" in intake_url

    # The emailed link carries the same token.
    buyer_mail = [s for s in mock_comms["sent"] if s["to"] == BUYER_EMAIL]
    assert buyer_mail, "buyer confirmation email was not sent"
    assert "?token=" in (buyer_mail[0]["text"] or "")

    # And the link actually opens the intake page.
    path = "/" + intake_url.split("getsincor.com/", 1)[1]
    page = client.get(path)
    assert page.status_code == 200
    assert "Credentialing Launch" in page.get_data(as_text=True)

    # A duplicate settle ack returns the identical URL — the token is
    # never rotated out from under the first email.
    res2 = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": TX_HASH,
        "payer_wallet": PAYER, "buyer_email": BUYER_EMAIL})
    assert res2.status_code == 200
    assert res2.get_json()["fulfillment"]["intake_url"] == intake_url


def test_settle_prefills_intake_answers_from_pay_details(
        intake_app, intake_env, mock_comms):
    """Details collected on /rail/pay before paying ride the settle
    into the intake record as pre-filled answers."""
    client = intake_app.test_client()
    cid = _checkout_challenge(client)
    res = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": TX_HASH,
        "payer_wallet": PAYER, "buyer_email": BUYER_EMAIL,
        "business_name": "Acme Dental LLC",
        "contact_name": "Amy Acme",
        "website_or_none": "none",
        "practice_type": "dental practice",
        "state": "IA",
        "specialties": "general dentistry",
        "current_payer_list": "Delta Dental",
    })
    assert res.status_code == 200, res.get_json()
    row = pl.get_pack_intake(intake_env["db"], f"intake-rail-{cid}")
    answers = row.details["intake_answers"]
    assert answers["practice_type"] == "dental practice"
    assert answers["business_name"] == "Acme Dental LLC"
    assert answers["current_payer_list"] == "Delta Dental"
    # The buyer's settle email doubles as the intake contact email.
    assert answers["contact_email"] == BUYER_EMAIL


def test_pay_page_collects_required_details_before_paying():
    """The pay page renders the per-SKU required fields and gates the
    checkout/settle payloads on them (item 5 wiring pins)."""
    template = (Path(__file__).resolve().parents[2]
                / "templates" / "rail_pay.html").read_text()
    for group_id in ("sharedPackFields", "webbuilderFields",
                     "credentialingFields", "chromaFields",
                     "sadasFields"):
        assert f'id="{group_id}"' in template
    for element_id in ("contactName", "buyerWebsite", "buyerTerritory",
                       "buyerPrompt", "practiceType", "practiceState",
                       "specialties", "currentPayerList", "repoUrl",
                       "sbomFormat", "complianceTarget", "sadasGoal",
                       "systemsInScope", "successMetric", "sadasTimeline"):
        assert f'id="{element_id}"' in template
    # The checkout body and the Pay gate both consult the details.
    assert "detailValues()" in template
    assert "detailsComplete()" in template


def test_intake_route_mounted_on_mvp_app(intake_env):
    """The production app must actually serve /intake/<id> — the
    not-mounted failure class is what made this PR necessary."""
    import sincor2.mvp_app as mvp

    rules = {rule.rule for rule in mvp.app.url_map.iter_rules()}
    assert "/intake/<intake_id>" in rules
    assert mvp.RAIL_MOUNT_STATUS["rail_intake"] is True


# ---------------------------------------------------------------------------
# Repair round (Builder B NOT CONFIRMED): five defects
#   1. non-ASCII token 500'd on known ids (existence oracle)
#   2. non-atomic read-merge-write lost keys under concurrency;
#      corrupt details JSON raised out of the update
#   3. duplicate owner notifications under concurrent POSTs
#   4. tag-only values passed the required-field check (B1)
#   5. settle prefill bypassed sanitization (F1); entity-encoded markup
#      survived cleaning (B2)
# ---------------------------------------------------------------------------

def test_non_ascii_token_takes_identical_404_path(
        intake_app, intake_env):
    """hmac.compare_digest on str raises TypeError for non-ASCII input:
    a non-ASCII token on a KNOWN id 500'd while an unknown id cleanly
    404'd — an unauthenticated existence oracle. Every bad-token shape
    must take the identical 404 path, byte for byte, on GET and POST."""
    _seed_intake(intake_env["db"])
    client = intake_app.test_client()
    unknown = client.get("/intake/intake-rail-nope?token=whatever")
    assert unknown.status_code == 404

    weird = "tökén-üñïcode-🔑"
    known_get = client.get(
        "/intake/intake-rail-x402-test0001",
        query_string={"token": weird})
    assert known_get.status_code == 404
    assert known_get.data == unknown.data

    known_post = client.post(
        "/intake/intake-rail-x402-test0001",
        query_string={"token": weird}, json=CRED_ANSWERS)
    assert known_post.status_code == 404
    assert known_post.data == unknown.data

    # The header path takes the same comparison.
    known_hdr = client.get(
        "/intake/intake-rail-x402-test0001",
        headers={"X-Intake-Token": "tökén"})
    assert known_hdr.status_code == 404
    assert known_hdr.data == unknown.data

    # Nothing was written by any of the rejected requests.
    row = pl.get_pack_intake(intake_env["db"], "intake-rail-x402-test0001")
    assert row.status == "created"
    assert "intake_answers" not in (row.details or {})


def test_update_status_concurrent_merges_lose_no_keys(intake_env):
    """Six concurrent read-merge-writes with distinct keys must ALL
    survive: the merge is serialized (BEGIN IMMEDIATE), not a
    last-writer-wins snapshot race."""
    db = intake_env["db"]
    _seed_intake(db)
    barrier = threading.Barrier(6)
    errors = []

    def worker(i):
        try:
            barrier.wait(timeout=10)
            pl.update_pack_intake_status(
                db, "intake-rail-x402-test0001", "agent_kickoff",
                details={f"key_{i}": i})
        except Exception as exc:  # recorded, asserted below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,))
               for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors
    row = pl.get_pack_intake(db, "intake-rail-x402-test0001")
    for i in range(6):
        assert row.details[f"key_{i}"] == i
    # The fulfillment details rode through every merge.
    assert row.details["intake_token"] == INTAKE_TOKEN
    assert row.details["payer_wallet"] == PAYER.lower()


def test_update_status_heals_corrupt_details_json(intake_env):
    """A row whose stored details are invalid JSON must not raise out
    of the update — the old replace-write healed such rows by
    overwriting them; the merge treats unparseable details as {}."""
    db = intake_env["db"]
    _seed_intake(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE pack_intakes SET details=? WHERE intake_id=?",
        ("{not valid json", "intake-rail-x402-test0001"))
    conn.commit()
    conn.close()

    advanced = pl.update_pack_intake_status(
        db, "intake-rail-x402-test0001", "agent_kickoff",
        details={"fresh": "value"})
    assert advanced is True
    row = pl.get_pack_intake(db, "intake-rail-x402-test0001")
    assert row.status == "agent_kickoff"
    assert row.details == {"fresh": "value"}


def test_update_status_reports_whether_it_advanced(intake_env):
    """The return value is the CAS signal the intake route keys its
    exactly-once owner notification on: True only when THIS call moved
    the record onto the requested status."""
    db = intake_env["db"]
    _seed_intake(db)
    assert pl.update_pack_intake_status(
        db, "intake-rail-x402-test0001", "agent_kickoff") is True
    # Already there: no transition performed by this call.
    assert pl.update_pack_intake_status(
        db, "intake-rail-x402-test0001", "agent_kickoff") is False
    # Backwards request: details merge, status untouched, no advance.
    assert pl.update_pack_intake_status(
        db, "intake-rail-x402-test0001", "created") is False
    # Unknown row: nothing happened.
    assert pl.update_pack_intake_status(
        db, "intake-rail-nope", "agent_kickoff") is False
    # Forward jump still reports its advance.
    assert pl.update_pack_intake_status(
        db, "intake-rail-x402-test0001", "intake_complete") is True


def test_post_concurrent_burst_notifies_owner_exactly_once(
        intake_app, intake_env, mock_comms, monkeypatch):
    """Concurrent first POSTs (auth read artificially slowed, so every
    request observes the pre-completion record) must produce exactly
    ONE owner notification per recipient: the notify decision rides on
    the atomic transition signal from the ledger write, not on a
    status read taken before it."""
    monkeypatch.setenv("SUPPORT_EMAIL", "support@example.com")
    _seed_intake(intake_env["db"])

    real_get = pl.get_pack_intake

    def slow_get(store_or_db, intake_id):
        time.sleep(0.3)
        return real_get(store_or_db, intake_id)

    monkeypatch.setattr(pl, "get_pack_intake", slow_get)

    barrier = threading.Barrier(6)
    statuses = []
    errors = []

    def worker():
        try:
            client = intake_app.test_client()
            barrier.wait(timeout=10)
            res = client.post(
                f"/intake/intake-rail-x402-test0001?token={INTAKE_TOKEN}",
                json=CRED_ANSWERS)
            statuses.append(res.status_code)
        except Exception as exc:  # recorded, asserted below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors
    assert statuses == [200] * 6

    owner = [s for s in mock_comms["sent"] if s["to"] == OWNER_EMAIL]
    support = [s for s in mock_comms["sent"]
               if s["to"] == "support@example.com"]
    assert len(owner) == 1
    assert len(support) == 1

    monkeypatch.setattr(pl, "get_pack_intake", real_get)
    row = pl.get_pack_intake(intake_env["db"], "intake-rail-x402-test0001")
    assert row.status == "intake_complete"
    assert row.details["intake_answers"] == CRED_ANSWERS


def test_post_tag_only_required_value_counts_as_missing(
        intake_app, intake_env, mock_comms):
    """B1: required fields are validated on the CLEANED value — a
    business_name of '<b></b>' (or its entity-encoded twin) cleans to
    '' and is no answer at all: 400 naming the field, nothing stored,
    no owner notification about a blank intake."""
    _seed_intake(intake_env["db"])
    client = intake_app.test_client()
    url = f"/intake/intake-rail-x402-test0001?token={INTAKE_TOKEN}"
    for blank in ("<b></b>", "&lt;b&gt;&lt;/b&gt;"):
        res = client.post(
            url, json=dict(CRED_ANSWERS, business_name=blank))
        assert res.status_code == 400
        body = res.get_json()
        assert body["error"] == "missing_required_fields"
        assert "business_name" in (body.get("fields") or [])
    row = pl.get_pack_intake(intake_env["db"], "intake-rail-x402-test0001")
    assert row.status == "created"
    assert "intake_answers" not in (row.details or {})
    assert _owner_sends(mock_comms) == []


def test_post_entity_encoded_markup_is_stripped_not_stored(
        intake_app, intake_env, mock_comms):
    """B2: cleaning unescapes BEFORE stripping, so markup that only
    exists after entity-decoding ('&lt;b&gt;…') is stripped like
    literal markup — the stored answer holds the text, never tags the
    unescape introduced."""
    _seed_intake(intake_env["db"])
    client = intake_app.test_client()
    payload = dict(CRED_ANSWERS,
                   business_name="&lt;b&gt;Acme&lt;/b&gt; Dental LLC")
    res = client.post(
        f"/intake/intake-rail-x402-test0001?token={INTAKE_TOKEN}",
        json=payload)
    assert res.status_code == 200
    row = pl.get_pack_intake(intake_env["db"], "intake-rail-x402-test0001")
    assert row.details["intake_answers"]["business_name"] == \
        "Acme Dental LLC"


def test_settle_prefill_is_sanitized_and_allowlisted(
        intake_app, intake_env, mock_comms):
    """F1: details supplied at settle are buyer-controlled input and
    pass through the intake form's own allowlist + sanitizer before
    they are stored as pre-filled answers: off-SKU keys are dropped,
    entity-encoded script blocks are stripped, over-length values are
    dropped — never stored verbatim."""
    client = intake_app.test_client()
    cid = _checkout_challenge(client)
    res = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": TX_HASH,
        "payer_wallet": PAYER, "buyer_email": BUYER_EMAIL,
        "business_name":
            "&lt;script&gt;alert(1)&lt;/script&gt;Acme Dental LLC",
        "contact_name": "Amy <b>Acme</b>",
        "state": "IA",
        "specialties": "x" * 5000,
        "phone": "+1 555 000 1111",  # webbuilder field, not credentialing
    })
    assert res.status_code == 200, res.get_json()
    row = pl.get_pack_intake(intake_env["db"], f"intake-rail-{cid}")
    answers = row.details["intake_answers"]
    assert "phone" not in answers  # off-allowlist keys are dropped
    assert "specialties" not in answers  # 10x the POST cap: dropped
    assert answers["business_name"] == "Acme Dental LLC"
    assert answers["contact_name"] == "Amy Acme"
    assert answers["state"] == "IA"
    assert answers["contact_email"] == BUYER_EMAIL
    assert all(len(v) <= 500 for v in answers.values())
    assert not any("<" in v or ">" in v for v in answers.values())


# ---------------------------------------------------------------------------
# Round-2 repair (B1 residual): invisible-Unicode-only values are blank
# ---------------------------------------------------------------------------

# Invisible Unicode format characters (category Cf), built with
# chr() so this source stays plain ASCII. str.strip() does not treat
# them as whitespace, so a "value" consisting of only one of these
# survived cleaning and counted as a 1-character answer (Builder B
# delta re-check, defect 4's last variant).
_CF_ONLY_BLANKS = [chr(cp) for cp in (
    0x200B,  # ZERO WIDTH SPACE
    0xFEFF,  # ZERO WIDTH NO-BREAK SPACE (BOM)
    0x200C,  # ZERO WIDTH NON-JOINER
    0x200D,  # ZERO WIDTH JOINER
    0x2060,  # WORD JOINER
    0x180E,  # MONGOLIAN VOWEL SEPARATOR
)]


def test_post_cf_only_required_value_counts_as_missing(
        intake_app, intake_env, mock_comms):
    """B1 residual: a required value made solely of invisible Cf
    characters is NO answer — 400 missing_required_fields naming the
    field, nothing stored, no owner notification."""
    _seed_intake(intake_env["db"])
    client = intake_app.test_client()
    url = f"/intake/intake-rail-x402-test0001?token={INTAKE_TOKEN}"
    for blank in _CF_ONLY_BLANKS:
        res = client.post(
            url, json=dict(CRED_ANSWERS, business_name=blank))
        assert res.status_code == 400, repr(blank)
        body = res.get_json()
        assert body["error"] == "missing_required_fields"
        assert "business_name" in (body.get("fields") or [])
    row = pl.get_pack_intake(intake_env["db"], "intake-rail-x402-test0001")
    assert row.status == "created"
    assert "intake_answers" not in (row.details or {})
    assert _owner_sends(mock_comms) == []


def test_post_cf_only_value_on_second_sku_counts_as_missing(
        intake_app, intake_env, mock_comms):
    """The Cf judgment is not credentialing-specific: a Cf-only value
    on a chroma_compliance required field is likewise missing."""
    _seed_intake(intake_env["db"], pack_sku="chroma_compliance")
    client = intake_app.test_client()
    res = client.post(
        f"/intake/intake-rail-x402-test0001?token={INTAKE_TOKEN}",
        json=dict(CHROMA_ANSWERS,
                  compliance_target=chr(0x2060) + chr(0x200C)))
    assert res.status_code == 400
    body = res.get_json()
    assert body["error"] == "missing_required_fields"
    assert "compliance_target" in (body.get("fields") or [])
    row = pl.get_pack_intake(intake_env["db"], "intake-rail-x402-test0001")
    assert row.status == "created"
    assert "intake_answers" not in (row.details or {})
    assert _owner_sends(mock_comms) == []


def test_post_cc_only_required_value_counts_as_missing(
        intake_app, intake_env, mock_comms):
    """Cc probe outcome: non-whitespace control characters (BEL
    U+0007, NUL U+0000) also survived strip() as 'answers' pre-fix,
    so the judgment removes them too. (Whitespace-class Cc — VT,
    NEL, unit separator — already cleaned to '' via strip() before
    this fix.)"""
    _seed_intake(intake_env["db"])
    client = intake_app.test_client()
    url = f"/intake/intake-rail-x402-test0001?token={INTAKE_TOKEN}"
    for blank in (chr(0x07), chr(0x00)):
        res = client.post(
            url, json=dict(CRED_ANSWERS, business_name=blank))
        assert res.status_code == 400, repr(blank)
        body = res.get_json()
        assert body["error"] == "missing_required_fields"
        assert "business_name" in (body.get("fields") or [])
    row = pl.get_pack_intake(intake_env["db"], "intake-rail-x402-test0001")
    assert row.status == "created"
    assert "intake_answers" not in (row.details or {})
    assert _owner_sends(mock_comms) == []


def test_settle_prefill_cf_only_value_is_dropped(
        intake_app, intake_env, mock_comms):
    """Prefill path: a Cf-only settle-supplied value is dropped by
    sanitize_prefill_answers exactly like a blank — never stored,
    never fatal to the settle, and it cannot complete the intake."""
    client = intake_app.test_client()
    cid = _checkout_challenge(client)
    res = client.post("/rail/settle", json={
        "challenge_id": cid, "tx_hash": TX_HASH,
        "payer_wallet": PAYER, "buyer_email": BUYER_EMAIL,
        "business_name": chr(0x200B) + chr(0xFEFF),
        "contact_name": "Amy Acme",
        "state": "IA",
    })
    assert res.status_code == 200, res.get_json()
    row = pl.get_pack_intake(intake_env["db"], f"intake-rail-{cid}")
    answers = row.details["intake_answers"]
    assert "business_name" not in answers  # Cf-only: dropped as blank
    assert answers["contact_name"] == "Amy Acme"
    assert answers["state"] == "IA"
    assert row.status != "intake_complete"


def test_post_real_text_with_cf_chars_is_stored_intact(
        intake_app, intake_env, mock_comms):
    """The fix is judgment-side only: Cf characters INSIDE real text
    are meaningful (ZWNJ in Persian/Arabic names) and the stored
    value keeps them byte-for-byte."""
    _seed_intake(intake_env["db"])
    client = intake_app.test_client()
    name = "Ali" + chr(0x200C) + "Reza Mohammadi"  # ZWNJ inside text
    res = client.post(
        f"/intake/intake-rail-x402-test0001?token={INTAKE_TOKEN}",
        json=dict(CRED_ANSWERS, business_name=name))
    assert res.status_code == 200
    row = pl.get_pack_intake(intake_env["db"], "intake-rail-x402-test0001")
    assert row.status == "intake_complete"
    assert row.details["intake_answers"]["business_name"] == name


def test_post_cf_padded_real_text_passes(
        intake_app, intake_env, mock_comms):
    """Real text PADDED with Cf characters passes: the judgment sees
    the text as the content (and storage keeps the value as
    submitted, padding included)."""
    _seed_intake(intake_env["db"])
    client = intake_app.test_client()
    padded = chr(0x200B) + chr(0xFEFF) + "Acme Dental LLC" + chr(0x200B)
    res = client.post(
        f"/intake/intake-rail-x402-test0001?token={INTAKE_TOKEN}",
        json=dict(CRED_ANSWERS, business_name=padded))
    assert res.status_code == 200
    row = pl.get_pack_intake(intake_env["db"], "intake-rail-x402-test0001")
    assert row.status == "intake_complete"
    assert "Acme Dental LLC" in \
        row.details["intake_answers"]["business_name"]
