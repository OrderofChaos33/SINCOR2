"""Outbound send gate — nothing fires without approval + CHROMA_LIVE_SEND=true.

Every drafted email, SMS, or social post lands in the outreach queue with
status ``pending_approval``. Approve / Edit / Kill are the only mutations.
A local TOA-style score is attached for the shop owner (does not call the
marketplace / underwriting TOA stack).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .config import live_send_enabled
from .store import ChromaStore, get_store

logger = logging.getLogger("chroma.send_gate")

PENDING = "pending_approval"
APPROVED_DRY_RUN = "approved_dry_run"
SENT = "sent"
NOT_SENT = "not_sent"
SEND_FAILED = "send_failed"
SENDING = "sending"
KILLED = "killed"
BLOCKED = "blocked_live_flag_off"

#: Provider marker for the log-only delivery stub. A result carrying this
#: provider was NEVER delivered — it was only recorded in the log.
LOG_ONLY_PROVIDER = "log_only"


def toa_score_outbound(
    *,
    channel: str,
    kind: str,
    body: str,
    band: Optional[str] = None,
) -> Dict[str, Any]:
    """Cheap local TOA rubric — steer-to-booking, no spam, quiet hours later."""
    score = 0.55
    note_bits = []
    text = (body or "").lower()
    if any(k in text for k in ("book", "bay", "calendly", "deposit")):
        score += 0.18
        note_bits.append("steers to a bay")
    if channel in {"sms", "email"}:
        score += 0.05
    if kind in {"quote_followup", "missed_call"}:
        score += 0.08
        note_bits.append("speed-to-lead")
    if band == "hot":
        score += 0.10
        note_bits.append("hot lead")
    if any(k in text for k in ("buy now!!!", "limited time", "act fast")):
        score -= 0.25
        note_bits.append("hype language — trim it")
    score = round(min(max(score, 0.05), 0.98), 2)
    return {
        "toa_score": score,
        "toa_note": "; ".join(note_bits) or "clear to send after owner OK",
        "needs_owner": True,
    }


def enqueue(
    *,
    channel: str,
    kind: str,
    body: str,
    to: Optional[str] = None,
    subject: Optional[str] = None,
    lead_id: Optional[str] = None,
    band: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    store: Optional[ChromaStore] = None,
) -> Dict[str, Any]:
    """Draft an outbound item. Never sends."""
    store = store or get_store()
    toa = toa_score_outbound(channel=channel, kind=kind, body=body, band=band)
    item = {
        "channel": channel,
        "kind": kind,
        "to": to,
        "subject": subject,
        "body": body,
        "lead_id": lead_id,
        "status": PENDING,
        "live": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        **toa,
        "metadata": metadata or {},
    }
    saved = store.save_outbound(item)
    store.add_event(lead_id, "queued_outbound", f"{channel}:{kind} → {saved['id']}")
    logger.info("CHROMA queued %s %s id=%s live_send=%s", channel, kind, saved["id"], live_send_enabled())
    return saved


def _actually_delivered(result: Dict[str, Any]) -> bool:
    """Truth check for a delivery receipt: nothing counts as sent unless
    ALL three hold — ``ok`` is truthy, the provider is a real provider
    (never ``LOG_ONLY_PROVIDER``), and ``delivered`` is explicitly True.

    This is the fix for the log-only truth gap: the old code treated
    ``ok=True`` alone as "sent", so the log_only stub (which never touches
    the network) was recorded as ``status="sent", live=True``.
    """
    if not isinstance(result, dict):
        return False
    if not result.get("ok"):
        return False
    if result.get("provider") == LOG_ONLY_PROVIDER:
        return False
    return result.get("delivered") is True


def approve(item_id: str, store: Optional[ChromaStore] = None) -> Dict[str, Any]:
    """Owner said yes. Still blocked unless CHROMA_LIVE_SEND=true.

    TRUTH CONTRACT: even with the live flag on, an item is recorded
    ``status="sent", live=True`` ONLY when a real provider actually
    delivered it (see :func:`_actually_delivered`). A log_only receipt —
    no provider wired — is recorded ``status="not_sent", live=False`` and
    can never be confused with a send.
    """
    store = store or get_store()
    item = store.get_outbound(item_id)
    if not item:
        raise ValueError(f"Unknown outbound item: {item_id}")
    if item.get("status") == KILLED:
        raise ValueError("Killed items cannot be approved")
    if item.get("status") == SENT:
        raise ValueError("Already sent items cannot be approved again")
    if item.get("status") == SENDING:
        raise ValueError("Send already in progress for this item")

    if not live_send_enabled():
        updated = store.update_outbound(
            item_id,
            status=APPROVED_DRY_RUN,
            live=False,
            delivery="dry_run",
            delivery_note="Owner approved. CHROMA_LIVE_SEND is off — logged, not sent.",
        )
        store.add_event(item.get("lead_id"), "approved_dry_run", item_id)
        logger.info("CHROMA dry-run hold id=%s", item_id)
        return updated or item

    claimed = store.claim_outbound_for_send(item_id)
    if not claimed:
        latest = store.get_outbound(item_id)
        if not latest:
            raise ValueError(f"Unknown outbound item: {item_id}")
        if latest.get("status") == KILLED:
            raise ValueError("Killed items cannot be approved")
        if latest.get("status") == SENT:
            raise ValueError("Already sent items cannot be approved again")
        raise ValueError("Outbound item is no longer sendable")

    delivered = _deliver(claimed)
    sent = _actually_delivered(delivered)
    if sent:
        new_status, event_kind = SENT, "sent"
    elif delivered.get("provider") == LOG_ONLY_PROVIDER:
        new_status, event_kind = NOT_SENT, "not_sent"
    else:
        new_status, event_kind = SEND_FAILED, "send_failed"
    updated = store.update_outbound(
        item_id,
        status=new_status,
        live=sent,
        delivery=delivered,
    )
    store.add_event(claimed.get("lead_id"), event_kind, item_id)
    return updated or item


def kill(item_id: str, store: Optional[ChromaStore] = None) -> Dict[str, Any]:
    store = store or get_store()
    item = store.get_outbound(item_id)
    if not item:
        raise ValueError(f"Unknown outbound item: {item_id}")
    updated = store.update_outbound(item_id, status=KILLED, live=False)
    store.add_event(item.get("lead_id"), "killed_outbound", item_id)
    return updated or item


def edit(
    item_id: str,
    *,
    body: Optional[str] = None,
    subject: Optional[str] = None,
    store: Optional[ChromaStore] = None,
) -> Dict[str, Any]:
    store = store or get_store()
    item = store.get_outbound(item_id)
    if not item:
        raise ValueError(f"Unknown outbound item: {item_id}")
    if item.get("status") == SENT:
        raise ValueError("Already sent items cannot be edited")
    fields: Dict[str, Any] = {"status": PENDING}
    if body is not None:
        fields["body"] = body
    if subject is not None:
        fields["subject"] = subject
    toa = toa_score_outbound(
        channel=item.get("channel") or "email",
        kind=item.get("kind") or "draft",
        body=fields.get("body", item.get("body") or ""),
    )
    fields.update(toa)
    updated = store.update_outbound(item_id, **fields)
    store.add_event(item.get("lead_id"), "edited_outbound", item_id)
    return updated or item


def _deliver(item: Dict[str, Any]) -> Dict[str, Any]:
    """Live delivery stub. Twilio/SMTP stay unwired unless providers exist.

    TRUTH CONTRACT: this default path never touches the network. It returns
    a log-only receipt with ``delivered=False`` and ``status="not_sent"`` —
    callers MUST NOT record this as sent (see :func:`_actually_delivered`).

    Tests patch this with a provider-shaped result, e.g.
    ``{"ok": True, "provider": "twilio", "delivered": True, "status": "sent"}``.
    """
    channel = item.get("channel")
    logger.info(
        "CHROMA LIVE_SEND would deliver channel=%s to=%s id=%s — no provider wired; logged, NOT sent",
        channel,
        item.get("to") or item.get("to_addr"),
        item.get("id"),
    )
    return {
        "ok": True,
        "provider": LOG_ONLY_PROVIDER,
        "delivered": False,
        "status": NOT_SENT,
        "note": "CHROMA_LIVE_SEND=true but no Twilio/SMTP provider wired — logged only, NOT sent.",
        "at": datetime.now(timezone.utc).isoformat(),
    }


def can_fire(item: Dict[str, Any]) -> bool:
    """True only when approved AND the live flag is on."""
    return bool(live_send_enabled() and item.get("status") in {SENT, APPROVED_DRY_RUN})
