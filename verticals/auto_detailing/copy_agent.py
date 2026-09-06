"""Ad copy, RSA, Meta, SMS, and landing headlines for detailing shops."""

from __future__ import annotations

from typing import Any, Dict, List

from .protocols import PACKAGES, SEASONAL_CAMPAIGNS
from .schemas import CopyRequest


def _package_label(package_id: str | None) -> str:
    if package_id and package_id in PACKAGES:
        return str(PACKAGES[package_id]["label"])
    return "auto detailing"


class DetailingCopyAgent:
    def generate(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        req = CopyRequest(
            channel=payload.get("channel") or payload.get("input") or "google_rsa",
            package_id=payload.get("package_id"),
            city=payload.get("city", "Dubuque"),
            offer=payload.get("offer"),
            tone=payload.get("tone", "direct"),
        )
        service = _package_label(req.package_id)
        city = req.city
        offer = req.offer or "Same-week bays. Deposit locks ceramic dates."
        season = next((c for c in SEASONAL_CAMPAIGNS if c["package"] == (req.package_id or "")), None)

        if req.channel in {"google_rsa", "google"}:
            headlines: List[str] = [
                f"{service.title()} in {city}",
                f"Book {service} This Week",
                f"{city} Ceramic & Detail",
                "Self-Book in 60 Seconds",
                "Paint Correction Specialists",
                f"{city} Auto Detailing",
                "Deposits Save Your Bay",
                "Mobile & Shop Bays Open",
                "Before/After, Not Filters",
                "Google-Rated Local Shop",
                offer[:30],
                "Skip the Text Tag — Book",
                "Interior Revival From $249",
                "PPF Consult by Photo Quote",
                "Membership Washes, 30-Day",
            ]
            descriptions = [
                f"Northline Detail — {city}. {offer} Choose a package, add your vehicle, book the next open bay.",
                "Ceramic, PPF, paint correction, interiors. Instant quote. Review-backed. Open late for drop-offs.",
            ]
            return {
                "channel": "google_rsa",
                "headlines": headlines[:15],
                "descriptions": descriptions,
                "path1": "detailing",
                "path2": city.lower().replace(" ", "-"),
                "keywords": [
                    f"auto detailing {city}",
                    f"ceramic coating {city}",
                    f"mobile detailing {city}",
                    "paint correction near me",
                ],
            }

        if req.channel in {"meta", "facebook", "instagram"}:
            primary = (
                f"Your {city} neighbors are comparing three detailers and booking the first one that answers. "
                f"We answer in under 90 seconds — and you pick the bay yourself."
            )
            return {
                "channel": "meta",
                "primary_text": primary,
                "headline": f"{service} — {city}",
                "description": offer,
                "cta": "Book Now",
                "placements": ["feed", "stories", "reels"],
            }

        if req.channel == "gbp":
            body = season["offer"] if season else (
                f"{city} {service}. Photo-quote ceramic and PPF. Self-book washes and interiors today."
            )
            return {
                "channel": "gbp",
                "summary": f"{service} in {city}",
                "body": body,
                "cta": season["cta"] if season else "Book online",
            }

        if req.channel == "sms":
            return {
                "channel": "sms",
                "body": (
                    f"Northline: your {service} quote is ready. "
                    f"{offer} Book here — it takes about a minute."
                )[:160],
            }

        if req.channel == "email":
            return {
                "channel": "email",
                "subject": f"{city} {service} — your bay is open",
                "preview": offer,
                "body": (
                    f"Hi — you asked about {service}. "
                    f"We priced it for your vehicle and held the logic (not the bay) until you book.\n\n"
                    f"{offer}\n\n"
                    "Self-book the next slot. Coatings take a deposit so no-shows don't steal the day."
                ),
            }

        return {
            "channel": "landing",
            "headline": f"The {city} shop that books itself.",
            "subhead": f"{service}. Instant quote. Live bays. {offer}",
            "primary_cta": "See open bays",
            "secondary_cta": "Get a photo quote",
        }
