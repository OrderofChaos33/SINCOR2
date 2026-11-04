"""Wren PR 1 (hardening round): intake readability + owner-notify truth.

Builds on the original Wren PR 1 intake route (test_intake_route.py):

  * GET /api/admin/intakes/<id> — an operator can READ one completed
    intake (pack status, customer email, submitted answers) without a
    SQLite shell. Admin-gated: 401 without valid admin credentials,
    and the 401 body carries no intake data.
  * _notify_owner treats any non-"sent" EmailSender status (failed,
    stub, exception) as a FAILURE: it records a retryable
    ``intake_owner_notify_failed`` alert and persists
    details.owner_notified=False. On real sends it persists True and
    the email body carries the buyer's raw answers.
  * The intake page only claims "our team has been notified" when the
    POST response says owner_notified is true.
  * The advancing POST stamps details.intake_completed_at (UTC ISO),
    and list_stale_completed_pack_intakes ages rows by that stamp,
    falling back to created_at for unstamped records.
  * The pack fulfillment email (rail_routes) states the true
    sequence — intake -> owner review -> human-reviewed deliverable —
    with no queue/deployment promises.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import sincor2.rail_routes as rr
from sincor2 import payment_ledger as pl

OWNER_EMAIL = "owner@example.com"
BUYER_EMAIL = "buyer@example.com"
INTAKE_TOKEN = "tok-test-unguessable-123"
INTAKE_ID = "intake-rail-x402-test0001"
EVENT_ID = "rail-x402-test0001"

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


@pytest.fixture()
def intake_env(tmp_path, monkeypatch):
    """Hermetic ledger + owner/support alert addresses."""
    monkeypatch.setenv("SINCOR_REVENUE_DB", str(tmp_path / "revenue.db"))
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LAUNCH_REVIEW_ALERT_EMAIL", OWNER_EMAIL)
    monkeypatch.setenv("SUPPORT_EMAIL", OWNER_EMAIL)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin")
    return {"db": str(tmp_path / "revenue.db"), "tmp": tmp_path}


@pytest.fixture()
def intake_app():
    """Minimal Flask app: real rail blueprint + the intake blueprint
    (mirrors the mvp_app mount, as in test_intake_route.py)."""
    from flask import Flask

    from sincor2.blueprints.intake import init_intake_routes

    template_dir = Path(__file__).resolve().parents[2] / "templates"
    app = Flask(__name__, template_folder=str(template_dir))
    app.secret_key = "test-secret-key-with-32-char-minimum-ok"
    app.config["TESTING"] = True
    rr.init_rail_routes(app)
    init_intake_routes(app)
    return app


@pytest.fixture()
def sent_mail(monkeypatch):
    """Capture outbound email; every send reports status 'sent'."""
    sent = []

    def fake_send_email(self, to_email, to_name="", subject="",
                        html_content="", text_content=None, metadata=None):
        sent.append({
            "to": to_email, "subject": subject, "html": html_content,
            "text": text_content, "metadata": metadata or {},
        })
        return {"status": "sent", "message_id": "msg-test-1"}

    monkeypatch.setattr(
        "sincor2.email_sender.EmailSender.send_email", fake_send_email)
    return sent


def _seed_intake(db, *, status="created", details=None):
    base_details = {
        "pack_name": "Credentialing Launch",
        "module": "verticals/healthcare",
        "payer_wallet": "0x" + "ab" * 20,
        "rail": True,
        "intake_token": INTAKE_TOKEN,
    }
    if details:
        base_details.update(details)
    intake, _is_new = pl.record_pack_intake(
        db,
        intake_id=INTAKE_ID,
        customer_email=BUYER_EMAIL,
        pack_sku="credentialing_launch",
        event_id=EVENT_ID,
        intake_url=f"https://getsincor.com/intake/{INTAKE_ID}"
                   f"?token={INTAKE_TOKEN}",
        details=base_details,
    )
    if status != "created":
        pl.update_pack_intake_status(db, INTAKE_ID, status)
    return intake


def _post_answers(client):
    return client.post(f"/intake/{INTAKE_ID}?token={INTAKE_TOKEN}",
                       json=CRED_ANSWERS)


def _notify_alerts(db):
    return [a for a in pl.list_alerts(db)
            if a.kind == "intake_owner_notify_failed"]


def _admin_session(client):
    with client.session_transaction() as sess:
        sess["is_admin"] = True
        sess["admin_username"] = os.environ.get("ADMIN_USERNAME", "admin")


# ---------------------------------------------------------------------------
# GET /api/admin/intakes/<id>: the gate
# ---------------------------------------------------------------------------

def test_admin_detail_401_without_credentials(intake_app, intake_env,
                                              sent_mail):
    _seed_intake(intake_env["db"])
    assert _post_answers(intake_app.test_client()).status_code == 200

    client = intake_app.test_client()
    res = client.get(f"/api/admin/intakes/{INTAKE_ID}")
    assert res.status_code == 401
    body = res.get_data(as_text=True)
    # Fail closed means no leak either: neither the buyer email nor
    # any submitted answer may appear in the rejection body.
    assert BUYER_EMAIL not in body
    assert "Acme Dental" not in body
    assert "Delta Dental" not in body


def test_admin_detail_401_with_wrong_admin_key(intake_app, intake_env):
    _seed_intake(intake_env["db"])
    client = intake_app.test_client()
    res = client.get(f"/api/admin/intakes/{INTAKE_ID}",
                     headers={"X-Admin-Key": "not-the-admin-password"})
    assert res.status_code == 401


def test_admin_detail_404_for_unknown_id_with_admin(intake_app,
                                                     intake_env):
    _seed_intake(intake_env["db"])
    client = intake_app.test_client()
    res = client.get("/api/admin/intakes/intake-rail-nope",
                     headers={"X-Admin-Key": os.environ["ADMIN_PASSWORD"]})
    assert res.status_code == 404


def test_admin_detail_returns_answers_with_admin_key(
        intake_app, intake_env, sent_mail):
    _seed_intake(intake_env["db"])
    client = intake_app.test_client()
    assert _post_answers(client).status_code == 200

    res = client.get(f"/api/admin/intakes/{INTAKE_ID}",
                     headers={"X-Admin-Key": os.environ["ADMIN_PASSWORD"]})
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert body["intake_id"] == INTAKE_ID
    assert body["status"] == "intake_complete"
    assert body["customer_email"] == BUYER_EMAIL
    assert body["intake_answers"] == CRED_ANSWERS
    assert body["intake_completed_at"]  # stamped by the advancing POST
    assert body["owner_notified"] is True


def test_admin_detail_returns_answers_with_admin_session(
        intake_app, intake_env, sent_mail):
    _seed_intake(intake_env["db"])
    client = intake_app.test_client()
    assert _post_answers(client).status_code == 200

    _admin_session(client)
    res = client.get(f"/api/admin/intakes/{INTAKE_ID}")
    assert res.status_code == 200
    assert res.get_json()["intake_answers"] == CRED_ANSWERS


def test_admin_detail_mounted_and_gated_on_mvp_app(intake_env):
    """The production app serves the endpoint behind the existing
    /api/admin/intakes before_request guard."""
    import sincor2.mvp_app as mvp

    rules = {rule.rule for rule in mvp.app.url_map.iter_rules()}
    assert "/api/admin/intakes/<intake_id>" in rules
    res = mvp.app.test_client().get(f"/api/admin/intakes/{INTAKE_ID}")
    assert res.status_code == 401


# ---------------------------------------------------------------------------
# Owner notification: sent means sent — everything else is a failure
# ---------------------------------------------------------------------------

def test_notify_sent_includes_answers_and_marks_notified(
        intake_app, intake_env, sent_mail):
    _seed_intake(intake_env["db"])
    client = intake_app.test_client()
    res = _post_answers(client)
    assert res.status_code == 200
    assert res.get_json()["owner_notified"] is True

    owner = [s for s in sent_mail if s["to"] == OWNER_EMAIL]
    assert len(owner) == 1
    # The internal notification carries the RAW answers — the owner
    # reads the intake from the email, not from a SQLite shell.
    assert "Acme Dental LLC" in owner[0]["text"]
    assert "Delta Dental, Cigna" in owner[0]["text"]
    assert "dental practice" in owner[0]["text"]

    row = pl.get_pack_intake(intake_env["db"], INTAKE_ID)
    assert row.details["owner_notified"] is True
    assert _notify_alerts(intake_env["db"]) == []


def test_notify_stub_mode_records_alert_and_post_stays_200(
        intake_app, intake_env, monkeypatch):
    """The REAL EmailSender in stub mode (no provider configured)
    reports status 'stub' — not sent. That must surface as a
    recorded retryable alert + owner_notified False, while the
    buyer's POST still succeeds."""
    for var in ("RESEND_API_KEY", "SENDGRID_API_KEY",
                "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN",
                "TWILO_ID", "TWILO_AUTH"):
        monkeypatch.delenv(var, raising=False)
    _seed_intake(intake_env["db"])
    client = intake_app.test_client()
    res = _post_answers(client)
    assert res.status_code == 200
    assert res.get_json()["ok"] is True
    assert res.get_json()["owner_notified"] is False

    row = pl.get_pack_intake(intake_env["db"], INTAKE_ID)
    assert row.status == "intake_complete"
    assert row.details["owner_notified"] is False

    alerts = _notify_alerts(intake_env["db"])
    assert len(alerts) == 1
    assert alerts[0].details["retryable"] is True
    assert alerts[0].details["intake_id"] == INTAKE_ID


def test_notify_failed_status_records_alert(intake_app, intake_env,
                                            monkeypatch):
    def fake_send_email(self, to_email, to_name="", subject="",
                        html_content="", text_content=None, metadata=None):
        return {"status": "failed", "error": "provider 500"}

    monkeypatch.setattr(
        "sincor2.email_sender.EmailSender.send_email", fake_send_email)
    _seed_intake(intake_env["db"])
    client = intake_app.test_client()
    res = _post_answers(client)
    assert res.status_code == 200
    assert res.get_json()["owner_notified"] is False
    row = pl.get_pack_intake(intake_env["db"], INTAKE_ID)
    assert row.details["owner_notified"] is False
    alerts = _notify_alerts(intake_env["db"])
    assert len(alerts) == 1
    assert alerts[0].details["retryable"] is True


def test_notify_exception_records_alert(intake_app, intake_env,
                                        monkeypatch):
    def boom(self, *a, **kw):
        raise RuntimeError("provider down")

    monkeypatch.setattr(
        "sincor2.email_sender.EmailSender.send_email", boom)
    _seed_intake(intake_env["db"])
    client = intake_app.test_client()
    res = _post_answers(client)
    assert res.status_code == 200
    row = pl.get_pack_intake(intake_env["db"], INTAKE_ID)
    assert row.status == "intake_complete"
    assert row.details["owner_notified"] is False
    assert len(_notify_alerts(intake_env["db"])) == 1


def test_intake_template_notified_claim_is_conditional():
    """The buyer page may claim 'our team has been notified' only
    when the POST response says the notification was sent."""
    template = (Path(__file__).resolve().parents[2]
                / "templates" / "intake.html").read_text()
    assert "Intake received" in template
    assert "body.owner_notified" in template


# ---------------------------------------------------------------------------
# intake_completed_at + stale-list aging
# ---------------------------------------------------------------------------

def test_completed_at_stamped_on_advance_and_stable(
        intake_app, intake_env, sent_mail):
    _seed_intake(intake_env["db"])
    client = intake_app.test_client()
    assert _post_answers(client).status_code == 200
    row = pl.get_pack_intake(intake_env["db"], INTAKE_ID)
    stamp = row.details.get("intake_completed_at")
    assert stamp
    parsed = datetime.fromisoformat(stamp)
    assert parsed.tzinfo is not None  # UTC ISO, not a naive local time

    # A re-submission (answers update, no advance) must NOT move the
    # completion stamp — the operator clock started at first
    # completion.
    updated = dict(CRED_ANSWERS, business_name="Acme Dental West")
    res = client.post(f"/intake/{INTAKE_ID}?token={INTAKE_TOKEN}",
                      json=updated)
    assert res.status_code == 200
    row = pl.get_pack_intake(intake_env["db"], INTAKE_ID)
    assert row.details["intake_completed_at"] == stamp


def _seed_completed(db, intake_id, *, created_hours_ago,
                    completed_hours_ago):
    """One intake_complete row with controlled created/completed ages."""
    pl.record_pack_intake(
        db, intake_id=intake_id, customer_email=BUYER_EMAIL,
        pack_sku="credentialing_launch", event_id=f"evt-{intake_id}",
        intake_url="", details={"pack_name": "Credentialing Launch"})
    pl.update_pack_intake_status(db, intake_id, "intake_complete")
    now = datetime.now(timezone.utc)
    created = (now - timedelta(hours=created_hours_ago)).isoformat()
    details = {}
    if completed_hours_ago is not None:
        details["intake_completed_at"] = (
            now - timedelta(hours=completed_hours_ago)).isoformat()
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE pack_intakes SET created_at=? WHERE intake_id=?",
            (created, intake_id))
        conn.commit()
    finally:
        conn.close()
    if details:
        pl.update_pack_intake_status(db, intake_id, "intake_complete",
                                     details=details)


def test_stale_list_ages_by_intake_completed_at(tmp_path):
    db = str(tmp_path / "revenue.db")
    # Created 1h ago but completed 72h ago: stale — the operator
    # clock runs from completion.
    _seed_completed(db, "intake-a", created_hours_ago=1,
                    completed_hours_ago=72)
    # Created 72h ago but completed 1h ago: NOT stale — creation age
    # must not condemn a just-completed intake.
    _seed_completed(db, "intake-b", created_hours_ago=72,
                    completed_hours_ago=1)
    # No completion stamp (pre-stamp record): falls back to
    # created_at, so a 72h-old record is stale as before.
    _seed_completed(db, "intake-c", created_hours_ago=72,
                    completed_hours_ago=None)

    stale = pl.list_stale_completed_pack_intakes(db, min_age_hours=24)
    assert {i.intake_id for i in stale} == {"intake-a", "intake-c"}


# ---------------------------------------------------------------------------
# Rail pack email copy: the true sequence, no queue/deployment promises
# ---------------------------------------------------------------------------

def test_pack_email_copy_has_no_queue_or_deployment_promises(monkeypatch):
    captured = {}

    def fake_send_email(self, to_email, to_name="", subject="",
                        html_content="", text_content=None, metadata=None):
        captured["html"] = html_content
        captured["text"] = text_content or ""
        return {"status": "sent", "message_id": "msg-test-1"}

    monkeypatch.setattr(
        "sincor2.email_sender.EmailSender.send_email", fake_send_email)
    rr._send_pack_email(
        to_email=BUYER_EMAIL, customer_name="Amy Acme",
        pack_name="Credentialing Launch",
        intake_url=f"https://getsincor.com/intake/{INTAKE_ID}"
                   f"?token={INTAKE_TOKEN}",
        order_id="ord-test-1")

    for body in (captured["html"], captured["text"]):
        lowered = body.lower()
        assert "queued" not in lowered
        assert "deployment kickoff" not in lowered
        # The true sequence, matching pricing.html's
        # "What happens after you pay".
        assert "owner reviews your intake" in lowered
        assert "human-reviewed deliverable" in lowered
        assert INTAKE_ID in body  # the intake link survived the rewrite
