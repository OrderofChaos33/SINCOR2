"""Shared protocols for the auto detailing vertical.

These constants encode how high-performing detail shops actually autopilot:
speed-to-lead, photo quotes, deposits on coatings, review compounding,
membership cadence, seasonal offers, and self-serve booking.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, List, Tuple

# First-response SLA that wins comparison shoppers.
FIRST_RESPONSE_SECONDS = 90
QUALIFIED_HANDOFF_SECONDS = 180

# Source intent weights (0–1). Search and missed-call beat social.
SOURCE_WEIGHT: Dict[str, float] = {
    "missed_call": 0.96,
    "google": 0.95,
    "gbp": 0.92,
    "referral": 0.90,
    "website": 0.88,
    "google_ads": 0.85,
    "facebook_marketplace": 0.78,
    "yelp": 0.74,
    "instagram": 0.62,
    "tiktok": 0.55,
    "walk_in": 0.70,
}

# Message keywords that lift intent.
INTENT_KEYWORDS: Dict[str, float] = {
    "ceramic": 0.28,
    "coating": 0.26,
    "ppf": 0.30,
    "paint protection": 0.30,
    "paint correction": 0.24,
    "swirl": 0.16,
    "oxidation": 0.18,
    "interior": 0.12,
    "pet hair": 0.14,
    "detail": 0.10,
    "wash": 0.06,
    "salt": 0.14,
    "pollen": 0.10,
    "wedding": 0.18,
    "show car": 0.22,
    "exotic": 0.20,
}

LUXURY_MAKES: FrozenSet[str] = frozenset(
    {
        "mercedes",
        "mercedes-benz",
        "bmw",
        "audi",
        "porsche",
        "tesla",
        "lexus",
        "range rover",
        "land rover",
        "cadillac",
        "genesis",
        "jaguar",
        "maserati",
        "ferrari",
        "lamborghini",
        "mclaren",
        "bentley",
        "rolls-royce",
        "aston martin",
        "rivian",
        "lucid",
    }
)

VEHICLE_SIZE_MULTIPLIER: Dict[str, float] = {
    "compact": 0.90,
    "sedan": 1.00,
    "coupe": 1.00,
    "suv": 1.20,
    "crossover": 1.12,
    "truck": 1.25,
    "van": 1.28,
    "exotic": 1.45,
    "oversized": 1.35,
}

# Deposit fractions by package family.
DEPOSIT_RATE: Dict[str, float] = {
    "express_wash": 0.00,
    "maintenance_wash": 0.00,
    "interior": 0.20,
    "full_detail": 0.25,
    "paint_correction": 0.40,
    "ceramic": 0.50,
    "ppf": 0.50,
    "membership": 0.00,
}

PACKAGES: Dict[str, Dict[str, object]] = {
    "express_wash": {
        "label": "Express Wash",
        "duration_min": 45,
        "price": 79,
        "family": "express_wash",
        "exterior": True,
        "calendly_event": "express-wash",
    },
    "maintenance_wash": {
        "label": "Maintenance Wash",
        "duration_min": 75,
        "price": 129,
        "family": "maintenance_wash",
        "exterior": True,
        "calendly_event": "maintenance-wash",
    },
    "interior": {
        "label": "Interior Revival",
        "duration_min": 180,
        "price": 249,
        "family": "interior",
        "exterior": False,
        "calendly_event": "interior-revival",
    },
    "full_detail": {
        "label": "Signature Detail",
        "duration_min": 300,
        "price": 449,
        "family": "full_detail",
        "exterior": True,
        "calendly_event": "signature-detail",
    },
    "paint_correction": {
        "label": "Paint Correction",
        "duration_min": 480,
        "price": 899,
        "family": "paint_correction",
        "exterior": True,
        "calendly_event": "paint-correction",
    },
    "ceramic": {
        "label": "Ceramic Coating",
        "duration_min": 720,
        "price": 1499,
        "family": "ceramic",
        "exterior": True,
        "calendly_event": "ceramic-coating",
    },
    "ppf": {
        "label": "Paint Protection Film",
        "duration_min": 960,
        "price": 2899,
        "family": "ppf",
        "exterior": True,
        "calendly_event": "ppf-consult",
    },
}

MEMBERSHIPS: Dict[str, Dict[str, object]] = {
    "monthly_gloss": {"label": "Monthly Gloss", "price": 149, "interval_days": 30},
    "fleet_care": {"label": "Fleet Care", "price": 99, "interval_days": 21, "min_vehicles": 4},
    "ceramic_warranty": {"label": "Ceramic Warranty Wash", "price": 89, "interval_days": 60},
}

SEASONAL_CAMPAIGNS: List[Dict[str, str]] = [
    {
        "id": "winter_salt",
        "months": "12,1,2,3",
        "headline": "Salt is etching your clear coat",
        "offer": "Undercarriage + decontamination wash — book before the next thaw.",
        "cta": "Book salt removal",
        "package": "maintenance_wash",
    },
    {
        "id": "spring_pollen",
        "months": "4,5",
        "headline": "Pollen is sandpaper",
        "offer": "Safe decon wash + ceramic booster before the yellow dust sets in.",
        "cta": "Clear the pollen",
        "package": "full_detail",
    },
    {
        "id": "summer_ceramic",
        "months": "6,7,8",
        "headline": "UV is bleaching unprotected paint",
        "offer": "Ceramic coating with 50% deposit locks the date. Warranty included.",
        "cta": "Reserve a coating bay",
        "package": "ceramic",
    },
    {
        "id": "fall_ppf",
        "months": "9,10,11",
        "headline": "Rock chips happen on the first gravel road",
        "offer": "Front-end PPF consult — photo quote in the chat, no shop visit required.",
        "cta": "Get a film quote",
        "package": "ppf",
    },
]

# Wash → interior → ceramic → PPF upsell graph.
UPSELL_GRAPH: Dict[str, str] = {
    "express_wash": "maintenance_wash",
    "maintenance_wash": "interior",
    "interior": "full_detail",
    "full_detail": "ceramic",
    "paint_correction": "ceramic",
    "ceramic": "ppf",
}

REMINDER_OFFSETS_HOURS: Tuple[int, ...] = (24, 2)
REVIEW_REQUEST_HOURS_AFTER_JOB = 2
MEMBERSHIP_NUDGE_DAYS = (30, 60, 90)
WEATHER_HOLD_PRECIP_PCT = 40
NO_SHOW_RECOVERY_HOURS = 1
PHOTO_QUOTE_FAMILIES: FrozenSet[str] = frozenset({"ceramic", "ppf", "paint_correction", "interior"})

LOCAL_SEO_KEYWORDS: Tuple[str, ...] = (
    "auto detailing near me",
    "ceramic coating",
    "paint correction",
    "mobile detailing",
    "interior car detailing",
    "PPF paint protection film",
    "car wash membership",
    "salt removal car wash",
)

GBP_CATEGORIES: Tuple[str, ...] = (
    "Auto detailing service",
    "Car wash",
    "Vehicle wrapping service",
)

AEO_FAQS: List[Tuple[str, str]] = [
    (
        "How long does a ceramic coating take?",
        "A single-stage coating is a full-day bay. Multi-coat systems run 1–2 days including paint correction.",
    ),
    (
        "Do I need to drop the car off?",
        "Yes for coatings and PPF. Washes and interiors can be mobile within the service radius if weather allows.",
    ),
    (
        "How do I book?",
        "Pick a package, confirm vehicle size, and self-book the next open bay. Coatings require a deposit.",
    ),
]


def vehicle_size_from_body(body_style: str | None, make: str | None) -> str:
    body = (body_style or "").lower()
    make_l = (make or "").lower()
    if make_l in {"ferrari", "lamborghini", "mclaren", "porsche"} or "exotic" in body:
        return "exotic"
    if any(k in body for k in ("truck", "pickup", "f-150", "silverado")):
        return "truck"
    if "van" in body:
        return "van"
    if any(k in body for k in ("suv", "crossover", "4runner", "tahoe", "x5")):
        return "suv"
    if "compact" in body or "hatch" in body:
        return "compact"
    return "sedan"


def deposit_for(package_id: str) -> float:
    pkg = PACKAGES.get(package_id, {})
    family = str(pkg.get("family", package_id))
    return float(DEPOSIT_RATE.get(family, 0.25))
