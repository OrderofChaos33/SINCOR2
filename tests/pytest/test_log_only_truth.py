"""log_only truth-gap regression tests (Worker 4a).

The send_gate `_deliver()` stub never touches the network. These tests pin
the truth contract: a log_only receipt is NEVER recorded as sent —
``delivered`` must be False and ``status`` must be "not_sent" — and every
caller must interpret it that way. If anyone reintroduces the old
``ok=True -> "sent"`` mapping, these tests fail.
"""

from __future__ import annotations

import inspect
import json

import pytest

import verticals.auto_detailing.send_gate as send_gate
from verticals.auto_detailing.send_gate import (
    LOG_ONLY_PROVIDER,
    NOT_SENT,
    SENT,
    _actually_delivered,
    approve,
    enqueue,
)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("CHROMA_LIVE_SEND", raising=False)
    monkeypatch.setenv("CHROMA_DB_PATH", str(tmp_path / "chroma.db"))
    from verticals.auto_detailing.store import reset_store

    return reset_store(tmp_path / "chroma.db")


def _live(monkeypatch):
    monkeypatch.setenv("CHROMA_LIVE_SEND", "true")


def _draft(store):
    return enqueue(
        channel="email",
        kind="quote_followup",
        body="Book the bay.",
        to="ava@example.com",
        lead_id="LD-TRUTH",
        store=store,
    )


# ---------------------------------------------------------------------------
# 1. The log_only _deliver() receipt itself
# ---------------------------------------------------------------------------


def test_log_only_deliver_returns_delivered_false():
    receipt = send_gate._deliver({"channel": "email", "to": "a@b.c", "id": "x"})
    assert receipt["delivered"] is False


def test_log_only_status_is_not_sent_never_sent():
    receipt = send_gate._deliver({"channel": "sms", "to": "+1555", "id": "y"})
    assert receipt["status"] == "not_sent"
    assert receipt["status"] not in {"sent", "delivered", "success"}
    assert receipt["provider"] == LOG_ONLY_PROVIDER == "log_only"


def test_log_only_ok_true_does_not_mean_sent():
    """ok=True alone is not delivery — this is the original truth-gap shape."""
    receipt = send_gate._deliver({"channel": "email", "id": "z"})
    assert receipt["ok"] is True  # the gap: ok was True while nothing sent
    assert _actually_delivered(receipt) is False


# ---------------------------------------------------------------------------
# 2. The truth check on receipt shapes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "receipt, expected",
    [
        # New log_only shape
        (
            {"ok": True, "provider": "log_only", "delivered": False, "status": "not_sent"},
            False,
        ),
        # The ORIGINAL truth-gap shape: ok=True, no delivered key, log_only provider
        ({"ok": True, "provider": "log_only"}, False),
        ({"ok": True, "provider": "log_only", "note": "logged as sent."}, False),
        # Real provider, actually delivered
        (
            {"ok": True, "provider": "twilio", "delivered": True, "status": "sent"},
            True,
        ),
        # Real provider, ok but not delivered
        (
            {"ok": True, "provider": "twilio", "delivered": False, "status": "failed"},
            False,
        ),
        # Bare ok=True with no delivered confirmation — not trusted as sent
        ({"ok": True}, False),
        ({"ok": True, "provider": "twilio"}, False),
        # Failures
        ({"ok": False, "provider": "twilio"}, False),
        ({}, False),
        (None, False),
    ],
)
def test_actually_delivered_truth_table(receipt, expected):
    assert _actually_delivered(receipt) is expected


# ---------------------------------------------------------------------------
# 3. Every caller interprets log_only as not-sent
# ---------------------------------------------------------------------------


def test_approve_live_flag_log_only_records_not_sent(store, monkeypatch):
    _live(monkeypatch)
    item = _draft(store)
    result = approve(item["id"], store=store)
    assert result["status"] == NOT_SENT
    assert result["status"] != SENT
    assert result["live"] is False
    stored = store.get_outbound(item["id"])
    assert stored["status"] == "not_sent"
    assert stored["live"] is False
    delivery = json.loads(stored["payload_json"])["delivery"]
    assert delivery["provider"] == "log_only"
    assert delivery["delivered"] is False
    assert delivery["status"] == "not_sent"


def test_approve_live_flag_log_only_event_is_not_sent(store, monkeypatch):
    _live(monkeypatch)
    item = _draft(store)
    approve(item["id"], store=store)
    kinds = [e["kind"] for e in store.timeline("LD-TRUTH")]
    assert "sent" not in kinds
    assert "not_sent" in kinds


def test_approve_live_flag_real_provider_records_sent(store, monkeypatch):
    _live(monkeypatch)
    monkeypatch.setattr(
        send_gate,
        "_deliver",
        lambda _item: {
            "ok": True,
            "provider": "twilio",
            "delivered": True,
            "status": "sent",
        },
    )
    item = _draft(store)
    result = approve(item["id"], store=store)
    assert result["status"] == SENT
    assert result["live"] is True
    kinds = [e["kind"] for e in store.timeline("LD-TRUTH")]
    assert "sent" in kinds


def test_approve_ok_true_without_delivery_never_sent(store, monkeypatch):
    """Even a provider-shaped ok=True is not sent unless delivered is True."""
    _live(monkeypatch)
    monkeypatch.setattr(
        send_gate,
        "_deliver",
        lambda _item: {"ok": True, "provider": "twilio", "delivered": False},
    )
    item = _draft(store)
    result = approve(item["id"], store=store)
    assert result["status"] != SENT
    assert result["live"] is False


def test_blueprint_approve_never_flashes_sent_for_log_only(tmp_path, monkeypatch):
    """End-to-end through the HTTP caller: flash must not claim a send."""
    monkeypatch.setenv("CHROMA_DEMO", "true")
    monkeypatch.setenv("CHROMA_DB_PATH", str(tmp_path / "chroma.db"))
    _live(monkeypatch)
    from verticals.auto_detailing.store import reset_store

    store = reset_store(tmp_path / "chroma.db")
    from sincor2.chroma_app import create_chroma_app

    app = create_chroma_app()
    app.config["TESTING"] = True
    client = app.test_client()
    item = _draft(store)
    resp = client.post(
        f"/chroma/outreach/{item['id']}/approve", follow_redirects=False
    )
    assert resp.status_code in (302, 303)
    with client.session_transaction() as sess:
        flashes = sess.get("_flashes", [])
    messages = " ".join(m for _, m in flashes).lower()
    assert "logged as sent" not in messages
    assert "approved and sent" not in messages
    assert "not sent" in messages
    assert store.get_outbound(item["id"])["status"] == "not_sent"


# ---------------------------------------------------------------------------
# 4. Other stub adapters found in the same sweep
# ---------------------------------------------------------------------------


def test_partner_outreach_stub_never_claims_sent(monkeypatch):
    import sincor2.email_sender as email_sender
    import sincor2.partner_outreach_notify as pon

    class _StubSender:
        mode = "stub"

    monkeypatch.setattr(email_sender, "get_email_sender", lambda: _StubSender())
    monkeypatch.setattr(pon, "_alert_email", lambda: "ops@example.com")
    monkeypatch.setattr(
        pon, "build_partner_reminder_content", lambda: ("s", "<p>x</p>", "x", 3)
    )
    result = pon.send_partner_outreach_reminder()
    assert result["ok"] is True
    assert result["delivered"] is False
    assert result["status"] == "not_sent"
    assert result["status"] not in {"sent", "delivered", "success"}


def test_launch_review_stub_never_claims_sent(monkeypatch, tmp_path):
    import sincor2.email_sender as email_sender
    import sincor2.launch_review_notify as lrn

    class _StubSender:
        mode = "stub"

        def send_email(self, **kwargs):
            return {"status": "stub", "message_id": "stub-x"}

    monkeypatch.setattr(lrn, "_HARD_DISABLE_REMINDER_EMAILS", False)
    monkeypatch.setattr(lrn, "_PROJECT_ROOT", tmp_path)  # keep the stub log file local
    monkeypatch.setattr(email_sender, "get_email_sender", lambda: _StubSender())
    monkeypatch.setattr(lrn, "_alert_email", lambda: "ops@example.com")
    monkeypatch.setattr(
        lrn, "build_reminder_content", lambda: ("s", "<p>x</p>", "x", 2)
    )
    result = lrn.send_launch_review_reminder()
    assert result["delivered"] is False
    assert result["status"] == "not_sent"
    assert result["status"] not in {"sent", "delivered", "success"}


# ---------------------------------------------------------------------------
# 5. Regression tripwires — fail if the truth-gap is reintroduced
# ---------------------------------------------------------------------------


def test_approve_uses_truth_check_not_bare_ok():
    """If someone reverts approve() to `status = SENT if delivered.get("ok")`,
    this fails."""
    source = inspect.getsource(send_gate.approve)
    assert "_actually_delivered(" in source
    assert 'delivered.get("ok") else' not in source


def test_deliver_uses_log_only_provider_constant():
    source = inspect.getsource(send_gate._deliver)
    assert "LOG_ONLY_PROVIDER" in source
    assert '"delivered": False' in source or "'delivered': False" in source


def test_not_sent_and_sent_are_distinct_literals():
    assert NOT_SENT == "not_sent"
    assert SENT == "sent"
    assert NOT_SENT != SENT
    assert NOT_SENT not in {"sent", "delivered", "success"}
