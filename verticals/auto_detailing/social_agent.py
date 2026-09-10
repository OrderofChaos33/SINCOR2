"""Scheduled posting and proactive social engagement that steers to booking."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from .protocols import PACKAGES, SEASONAL_CAMPAIGNS


_PILLARS = ("before_after", "education", "offer", "bay_life", "review")


class DetailingSocialAgent:
    def schedule(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        platforms = list(payload.get("platforms") or ["instagram", "facebook", "gbp"])
        cadence = int(payload.get("cadence_per_week") or 5)
        city = payload.get("city", "Dubuque")
        start = datetime.now(timezone.utc).replace(hour=15, minute=0, second=0, microsecond=0)
        posts: List[Dict[str, Any]] = []
        for i in range(cadence):
            pillar = _PILLARS[i % len(_PILLARS)]
            pkg_id = list(PACKAGES.keys())[i % len(PACKAGES)]
            pkg = PACKAGES[pkg_id]
            caption = {
                "before_after": (
                    f"Same {city} car. Same sun. Different clear coat. "
                    f"{pkg['label']} — book the bay from the link in bio."
                ),
                "education": (
                    "If a wash mitt squeaks, you're grinding grit into the clear. "
                    "Two-bucket or don't bother. We built the membership around that rule."
                ),
                "offer": (
                    f"{city}: {pkg['label']} this week. Self-book, no text tag. "
                    "Coatings take a deposit so the date actually holds."
                ),
                "bay_life": (
                    "Bay two, lights down, wet floor. This is the part Instagram usually skips — "
                    "the hour after the polish when the panel either tells the truth or it doesn't."
                ),
                "review": (
                    f"Five stars don't detail the car. Showing up for the 30-day wash does. "
                    f"{city} members get the bay that keeps ceramic honest."
                ),
            }[pillar]
            when = start + timedelta(days=i)
            posts.append(
                {
                    "id": f"POST-{i+1:02d}",
                    "at": when.isoformat(),
                    "platforms": platforms if pillar != "review" else ["gbp", "facebook"],
                    "pillar": pillar,
                    "caption": caption,
                    "cta": "Book from the link — no DM required.",
                    "package_id": pkg_id,
                }
            )
        queued_ids: List[str] = []
        if payload.get("enqueue"):
            from .send_gate import enqueue

            store = payload.get("store")
            for post in posts:
                item = enqueue(
                    channel="social",
                    kind=f"social_{post['pillar']}",
                    body=post["caption"],
                    subject=",".join(post["platforms"]),
                    lead_id=payload.get("lead_id"),
                    metadata=post,
                    store=store,
                )
                queued_ids.append(item["id"])
        return {
            "cadence_per_week": cadence,
            "platforms": platforms,
            "posts": posts,
            "queued_ids": queued_ids,
            "engagement_policy": {
                "reply_to_every_comment": True,
                "steer_to_booking": True,
                "marketplace_autodm": True,
                "quiet_hours": "21:00-07:00 local",
            },
        }

    def engage(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        inbound = (payload.get("comment") or payload.get("input") or payload.get("message") or "").lower()
        platform = payload.get("platform", "instagram")
        if any(k in inbound for k in ("price", "how much", "cost", "$")):
            reply = (
                "Pricing is by vehicle size and package — 60-second quote in the booking link. "
                "Coatings need a photo for a real number."
            )
            intent = "quote"
        elif any(k in inbound for k in ("book", "available", "this week", "saturday")):
            reply = "Open bays are on the calendar. Pick the package, drop the vehicle, done."
            intent = "book"
        elif any(k in inbound for k in ("mobile", "come to me", "house")):
            reply = (
                "We mobile washes and interiors inside the radius when weather holds. "
                "Coatings and PPF stay in the shop bays."
            )
            intent = "qualify"
        else:
            reply = (
                "Appreciate you. If you want this finish on yours, the booking link is the fastest path — "
                "we don't make people DM for a time."
            )
            intent = "nurture"
        queued_id = None
        if payload.get("enqueue"):
            from .send_gate import enqueue

            item = enqueue(
                channel="social",
                kind="comment_reply",
                body=reply,
                to=platform,
                lead_id=payload.get("lead_id"),
                store=payload.get("store"),
            )
            queued_id = item["id"]
        return {
            "platform": platform,
            "intent": intent,
            "reply": reply,
            "cta_url_hint": "/book",
            "queued_id": queued_id,
            "escalate_to_human": intent == "book" and "fleet" in inbound,
        }

    def seasonal(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        month = int(payload.get("month") or datetime.now(timezone.utc).month)
        active = [c for c in SEASONAL_CAMPAIGNS if str(month) in c["months"].split(",")]
        return {"month": month, "campaigns": active}
