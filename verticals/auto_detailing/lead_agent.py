"""Lead aggregation, scoring, and qualification for detailing shops.

Intent scoring rubric (0–100, clamped 5–99)
==========================================
raw = (source_weight * 0.42) + (intent * 0.32) + vehicle_lift + photo_lift + identity_lift

source_weight  0–1 from SOURCE_WEIGHT (missed_call 0.96 … tiktok 0.55)
intent         starts at 0.08, plus keyword lifts from INTENT_KEYWORDS, capped at 1.0
vehicle_lift   +0.18 luxury make, +0.10 dirty/neglected, +0.04 known year
photo_lift     +0.12 if photo_urls present (photo-quote)
identity_lift  +0.06 if email or phone

Bands
-----
>= 78  hot      next_action = book_now
>= 58  warm     next_action = qualify
else   nurture  next_action = sequence
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import uuid4

from .protocols import INTENT_KEYWORDS, LUXURY_MAKES, SOURCE_WEIGHT
from .schemas import LeadIngestRequest, Vehicle


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def score_lead(req: LeadIngestRequest) -> Dict[str, Any]:
    text = (req.message or "").lower()
    source = SOURCE_WEIGHT.get(req.source, 0.55)
    intent = 0.08
    matched: List[str] = []
    for kw, lift in INTENT_KEYWORDS.items():
        if kw in text:
            intent += lift
            matched.append(kw)
    intent = min(intent, 1.0)

    vehicle_lift = 0.0
    make = (req.vehicle.make if req.vehicle else None) or ""
    if make.lower() in LUXURY_MAKES:
        vehicle_lift += 0.18
    condition = (req.vehicle.condition if req.vehicle else None) or ""
    if condition in {"dirty", "neglected"}:
        vehicle_lift += 0.10
    if req.vehicle and req.vehicle.year:
        vehicle_lift += 0.04

    photo_lift = 0.12 if req.photo_urls else 0.0
    identity_lift = 0.06 if (req.email or req.phone) else 0.0

    raw = (source * 0.42) + (intent * 0.32) + vehicle_lift + photo_lift + identity_lift
    score = round(min(max(raw, 0.05), 0.99) * 100, 1)

    if score >= 78:
        band = "hot"
        action = "book_now"
    elif score >= 58:
        band = "warm"
        action = "qualify"
    else:
        band = "nurture"
        action = "sequence"

    return {
        "score": score,
        "band": band,
        "next_action": action,
        "matched_intents": matched,
        "source_weight": source,
        "photo_quote": bool(req.photo_urls),
        "sla_seconds": 90,
        "rubric": {
            "source": round(source * 0.42, 4),
            "intent": round(intent * 0.32, 4),
            "vehicle_lift": vehicle_lift,
            "photo_lift": photo_lift,
            "identity_lift": identity_lift,
        },
    }


class DetailingLeadAgent:
    """Ingests multi-channel leads and ranks them for booking."""

    def ingest(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        vehicle_raw = payload.get("vehicle") or {}
        vehicle = Vehicle.model_validate(vehicle_raw) if vehicle_raw else None
        req = LeadIngestRequest(
            source=payload.get("source", "website"),
            name=payload.get("name"),
            email=payload.get("email"),
            phone=payload.get("phone"),
            message=payload.get("message") or payload.get("input") or "",
            vehicle=vehicle,
            photo_urls=list(payload.get("photo_urls") or []),
            zip_code=payload.get("zip_code"),
            landing_page=payload.get("landing_page"),
            utm=dict(payload.get("utm") or {}),
        )
        ranking = score_lead(req)
        lead_id = payload.get("lead_id") or f"LD-{uuid4().hex[:8].upper()}"
        return {
            "lead_id": lead_id,
            "ingested_at": _now(),
            "source": req.source,
            "name": req.name,
            "contact": {"email": req.email, "phone": req.phone},
            "vehicle": req.vehicle.model_dump() if req.vehicle else None,
            "message": req.message,
            "ranking": ranking,
            "pipeline_stage": "new" if ranking["band"] != "hot" else "ready_to_book",
        }

    def score_batch(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        leads = payload.get("leads") or []
        ranked = []
        for item in leads:
            ranked.append(self.ingest(item))
        ranked.sort(key=lambda row: row["ranking"]["score"], reverse=True)
        hot = sum(1 for row in ranked if row["ranking"]["band"] == "hot")
        return {
            "count": len(ranked),
            "hot": hot,
            "leads": ranked,
        }
