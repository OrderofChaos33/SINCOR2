"""Session membership helpers for paid-shell templates."""
from __future__ import annotations

from flask import session

PAID_PLANS = {"starter", "professional", "enterprise", "pro", "member", "operator", "admin"}


def resolve_plan() -> str:
    raw = (
        session.get("plan")
        or session.get("membership")
        or session.get("plan_tier")
        or session.get("tier")
        or ""
    )
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "ignore")
    return str(raw).strip()


def is_member() -> bool:
    if session.get("paid") or session.get("is_member") or session.get("user_id") or session.get("email"):
        plan = resolve_plan().lower()
        if plan in {"none", "anon", "guest"}:
            return bool(session.get("paid"))
        return True
    return resolve_plan().lower() in PAID_PLANS


def inject_membership():
    plan = resolve_plan()
    member = is_member()
    return {
        "is_member": member,
        "plan": plan or ("Member" if member else ""),
        "show_upsell": not member,
    }


def register_membership(app):
    app.context_processor(inject_membership)
