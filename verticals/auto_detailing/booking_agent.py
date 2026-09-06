"""Quote math, Calendly handoff, weather holds, and deposit rules."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict
from urllib.parse import urlencode
from uuid import uuid4

from .protocols import (
    PACKAGES,
    PHOTO_QUOTE_FAMILIES,
    WEATHER_HOLD_PRECIP_PCT,
    deposit_for,
    vehicle_size_from_body,
)
from .schemas import BookingHandoffRequest, QuoteRequest, Vehicle


def _quote(req: QuoteRequest) -> Dict[str, Any]:
    pkg = PACKAGES.get(req.package_id)
    if not pkg:
        raise ValueError(f"Unknown package: {req.package_id}")
    size = req.vehicle.size or vehicle_size_from_body(req.vehicle.body_style, req.vehicle.make)
    multiplier = {
        "compact": 0.90,
        "sedan": 1.00,
        "coupe": 1.00,
        "suv": 1.20,
        "crossover": 1.12,
        "truck": 1.25,
        "van": 1.28,
        "exotic": 1.45,
        "oversized": 1.35,
    }.get(size, 1.0)
    mobile_fee = 45 if req.mobile else 0
    addon_fees = {"engine_bay": 65, "pet_hair": 85, "headlight": 90, "ozone": 70}
    addons_total = sum(addon_fees.get(a, 40) for a in req.addons)
    base = float(pkg["price"]) * multiplier
    total = round(base + addons_total + mobile_fee, 2)
    deposit_rate = deposit_for(req.package_id)
    deposit = round(total * deposit_rate, 2)
    family = str(pkg["family"])
    return {
        "package_id": req.package_id,
        "label": pkg["label"],
        "vehicle_size": size,
        "duration_min": pkg["duration_min"],
        "base_price": float(pkg["price"]),
        "size_multiplier": multiplier,
        "addons_total": addons_total,
        "mobile_fee": mobile_fee,
        "total": total,
        "deposit": deposit,
        "deposit_rate": deposit_rate,
        "balance_due_at_bay": round(total - deposit, 2),
        "photo_quote_recommended": family in PHOTO_QUOTE_FAMILIES,
        "exterior": bool(pkg["exterior"]),
    }


class DetailingBookingAgent:
    def quote(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        vehicle = Vehicle.model_validate(payload.get("vehicle") or {})
        req = QuoteRequest(
            package_id=payload.get("package_id") or payload.get("input") or "full_detail",
            vehicle=vehicle,
            addons=list(payload.get("addons") or []),
            mobile=bool(payload.get("mobile", False)),
        )
        if req.package_id not in PACKAGES:
            req = req.model_copy(update={"package_id": "full_detail"})
        return _quote(req)

    def calendly_handoff(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        vehicle = Vehicle.model_validate(payload.get("vehicle") or {})
        req = BookingHandoffRequest(
            lead_id=payload.get("lead_id"),
            package_id=payload.get("package_id", "full_detail"),
            vehicle=vehicle,
            name=payload.get("name"),
            email=payload.get("email"),
            phone=payload.get("phone"),
            calendly_handle=payload.get("calendly_handle", "northline-detail"),
            preferred_slot=payload.get("preferred_slot"),
            weather_precip_pct=payload.get("weather_precip_pct"),
        )
        if req.package_id not in PACKAGES:
            req = req.model_copy(update={"package_id": "full_detail"})
        quote = _quote(
            QuoteRequest(package_id=req.package_id, vehicle=req.vehicle, mobile=bool(payload.get("mobile")))
        )
        pkg = PACKAGES[req.package_id]
        event = str(pkg["calendly_event"])
        params = {
            "name": req.name or "",
            "email": req.email or "",
            "a1": " ".join(
                str(p)
                for p in (req.vehicle.year, req.vehicle.make, req.vehicle.model)
                if p
            ),
            "a2": str(pkg["label"]),
            "utm_source": "chroma",
            "utm_medium": "agent",
            "utm_campaign": req.package_id,
        }
        url = f"https://calendly.com/{req.calendly_handle}/{event}?{urlencode(params)}"
        precip = req.weather_precip_pct if req.weather_precip_pct is not None else 0
        weather_hold = bool(pkg["exterior"]) and precip >= WEATHER_HOLD_PRECIP_PCT
        booking_id = f"BK-{uuid4().hex[:8].upper()}"
        reminders = [
            (datetime.now(timezone.utc) + timedelta(hours=-24)).isoformat(),
            (datetime.now(timezone.utc) + timedelta(hours=-2)).isoformat(),
        ]
        return {
            "booking_id": booking_id,
            "lead_id": req.lead_id,
            "calendly_url": url,
            "event": event,
            "quote": quote,
            "weather_hold": weather_hold,
            "weather_reason": (
                f"Exterior work paused — precipitation forecast {precip}%"
                if weather_hold
                else None
            ),
            "reminders": ["T-24h", "T-2h"],
            "deposit_required": quote["deposit"] > 0,
            "self_serve": True,
        }
