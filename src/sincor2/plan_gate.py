"""Plan-tier feature gates.

Canon prices: starter 297 / professional 997 / enterprise 2997.
Stripe USDC fallback is the human path when token spot is unavailable.
"""
from __future__ import annotations

from dataclasses import dataclass

TIER_RANK = {
    "": 0,
    "anon": 0,
    "guest": 0,
    "none": 0,
    "starter": 1,
    "professional": 2,
    "pro": 2,
    "enterprise": 3,
    "member": 1,
    "operator": 3,
    "admin": 3,
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


def rank(plan: str | None) -> int:
    return TIER_RANK.get(normalize_plan(plan), 0)


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
