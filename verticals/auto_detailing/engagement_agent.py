"""Landing popup + after-hours engagement that qualifies and books."""

from __future__ import annotations

from typing import Any, Dict, List

from .booking_agent import DetailingBookingAgent
from .protocols import FIRST_RESPONSE_SECONDS, PACKAGES


_STEPS = ("greet", "service", "vehicle", "quote", "book")


class DetailingEngagementAgent:
    """Site visitor agent — popup, missed-call text-back, photo-quote chat."""

    def __init__(self) -> None:
        self._booking = DetailingBookingAgent()

    def engage(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        message = (payload.get("visitor_message") or payload.get("input") or "").strip()
        page = payload.get("page", "/")
        step = payload.get("step") or "greet"
        history: List[Dict[str, str]] = list(payload.get("history") or [])

        if not message:
            return {
                "step": "greet",
                "prompt": (
                    "Need the car camera-ready or just honest-clean? "
                    "Tell me the year, make, and what you want done — I'll price it and hand you the calendar."
                ),
                "quick_replies": ["Wash", "Interior", "Ceramic", "PPF"],
                "popup_delay_ms": 4000,
                "exit_intent": True,
                "sla_seconds": FIRST_RESPONSE_SECONDS,
                "page": page,
            }

        lower = message.lower()
        package_id = payload.get("package_id")
        for key, pkg in PACKAGES.items():
            label = str(pkg["label"]).lower()
            if key.replace("_", " ") in lower or label.split()[0].lower() in lower:
                package_id = key
                break
        if "ceramic" in lower:
            package_id = "ceramic"
        elif "ppf" in lower or "film" in lower:
            package_id = "ppf"
        elif "interior" in lower:
            package_id = "interior"
        elif "correct" in lower:
            package_id = "paint_correction"
        elif "wash" in lower:
            package_id = "maintenance_wash"

        if package_id:
            quote = self._booking.quote(
                {
                    "package_id": package_id,
                    "vehicle": payload.get("vehicle") or {},
                    "mobile": "mobile" in lower,
                }
            )
            handoff = self._booking.calendly_handoff(
                {
                    "package_id": package_id,
                    "vehicle": payload.get("vehicle") or {},
                    "name": payload.get("known_name"),
                    "email": payload.get("email"),
                    "phone": payload.get("phone"),
                    "calendly_handle": payload.get("calendly_handle", "northline-detail"),
                }
            )
            return {
                "step": "book",
                "package_id": package_id,
                "reply": (
                    f"{quote['label']} for a {quote['vehicle_size']} lands at ${quote['total']:.0f}. "
                    + (
                        f"${quote['deposit']:.0f} deposit holds the bay. "
                        if quote["deposit"]
                        else "No deposit on this package. "
                    )
                    + "Grab the next open slot — no one has to text you back."
                ),
                "quote": quote,
                "calendly_url": handoff["calendly_url"],
                "cta": "Self-book this bay",
                "history": history,
            }

        return {
            "step": step if step != "greet" else "service",
            "reply": (
                "Got it. Wash, interior, ceramic, or film? "
                "Add year/make if you have it — size changes the number."
            ),
            "quick_replies": ["Maintenance wash", "Interior revival", "Ceramic coating", "PPF consult"],
            "history": history + [{"role": "visitor", "text": message}],
        }

    def missed_call(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        phone = payload.get("phone") or "unknown"
        return {
            "channel": "sms",
            "to": phone,
            "body": (
                "Northline Detail — we missed you. Reply WASH, INTERIOR, CERAMIC, or PPF "
                "and we'll send a quote plus a booking link. Usually faster than a callback."
            ),
            "sent": True,
            "sla_seconds": 30,
        }
