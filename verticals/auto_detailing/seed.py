"""Demo seed — Clinton Auto Detailing, Clinton IA. 10 leads, 3 quotes, 2 bookings."""

from __future__ import annotations

from typing import Any, Dict, Optional

from .booking_agent import DetailingBookingAgent
from .config import DEFAULT_SHOP, default_packages
from .lead_agent import DetailingLeadAgent
from .send_gate import enqueue
from .store import ChromaStore, get_store

# Fictional locals in Court's real service area. Not real customers.
LEADS = [
    {
        "lead_id": "LD-AVA001",
        "source": "google",
        "name": "Miles Brennan",
        "email": "miles.brennan@example.com",
        "phone": "563-555-2101",
        "message": "Need ceramic coating and paint correction on my Tesla Model X",
        "vehicle": {
            "year": 2023,
            "make": "Tesla",
            "model": "Model X",
            "condition": "daily",
            "body_style": "suv",
        },
        "photo_urls": ["https://cdn.example/modelx-hood.jpg"],
        "stage_hint": "ready_to_book",
    },
    {
        "lead_id": "LD-MAR002",
        "source": "website",
        "name": "Dana Briggs",
        "email": "dana.briggs@example.com",
        "phone": "563-555-2102",
        "message": "Interior revival, English bulldog hair all over the seats",
        "vehicle": {
            "year": 2019,
            "make": "Toyota",
            "model": "Highlander",
            "body_style": "suv",
            "condition": "dirty",
        },
        "stage_hint": "quoted",
    },
    {
        "lead_id": "LD-JEN003",
        "source": "instagram",
        "name": "Jen Ortiz",
        "email": "jen.ortiz@example.com",
        "message": "How much for a wash this Saturday?",
        "vehicle": {"year": 2018, "make": "Honda", "model": "Civic", "size": "compact"},
        "stage_hint": "nurture",
    },
    {
        "lead_id": "LD-RAY004",
        "source": "missed_call",
        "name": "Ray Dietrich",
        "phone": "815-555-2104",
        "message": "PPF consult for a new F-150 — I'm in Fulton",
        "vehicle": {"year": 2025, "make": "Ford", "model": "F-150", "body_style": "truck"},
        "photo_urls": ["https://cdn.example/f150-front.jpg"],
        "stage_hint": "quoted",
    },
    {
        "lead_id": "LD-SAM005",
        "source": "facebook_marketplace",
        "name": "Wes Harlan",
        "email": "wes.harlan@example.com",
        "message": "Full detail on a Mustang GT before a show",
        "vehicle": {
            "year": 2018,
            "make": "Ford",
            "model": "Mustang GT",
            "size": "coupe",
            "condition": "clean",
        },
        "stage_hint": "booked",
    },
    {
        "lead_id": "LD-PAT006",
        "source": "gbp",
        "name": "Pat Nguyen",
        "email": "pat.nguyen@example.com",
        "phone": "563-555-2106",
        "message": "Maintenance wash membership? Camanche, salt is already on it",
        "vehicle": {"year": 2020, "make": "Chevy", "model": "Equinox", "body_style": "suv"},
        "stage_hint": "nurture",
    },
    {
        "lead_id": "LD-LEE007",
        "source": "referral",
        "name": "Lee Brooks",
        "email": "lee.brooks@example.com",
        "message": "Ceramic coating on a neglected daily driver BMW",
        "vehicle": {
            "year": 2016,
            "make": "BMW",
            "model": "328i",
            "condition": "neglected",
            "size": "sedan",
        },
        "photo_urls": ["https://cdn.example/328-swirls.jpg"],
        "stage_hint": "ready_to_book",
    },
    {
        "lead_id": "LD-KIM008",
        "source": "google_ads",
        "name": "Kim Alvarez",
        "email": "kim.alvarez@example.com",
        "message": "Paint correction, swirls on the hood",
        "vehicle": {"year": 2014, "make": "Chevrolet", "model": "Camaro", "size": "coupe"},
        "stage_hint": "quoted",
    },
    {
        "lead_id": "LD-TOM009",
        "source": "yelp",
        "name": "Mara Ellison",
        "phone": "563-555-2109",
        "message": "Interior only — kids wrecked the back seats",
        "vehicle": {"year": 2017, "make": "Chrysler", "model": "Pacifica", "body_style": "van"},
        "stage_hint": "new",
    },
    {
        "lead_id": "LD-WIN010",
        "source": "website",
        "name": "Chris Vale",
        "email": "chris.vale@example.com",
        "message": "You coated my car two years ago, does it still bead?",
        "vehicle": {"year": 2019, "make": "Lexus", "model": "GX", "body_style": "suv"},
        "stage_hint": "nurture",
    },
]


def seed(store: Optional[ChromaStore] = None, reset: bool = True) -> Dict[str, Any]:
    store = store or get_store()
    if reset:
        store.wipe()

    store.save_settings({**DEFAULT_SHOP, "packages": default_packages()})

    leads_agent = DetailingLeadAgent()
    booking = DetailingBookingAgent()

    for spec in LEADS:
        payload = {k: v for k, v in spec.items() if k != "stage_hint"}
        lead = leads_agent.ingest(payload)
        lead["pipeline_stage"] = spec["stage_hint"]
        store.upsert_lead(lead)
        store.add_event(lead["lead_id"], "ingested", f"{lead['source']} · {lead['ranking']['band']}")
        if spec["stage_hint"] in {"quoted", "ready_to_book", "booked"}:
            store.add_event(lead["lead_id"], "quoted", spec["stage_hint"])
        if spec["stage_hint"] == "booked":
            store.add_event(lead["lead_id"], "booked", "Thu 10:00 AM")
        if spec["stage_hint"] == "ready_to_book":
            store.add_event(lead["lead_id"], "booking_link", "Calendly handoff ready")

    q1 = store.save_quote(
        booking.quote(
            {
                "package_id": "ceramic",
                "vehicle": {"year": 2023, "make": "Tesla", "body_style": "suv"},
            }
        ),
        lead_id="LD-AVA001",
    )
    store.mark_quote_sent(q1["quote_id"], True)
    store.add_event("LD-AVA001", "quoted", f"Ceramic · ${q1['total']:.0f}")

    q2 = store.save_quote(
        booking.quote(
            {
                "package_id": "interior",
                "vehicle": {"year": 2019, "make": "Toyota", "body_style": "suv"},
                "addons": ["pet_hair"],
            }
        ),
        lead_id="LD-MAR002",
    )
    store.add_event("LD-MAR002", "quoted", f"Interior · ${q2['total']:.0f}")

    q3 = store.save_quote(
        booking.quote(
            {"package_id": "express_wash", "vehicle": {"size": "compact"}}
        ),
        lead_id="LD-JEN003",
    )
    store.add_event("LD-JEN003", "quoted", f"Express wash · ${q3['total']:.0f} · $0 deposit")

    booked = booking.calendly_handoff(
        {
            "lead_id": "LD-SAM005",
            "package_id": "full_detail",
            "name": "Wes Harlan",
            "email": "wes.harlan@example.com",
            "vehicle": {"year": 2018, "make": "Ford", "model": "Mustang GT", "size": "coupe"},
            "calendly_handle": DEFAULT_SHOP["calendly_handle"],
        }
    )
    booked["status"] = "booked"
    booked["slot"] = "Thu 10:00 AM · Signature Detail"
    booked["quote_id"] = q1["quote_id"]
    store.save_booking(booked)

    link = booking.calendly_handoff(
        {
            "lead_id": "LD-AVA001",
            "package_id": "ceramic",
            "name": "Miles Brennan",
            "email": "miles.brennan@example.com",
            "vehicle": {"year": 2023, "make": "Tesla", "model": "Model X", "body_style": "suv"},
            "calendly_handle": DEFAULT_SHOP["calendly_handle"],
        }
    )
    link["status"] = "link_ready"
    link["quote_id"] = q1["quote_id"]
    store.save_booking(link)
    store.add_event("LD-AVA001", "booking_link", link["calendly_url"])

    enqueue(
        channel="email",
        kind="quote_followup",
        body=(
            "Miles — ceramic on the Model X is quoted. Half holds Thursday at 715 Park Pl. "
            "Text (815) 718-8936 if you want the bay."
        ),
        to="miles.brennan@example.com",
        subject="Your ceramic quote is live — Clinton Auto Detailing",
        lead_id="LD-AVA001",
        band="hot",
        store=store,
    )
    enqueue(
        channel="sms",
        kind="missed_call",
        body="Clinton Auto Detailing — we missed you Ray. Reply PPF and we'll send the film quote. Text (815) 718-8936.",
        to="815-555-2104",
        lead_id="LD-RAY004",
        band="hot",
        store=store,
    )
    enqueue(
        channel="email",
        kind="quote_followup",
        body="Dana, interior + pet hair on the Highlander is quoted. Deposit 20%. We can get the bulldog out of those seats this week.",
        to="dana.briggs@example.com",
        subject="Highlander interior quote",
        lead_id="LD-MAR002",
        band="warm",
        store=store,
    )
    enqueue(
        channel="sms",
        kind="review_ask",
        body="Wes — if the Mustang looks like the photos, a Google review takes 20 seconds. Thank you from Clinton Auto Detailing.",
        to="wes.harlan@example.com",
        lead_id="LD-SAM005",
        band="hot",
        store=store,
    )
    enqueue(
        channel="social",
        kind="social_before_after",
        body="Same Clinton car. Same river sun. Different clear coat. Clinton, Fulton, Camanche, Morrison — text (815) 718-8936.",
        subject="instagram,facebook",
        store=store,
    )
    enqueue(
        channel="email",
        kind="winback",
        body="Chris, ceramic isn't immortal. Inspection wash and we'll tell you if it still beads. 715 Park Pl, Clinton.",
        to="chris.vale@example.com",
        subject="Your ceramic isn't immortal",
        lead_id="LD-WIN010",
        band="nurture",
        store=store,
    )

    return store.counts()


def ensure_demo(store: Optional[ChromaStore] = None) -> Dict[str, Any]:
    store = store or get_store()
    counts = store.counts()
    name = store.get_settings().get("shop_name")
    if counts["leads"] == 0 or name in {"Northline Detail", None, ""}:
        return seed(store=store, reset=True)
    return counts
