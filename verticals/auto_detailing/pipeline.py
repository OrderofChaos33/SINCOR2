"""Lead → score → quote → outreach → booking → engagement, all in-process.

Zero external calls. Outbound is queued pending owner approval. Live send
requires CHROMA_LIVE_SEND=true plus an explicit Approve click.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .booking_agent import DetailingBookingAgent
from .config import DEFAULT_SHOP, live_send_enabled
from .engagement_agent import DetailingEngagementAgent, infer_package
from .lead_agent import DetailingLeadAgent
from .outreach_agent import DetailingOutreachAgent
from .store import ChromaStore, get_store


def _shop(store: ChromaStore) -> Dict[str, Any]:
    settings = store.get_settings()
    merged = dict(DEFAULT_SHOP)
    merged.update({k: v for k, v in settings.items() if v not in (None, "")})
    return merged


def run_pipeline(
    payload: Dict[str, Any],
    store: Optional[ChromaStore] = None,
) -> Dict[str, Any]:
    """Ingest one lead and walk it as far as the score says to go."""
    store = store or get_store()
    shop = _shop(store)
    leads = DetailingLeadAgent()
    booking = DetailingBookingAgent()
    outreach = DetailingOutreachAgent()
    engage = DetailingEngagementAgent()

    lead = leads.ingest(payload)
    store.upsert_lead(lead)
    store.add_event(lead["lead_id"], "ingested", f"{lead['source']} · {lead['ranking']['band']}")

    ranking = lead["ranking"]
    package_id = payload.get("package_id") or infer_package(lead.get("message") or "", "full_detail")
    calendly_handle = (
        payload.get("calendly_handle")
        or shop.get("calendly_handle")
        or DEFAULT_SHOP["calendly_handle"]
    )
    packages = shop.get("packages") if isinstance(shop.get("packages"), dict) else None

    quote = None
    handoff = None
    sequence = None
    engagement = None

    if ranking["next_action"] in {"book_now", "qualify"}:
        quote = booking.quote(
            {
                "package_id": package_id,
                "vehicle": lead.get("vehicle") or payload.get("vehicle") or {},
                "mobile": bool(payload.get("mobile")),
                "addons": list(payload.get("addons") or []),
                "packages": packages,
            }
        )
        quote = store.save_quote(quote, lead_id=lead["lead_id"])
        store.add_event(
            lead["lead_id"],
            "quoted",
            f"{quote['label']} · ${quote['total']:.0f} · deposit ${quote['deposit']:.0f}",
        )
        store.set_lead_status(lead["lead_id"], "quoted")

        handoff = booking.calendly_handoff(
            {
                "lead_id": lead["lead_id"],
                "package_id": package_id,
                "vehicle": lead.get("vehicle") or {},
                "name": lead.get("name"),
                "email": (lead.get("contact") or {}).get("email"),
                "phone": (lead.get("contact") or {}).get("phone"),
                "calendly_handle": calendly_handle,
                "weather_precip_pct": payload.get("weather_precip_pct"),
                "packages": packages,
            }
        )
        handoff["quote_id"] = quote["quote_id"]
        handoff = store.save_booking(handoff)
        store.add_event(lead["lead_id"], "booking_link", handoff["calendly_url"])
        if handoff.get("weather_hold"):
            store.set_lead_status(lead["lead_id"], "weather_hold")
        else:
            store.set_lead_status(lead["lead_id"], "ready_to_book")

        sequence = outreach.sequence(
            {
                "kind": "quote_followup",
                "name": lead.get("name") or "there",
                "package_id": package_id,
                "channel": "email",
                "email": (lead.get("contact") or {}).get("email"),
                "phone": (lead.get("contact") or {}).get("phone"),
                "lead_id": lead["lead_id"],
                "band": ranking["band"],
                "enqueue": True,
                "store": store,
            }
        )
        store.add_event(lead["lead_id"], "outreach_queued", ",".join(sequence.get("queued_ids") or []))
    else:
        sequence = outreach.sequence(
            {
                "kind": "quote_followup" if ranking["next_action"] == "sequence" else "winback",
                "name": lead.get("name") or "there",
                "package_id": package_id,
                "channel": "email",
                "email": (lead.get("contact") or {}).get("email"),
                "phone": (lead.get("contact") or {}).get("phone"),
                "lead_id": lead["lead_id"],
                "band": ranking["band"],
                "enqueue": True,
                "store": store,
            }
        )
        store.set_lead_status(lead["lead_id"], "nurture")
        store.add_event(lead["lead_id"], "nurture_queued", ",".join(sequence.get("queued_ids") or []))

    if payload.get("visitor_message") or ranking["next_action"] == "book_now":
        engagement = engage.engage(
            {
                "visitor_message": payload.get("visitor_message") or lead.get("message") or "",
                "vehicle": lead.get("vehicle") or {},
                "known_name": lead.get("name"),
                "email": (lead.get("contact") or {}).get("email"),
                "phone": (lead.get("contact") or {}).get("phone"),
                "calendly_handle": calendly_handle,
                "package_id": package_id,
                "packages": packages,
            }
        )
        store.add_event(lead["lead_id"], "engaged", engagement.get("step", ""))

    if payload.get("missed_call"):
        missed = engage.missed_call(
            {
                "phone": (lead.get("contact") or {}).get("phone"),
                "lead_id": lead["lead_id"],
                "band": ranking["band"],
                "enqueue": True,
                "store": store,
            }
        )
        store.add_event(lead["lead_id"], "missed_call_queued", missed.get("queued_id") or "")

    fresh = store.get_lead(lead["lead_id"]) or lead
    return {
        "lead": fresh,
        "quote": quote,
        "booking": handoff,
        "outreach": sequence,
        "engagement": engagement,
        "timeline": store.timeline(lead["lead_id"]),
        "live_send": live_send_enabled(),
    }


def health_payload(store: Optional[ChromaStore] = None) -> Dict[str, Any]:
    from .config import demo_mode, live_send_enabled

    store = store or get_store()
    counts = store.counts()
    return {
        "ok": True,
        "service": "chroma",
        "live_send": live_send_enabled(),
        "demo": demo_mode(),
        **counts,
    }
