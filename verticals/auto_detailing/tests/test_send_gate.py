"""Outreach queue blocks sends without approval + CHROMA_LIVE_SEND=true."""

from __future__ import annotations

import json
import threading
import time

import pytest

import verticals.auto_detailing.send_gate as send_gate
from verticals.auto_detailing.send_gate import (
    APPROVED_DRY_RUN,
    KILLED,
    NOT_SENT,
    PENDING,
    SEND_FAILED,
    SENT,
    approve,
    edit,
    enqueue,
    kill,
)


def _provider_deliver(result_provider="twilio"):
    """A real provider-shaped delivery receipt — actually delivered."""
    def fake_deliver(_item):
        return {
            "ok": True,
            "provider": result_provider,
            "delivered": True,
            "status": "sent",
        }

    return fake_deliver


def test_enqueue_never_sends(store, monkeypatch):
    monkeypatch.delenv("CHROMA_LIVE_SEND", raising=False)
    item = enqueue(
        channel="email",
        kind="quote_followup",
        body="Your bay is open. Book from the link.",
        to="ava@example.com",
        subject="Quote live",
        lead_id="LD-1",
        store=store,
    )
    assert item["status"] == PENDING
    assert item["live"] is False


def test_approve_without_live_flag_is_dry_run(store, monkeypatch):
    monkeypatch.delenv("CHROMA_LIVE_SEND", raising=False)
    item = enqueue(
        channel="sms",
        kind="missed_call",
        body="We missed you.",
        to="+1555",
        store=store,
    )
    result = approve(item["id"], store=store)
    assert result["status"] == APPROVED_DRY_RUN
    assert result["live"] is False
    assert result["status"] != SENT


def test_approve_with_live_flag_marks_sent(store, monkeypatch):
    """A real provider delivery still records sent/live."""
    monkeypatch.setenv("CHROMA_LIVE_SEND", "true")
    monkeypatch.setattr(send_gate, "_deliver", _provider_deliver())
    item = enqueue(
        channel="email",
        kind="quote_followup",
        body="Book the bay.",
        to="ava@example.com",
        store=store,
    )
    result = approve(item["id"], store=store)
    assert result["status"] == SENT
    assert result["live"] is True


def test_approve_with_live_flag_but_no_provider_is_not_sent(store, monkeypatch):
    """log_only truth fix: the default stub never touches the network, so
    approve() with CHROMA_LIVE_SEND=true must NOT record sent."""
    monkeypatch.setenv("CHROMA_LIVE_SEND", "true")
    item = enqueue(
        channel="email",
        kind="quote_followup",
        body="Book the bay.",
        to="ava@example.com",
        store=store,
    )
    result = approve(item["id"], store=store)
    assert result["status"] == NOT_SENT
    assert result["status"] != SENT
    assert result["live"] is False
    # The full receipt is preserved inside the stored payload_json.
    delivery = json.loads(store.get_outbound(item["id"])["payload_json"])["delivery"]
    assert delivery["provider"] == "log_only"
    assert delivery["delivered"] is False
    assert delivery["status"] == "not_sent"


def test_kill_blocks_later_approve(store, monkeypatch):
    monkeypatch.setenv("CHROMA_LIVE_SEND", "true")
    item = enqueue(channel="email", kind="winback", body="Come back", store=store)
    kill(item["id"], store=store)
    with pytest.raises(ValueError):
        approve(item["id"], store=store)
    assert store.get_outbound(item["id"])["status"] == KILLED


def test_edit_resets_to_pending(store):
    item = enqueue(channel="email", kind="quote_followup", body="v1", store=store)
    edited = edit(item["id"], body="v2 cleaner copy that books the bay", store=store)
    assert edited["body"] == "v2 cleaner copy that books the bay"
    assert edited["status"] == PENDING


def test_approve_rejects_already_sent(store, monkeypatch):
    monkeypatch.setenv("CHROMA_LIVE_SEND", "true")
    monkeypatch.setattr(send_gate, "_deliver", _provider_deliver())
    item = enqueue(channel="email", kind="quote_followup", body="Book the bay.", store=store)
    first = approve(item["id"], store=store)
    assert first["status"] == SENT
    with pytest.raises(ValueError, match="Already sent"):
        approve(item["id"], store=store)
    assert store.get_outbound(item["id"])["status"] == SENT


def test_edit_rejects_already_sent(store, monkeypatch):
    monkeypatch.setenv("CHROMA_LIVE_SEND", "true")
    monkeypatch.setattr(send_gate, "_deliver", _provider_deliver())
    item = enqueue(channel="email", kind="quote_followup", body="Book the bay.", store=store)
    approve(item["id"], store=store)
    with pytest.raises(ValueError, match="Already sent"):
        edit(item["id"], body="new copy", store=store)


def test_ok_true_without_delivery_is_send_failed(store, monkeypatch):
    """A provider-shaped result with ok=True but delivered=False must not
    be recorded as sent — the truth check requires all three."""
    monkeypatch.setenv("CHROMA_LIVE_SEND", "true")

    def flaky_deliver(_item):
        return {"ok": True, "provider": "twilio", "delivered": False, "status": "failed"}

    monkeypatch.setattr(send_gate, "_deliver", flaky_deliver)
    item = enqueue(channel="email", kind="quote_followup", body="Book now.", store=store)
    result = approve(item["id"], store=store)
    assert result["status"] == SEND_FAILED
    assert result["status"] != SENT
    assert result["live"] is False


def test_concurrent_approve_delivers_once(store, monkeypatch):
    monkeypatch.setenv("CHROMA_LIVE_SEND", "true")
    item = enqueue(channel="email", kind="quote_followup", body="Book now.", store=store)
    calls = {"count": 0}

    def fake_deliver(_item):
        time.sleep(0.05)
        calls["count"] += 1
        return {"ok": True, "provider": "twilio", "delivered": True, "status": "sent"}

    monkeypatch.setattr(send_gate, "_deliver", fake_deliver)
    results = []
    errors = []

    def _approve():
        try:
            results.append(approve(item["id"], store=store))
        except ValueError as exc:
            errors.append(str(exc))

    t1 = threading.Thread(target=_approve)
    t2 = threading.Thread(target=_approve)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert calls["count"] == 1
    assert len(results) == 1
    assert len(errors) == 1
    assert store.get_outbound(item["id"])["status"] == SENT
