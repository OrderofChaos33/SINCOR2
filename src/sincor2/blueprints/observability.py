"""Public observability surfaces (read-only).

  GET /status -> aggregate service status; each section fail-soft
  GET /skills -> the SINCOR skill registry (same serialization the
                 agent card publishes)

(The third surface, GET /v1/a2a/pool/verify, lives with the other pool
routes in a2a_inbound_market.attach_market_routes.)

Every value comes from a real in-process source — the skill registry,
the bounty-pool ledger, the registration fabric. A source that fails
surfaces as {"error": "unavailable"} with the top-level status degraded
— never as a fabricated or plausible-looking number. Aggregates only:
no per-agent records, no allocation details, no secrets, and no env
values other than the public build SHA.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from flask import Blueprint, jsonify

observability_bp = Blueprint("observability", __name__)


def _pool_section():
    """Aggregate pool numbers + ledger verdict for /status.

    Same spent computation as GET /v1/a2a/pool/verify: sum of the
    spent-state allocations in the public status payload.
    """
    from sincor2.a2a_bounty_pool import bounty_pool

    pool = bounty_pool()
    status = pool.status()
    verification = pool.verify_ledger()
    spent_axm = sum(
        int(a.get("amount_wei") or 0) / 10**18
        for a in status.get("allocations") or []
        if a.get("state") == "spent"
    )
    return {
        "funded_axm": status.get("funded_axm"),
        "allocated_axm": status.get("allocated_axm"),
        "available_axm": status.get("available_axm"),
        "spent_axm": spent_axm,
        "ledger_ok": verification.get("ok") is True,
        "ledger_inconclusive": verification.get("inconclusive"),
    }


def _agents_section():
    """Registration totals, computed exactly as
    GET /v1/a2a/registration-velocity computes them."""
    from sincor2.a2a_inbound import get_fabric
    from sincor2.registration_velocity import velocity_report

    fabric = get_fabric()
    with fabric.lock:
        agents = {aid: dict(a) for aid, a in fabric.agents.items()}
    report = velocity_report(agents)
    return {
        "total_registered": report.get("total_registered"),
        "external": report.get("external"),
        "internal": report.get("internal"),
    }


@observability_bp.get("/skills")
def skills():
    """The platform skill registry — identical payload shape to the
    skills array in the published agent card."""
    from sincor2.a2a_integration import SINCOR_SKILLS

    return jsonify({
        "count": len(SINCOR_SKILLS),
        "skills": [s.to_dict() for s in SINCOR_SKILLS],
    }), 200


@observability_bp.get("/status")
def status():
    from sincor2.a2a_integration import PLATFORM_VERSION, SINCOR_SKILLS

    degraded = False
    try:
        pool = _pool_section()
    except Exception:
        pool = {"error": "unavailable"}
        degraded = True
    try:
        agents = _agents_section()
    except Exception:
        agents = {"error": "unavailable"}
        degraded = True
    return jsonify({
        "status": "degraded" if degraded else "ok",
        "service": "SINCOR Master Platform",
        "version": PLATFORM_VERSION,
        # Railway injects the deployed commit; absent locally — report
        # null, never invent a build identifier.
        "build": os.environ.get("RAILWAY_GIT_COMMIT_SHA"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "skills_count": len(SINCOR_SKILLS),
        "pool": pool,
        "agents": agents,
    }), 200
