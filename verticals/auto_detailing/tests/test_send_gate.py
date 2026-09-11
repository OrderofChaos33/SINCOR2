"""Outreach queue blocks sends without approval + CHROMA_LIVE_SEND=true."""

from __future__ import annotations

import pytest

from verticals.auto_detailing.send_gate import (
    APPROVED_DRY_RUN,
    KILLED,
    PENDING,
    SENT,
    approve,
    edit,
    enqueue,
    kill,
)


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
    monkeypatch.setenv("CHROMA_LIVE_SEND", "true")
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
    item = enqueue(channel="email", kind="quote_followup", body="Book the bay.", store=store)
    first = approve(item["id"], store=store)
    assert first["status"] == SENT
    with pytest.raises(ValueError, match="Already sent"):
        approve(item["id"], store=store)
    assert store.get_outbound(item["id"])["status"] == SENT
