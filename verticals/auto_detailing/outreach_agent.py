"""Email / SMS sequences: follow-up, membership nudges, review asks, winbacks."""

from __future__ import annotations

from typing import Any, Dict, List

from .protocols import MEMBERSHIP_NUDGE_DAYS, MEMBERSHIPS, PACKAGES, UPSELL_GRAPH
from .schemas import OutreachRequest


class DetailingOutreachAgent:
    def sequence(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        req = OutreachRequest(
            kind=payload.get("kind") or payload.get("input") or "quote_followup",
            name=payload.get("name") or "there",
            package_id=payload.get("package_id"),
            last_service_days=payload.get("last_service_days"),
            channel=payload.get("channel", "email"),
        )
        pkg_label = (
            str(PACKAGES[req.package_id]["label"])
            if req.package_id in PACKAGES
            else "detailing"
        )
        next_up = UPSELL_GRAPH.get(req.package_id or "", "ceramic")
        next_label = str(PACKAGES.get(next_up, {}).get("label", "ceramic coating"))

        if req.kind == "quote_followup":
            steps: List[Dict[str, Any]] = [
                {
                    "day": 0,
                    "channel": req.channel,
                    "subject": f"{req.name}, your {pkg_label} quote is still live",
                    "body": (
                        f"Still holding the math for your {pkg_label}. "
                        "Bays move — grab the slot if you want this week."
                    ),
                },
                {
                    "day": 2,
                    "channel": "sms",
                    "body": f"Northline: 2-day follow-up on {pkg_label}. Booking link in the last email.",
                },
                {
                    "day": 5,
                    "channel": "email",
                    "subject": "We'll release the hold",
                    "body": "If timing was the issue, the membership wash is the easier on-ramp.",
                },
            ]
        elif req.kind == "membership_nudge":
            days = req.last_service_days or MEMBERSHIP_NUDGE_DAYS[0]
            plan = MEMBERSHIPS["monthly_gloss"]
            steps = [
                {
                    "day": 0,
                    "channel": req.channel,
                    "subject": f"{days} days since the last wash",
                    "body": (
                        f"{req.name}, clear coat doesn't wait. "
                        f"{plan['label']} is ${plan['price']}/mo — same bay, no re-booking tax."
                    ),
                }
            ]
        elif req.kind == "review_ask":
            steps = [
                {
                    "day": 0,
                    "channel": "sms",
                    "body": (
                        f"Thanks {req.name}. If the {pkg_label} looks like the photos, "
                        "a Google review takes 20 seconds and actually moves our rank."
                    ),
                }
            ]
        elif req.kind == "winback":
            steps = [
                {
                    "day": 0,
                    "channel": "email",
                    "subject": "Your ceramic isn't immortal",
                    "body": (
                        f"It's been a while. The honest next step is a {next_label} "
                        "inspection wash — we'll tell you if the coating still beads."
                    ),
                }
            ]
        else:
            steps = [
                {
                    "day": 0,
                    "channel": req.channel,
                    "subject": f"Next up: {next_label}",
                    "body": f"Most {pkg_label} clients add {next_label} within 90 days. Photo quote is open.",
                }
            ]
        return {
            "kind": req.kind,
            "contact": req.name,
            "channel": req.channel,
            "steps": steps,
            "stop_on_book": True,
            "compliance": {"opt_out": True, "quiet_hours": True},
        }
