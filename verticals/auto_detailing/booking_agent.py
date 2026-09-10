"""Quote math, Calendly handoff, weather holds, and deposit rules."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import urlencode
from uuid import uuid4

from .config import DEFAULT_SHOP
from .protocols import (
    PACKAGES,
    PHOTO_QUOTE_FAMILIES,
    REMINDER_OFFSETS_HOURS,
    VEHICLE_SIZE_MULTIPLIER,
    WEATHER_HOLD_PRECIP_PCT,
    deposit_for,
    vehicle_size_from_body,
)
from .schemas import BookingHandoffRequest, QuoteRequest, Vehicle

KNOWN_SIZES = frozenset(VEHICLE_SIZE_MULTIPLIER.keys())


def resolve_size(vehicle: Vehicle) -> Dict[str, Any]:
    """Return vehicle size plus whether we had to assume sedan."""
    raw = (vehicle.size or "").strip().lower() or None
    if raw and raw in KNOWN_SIZES:
        return {"size": raw, "size_assumed": False, "size_requested": raw}
    inferred = vehicle_size_from_body(vehicle.body_style, vehicle.make)
    if raw and raw not in KNOWN_SIZES:
        return {
            "size": "sedan",
            "size_assumed": True,
            "size_requested": raw,
            "size_note": f"Unknown size '{raw}' — priced as sedan (1.00x).",
        }
    return {"size": inferred, "size_assumed": raw is None, "size_requested": raw}


def quote_matrix(
    packages: Optional[Dict[str, Dict[str, Any]]] = None,
    sizes: Optional[Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    """Full package × size price grid with coating deposit math."""
    packages = packages or PACKAGES
    sizes = sizes or VEHICLE_SIZE_MULTIPLIER
    rows: List[Dict[str, Any]] = []
    for pkg_id, pkg in packages.items():
        rate = deposit_for(pkg_id)
        cells: Dict[str, Any] = {}
        for size, multiplier in sizes.items():
            total = round(float(pkg["price"]) * float(multiplier), 2)
            deposit = round(total * rate, 2)
            cells[size] = {
                "multiplier": multiplier,
                "total": total,
                "deposit": deposit,
                "balance_due_at_bay": round(total - deposit, 2),
            }
        rows.append(
            {
                "package_id": pkg_id,
                "label": pkg["label"],
                "base_price": float(pkg["price"]),
                "duration_min": pkg["duration_min"],
                "deposit_rate": rate,
                "family": pkg["family"],
                "cells": cells,
            }
        )
    return rows


def _quote(req: QuoteRequest, packages: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    catalog = packages or PACKAGES
    pkg = catalog.get(req.package_id)
    if not pkg:
        raise ValueError(f"Unknown package: {req.package_id}")
    size_info = resolve_size(req.vehicle)
    size = size_info["size"]
    multiplier = float(VEHICLE_SIZE_MULTIPLIER.get(size, 1.0))
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
        "size_assumed": bool(size_info.get("size_assumed")),
        "size_requested": size_info.get("size_requested"),
        "size_note": size_info.get("size_note"),
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


def build_calendly_url(
    *,
    handle: str,
    event: str,
    name: Optional[str] = None,
    email: Optional[str] = None,
    vehicle: Optional[Vehicle] = None,
    package_label: Optional[str] = None,
    package_id: Optional[str] = None,
) -> str:
    params = {
        "name": name or "",
        "email": email or "",
        "a1": " ".join(
            str(p)
            for p in (
                (vehicle.year if vehicle else None),
                (vehicle.make if vehicle else None),
                (vehicle.model if vehicle else None),
            )
            if p
        ),
        "a2": package_label or "",
        "utm_source": "chroma",
        "utm_medium": "agent",
        "utm_campaign": package_id or "",
    }
    return f"https://calendly.com/{handle}/{event}?{urlencode(params)}"


class DetailingBookingAgent:
    def quote(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        vehicle = Vehicle.model_validate(payload.get("vehicle") or {})
        req = QuoteRequest(
            package_id=payload.get("package_id") or payload.get("input") or "full_detail",
            vehicle=vehicle,
            addons=list(payload.get("addons") or []),
            mobile=bool(payload.get("mobile", False)),
        )
        catalog = payload.get("packages") or PACKAGES
        if req.package_id not in catalog:
            req = req.model_copy(update={"package_id": "full_detail"})
        return _quote(req, packages=catalog)

    def calendly_handoff(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        vehicle = Vehicle.model_validate(payload.get("vehicle") or {})
        req = BookingHandoffRequest(
            lead_id=payload.get("lead_id"),
            package_id=payload.get("package_id", "full_detail"),
            vehicle=vehicle,
            name=payload.get("name"),
            email=payload.get("email"),
            phone=payload.get("phone"),
            calendly_handle=payload.get("calendly_handle") or DEFAULT_SHOP["calendly_handle"],
            preferred_slot=payload.get("preferred_slot"),
            weather_precip_pct=payload.get("weather_precip_pct"),
        )
        catalog = payload.get("packages") or PACKAGES
        if req.package_id not in catalog:
            req = req.model_copy(update={"package_id": "full_detail"})
        quote = _quote(
            QuoteRequest(
                package_id=req.package_id,
                vehicle=req.vehicle,
                mobile=bool(payload.get("mobile")),
            ),
            packages=catalog,
        )
        pkg = catalog[req.package_id]
        event = str(pkg["calendly_event"])
        url = build_calendly_url(
            handle=req.calendly_handle,
            event=event,
            name=req.name,
            email=req.email,
            vehicle=req.vehicle,
            package_label=str(pkg["label"]),
            package_id=req.package_id,
        )
        precip = req.weather_precip_pct if req.weather_precip_pct is not None else 0
        weather_hold = bool(pkg["exterior"]) and precip >= WEATHER_HOLD_PRECIP_PCT
        booking_id = f"BK-{uuid4().hex[:8].upper()}"
        reminders = [
            {"offset_hours": hours, "label": f"T-{hours}h"}
            for hours in REMINDER_OFFSETS_HOURS
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
            "reminders": reminders,
            "deposit_required": quote["deposit"] > 0,
            "self_serve": True,
            "preferred_slot": req.preferred_slot,
            "status": "weather_hold" if weather_hold else "link_ready",
        }
