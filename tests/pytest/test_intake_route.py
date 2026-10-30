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
