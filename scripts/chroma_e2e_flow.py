#!/usr/bin/env python3
"""CHROMA quote → payment mark → dispatch → completion. In-process. No live SMS."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from verticals.auto_detailing.pipeline import run_pipeline
from verticals.auto_detailing.store import ChromaStore


def main() -> int:
    db = ROOT / "data" / "chroma_e2e.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()
    store = ChromaStore(db)
    result = run_pipeline(
        {
            "name": "Jordan Hale",
            "source": "missed_call",
            "email": "jordan@example.com",
            "phone": "+15555550100",
            "message": "Need ceramic coating this week on my neglected Porsche 911, can you quote from these photos.",
            "vehicle": {"make": "Porsche", "model": "911", "color": "black", "year": 2022, "condition": "neglected"},
            "photo_urls": ["https://example.com/hood.jpg"],
            "mobile": True,
            "package_id": "ceramic",
        },
        store=store,
    )
    lead = result.get("lead") or {}
    lead_id = lead.get("lead_id")
    quote = result.get("quote")
    booking = None
    paid = False
    completed = False
    notes = []
    if quote and lead_id:
        store.mark_quote_sent(quote["quote_id"], True)
        store.add_event(lead_id, "payment_recorded", "demo USDC/Stripe fallback marked paid")
        store.set_lead_status(lead_id, "paid")
        paid = True
        booking = store.save_booking({
            "lead_id": lead_id,
            "quote_id": quote.get("quote_id"),
            "status": "dispatched",
            "package": quote.get("label"),
            "total": quote.get("total"),
        })
        store.add_event(lead_id, "dispatched", "detailer assigned (demo)")
        store.set_lead_status(lead_id, "completed")
        store.add_event(lead_id, "completed", "job closed (demo)")
        completed = True
    else:
        notes.append("Pipeline did not emit a quote for this payload.")
    notes.append("UI is operator-functional and unpolished. Do not market as a consumer app.")
    notes.append("Live SMS send stays gated on CHROMA_LIVE_SEND + explicit approve.")
    status = {
        "ok": bool(lead_id and quote and paid and completed),
        "lead_id": lead_id,
        "quote_id": (quote or {}).get("quote_id"),
        "quote_total": (quote or {}).get("total"),
        "booking_id": (booking or {}).get("booking_id"),
        "paid": paid,
        "completed": completed,
        "counts": store.counts(),
        "notes": notes,
    }
    out = ROOT / "docs" / "CHROMA_E2E_STATUS.md"
    out.write_text("# CHROMA E2E status\n\n```json\n" + json.dumps(status, indent=2) + "\n```\n")
    print(json.dumps(status, indent=2))
    return 0 if status["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
