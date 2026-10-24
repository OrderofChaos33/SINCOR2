"""Plan-tier feature gates.

Canon prices (USD): starter 297 / professional 997 / enterprise 2997.
Stripe USDC fallback is the human path when token spot is unavailable.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("sincor.plan_gate")

# ---------------------------------------------------------------------------
# VALID_PLANS — the ONLY plan strings the platform may ever store or grant.
# Enforced at every write path (checkout, webhook fulfillment, set_plan,
# create_user): anything else is rejected fail-closed and logged. Legacy
# alias strings ("admin", "operator", "pro", ...) are NOT plans — the read
# paths below map them to the lowest tier, never to paid or unlimited caps.
# ---------------------------------------------------------------------------
VALID_PLANS = frozenset({"none", "starter", "professional", "enterprise"})

# Fail-closed tier ranks: only valid plans carry a rank. Unknown strings
# fall through to 0 (lowest) — never unlimited.
TIER_RANK = {
    "none": 0,
    "starter": 1,
    "professional": 2,
    "enterprise": 3,
}

# Server-side canon prices in cents. The checkout endpoint derives the
# charge from the VALIDATED plan — client JSON is never trusted for price.
PLAN_PRICE_CENTS = {
    "starter": 29700,
    "professional": 99700,
    "enterprise": 299700,
}

# Server-side display names (client-supplied plan_name is ignored).
PLAN_DISPLAY_NAMES = {
    "starter": "SINCOR Starter",
    "professional": "SINCOR Professional",
    "enterprise": "SINCOR Enterprise",
}

FEATURE_MIN_TIER = {
    "toa_demo": 0,
    "checkout": 0,
    "starter_agents": 1,
    "professional_agents": 2,
    "enterprise_agents": 3,
    "underwrite_live_spend": 2,
    "chroma_owner": 1,
}


@dataclass(frozen=True)
class Gate:
    allowed: bool
    plan: str
    feature: str
    required: str
    checkout_url: str


def normalize_plan(plan: str | None) -> str:
    return (plan or "").strip().lower()


def is_valid_plan(plan: str | None) -> bool:
    """True iff this is a storable plan string (the allowlist)."""
    return normalize_plan(plan) in VALID_PLANS


def plan_price_cents(plan: str | None) -> int | None:
    """Server-side canon price (cents) for a plan. None if not purchasable."""
    return PLAN_PRICE_CENTS.get(normalize_plan(plan))


def rank(plan: str | None) -> int:
    return TIER_RANK.get(normalize_plan(plan), 0)


# ---------------------------------------------------------------------------
# PLAN_LIMITS — the SINGLE source of truth for per-plan numeric caps.
# The dashboard blueprint (command_center) enforces roster_cap on the agent
# roster endpoint. None = unlimited.
# ---------------------------------------------------------------------------
PLAN_LIMITS = {
    "none": {"roster_cap": 5},
    "starter": {"roster_cap": 10},
    "professional": {"roster_cap": 25},
    "enterprise": {"roster_cap": None},
}


def plan_limits(plan: str | None) -> dict:
    """Return this plan's limits dict (a copy).

    Fail closed: unknown plan strings get the LOWEST tier's limits —
    never unlimited, never a paid tier.
    """
    name = normalize_plan(plan)
    if name in PLAN_LIMITS:
        return dict(PLAN_LIMITS[name])
    return dict(PLAN_LIMITS["none"])


def roster_cap(plan: str | None) -> int | None:
    """Max agents the dashboard roster shows for this plan. None = unlimited."""
    return plan_limits(plan).get("roster_cap")


def required_plan(feature: str) -> str:
    need = FEATURE_MIN_TIER.get(feature, 1)
    for name, value in (("starter", 1), ("professional", 2), ("enterprise", 3)):
        if value >= need:
            return name
    return "enterprise"


def checkout_url(plan: str | None = None, feature: str = "checkout") -> str:
    target = normalize_plan(plan) or required_plan(feature)
    if target not in {"starter", "professional", "enterprise"}:
        target = "starter"
    return f"/buy?plan={target}&src=plan_gate"


def allow(plan: str | None, feature: str) -> Gate:
    need = FEATURE_MIN_TIER.get(feature, 1)
    current = rank(plan)
    ok = current >= need
    req = required_plan(feature)
    return Gate(
        allowed=ok,
        plan=normalize_plan(plan) or "anon",
        feature=feature,
        required=req,
        checkout_url=checkout_url(req, feature),
    )


def require(plan: str | None, feature: str) -> Gate:
    gate = allow(plan, feature)
    if not gate.allowed:
        raise PermissionError(f"{feature} requires {gate.required} plan")
    return gate
