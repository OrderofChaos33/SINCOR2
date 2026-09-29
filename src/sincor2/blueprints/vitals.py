"""OBS-01 Agent Vitals dashboard (BETA, unlisted).

Customer-facing, read-only vitals surface. Auth: the existing customer
session scope used by the production console pages — a logged-in customer
(``session["user_email"]``) or an admin session; anything else bounces to
``/login?next=...``. No new auth invented, no existing auth weakened.

Deliberately UNLINKED from all public nav, pricing, footers, and sitemaps
(founder standing rule: unproven products stay off the public surface).
Reachable only by direct URL: ``/obs/vitals``.

Alert evaluation piggybacks on reads of ``/api/obs/vitals`` and
``/obs/vitals`` — there is no background scheduler yet (documented beta
gap). Cooldowns in ``sincor2.obs_skus.vitals`` prevent webhook spam.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, redirect, render_template, session

vitals_bp = Blueprint("vitals", __name__)


def _customer_or_admin() -> bool:
    # Same customer scope as the production console pages:
    # logged-in customer session OR admin session.
    return bool(session.get("user_email")) or bool(session.get("is_admin"))


def _bounce(next_path: str):
    return redirect("/login?next=" + next_path, code=302)


@vitals_bp.get("/obs/vitals")
def vitals_dashboard():
    if not _customer_or_admin():
        return _bounce("/obs/vitals")
    from sincor2.obs_skus import vitals as obs_vitals

    fleet = obs_vitals.build_fleet_snapshot()
    # Evaluate alert rules on the read (cooldown-guarded; no scheduler yet).
    alert_summary = obs_vitals.evaluate_and_dispatch(fleet)
    return render_template(
        "vitals_dashboard.html",
        fleet=fleet,
        alert_summary=alert_summary,
    )


@vitals_bp.get("/api/obs/vitals")
def vitals_api():
    if not _customer_or_admin():
        return jsonify({"error": "login required", "status": 401}), 401
    from sincor2.obs_skus import vitals as obs_vitals

    fleet = obs_vitals.build_fleet_snapshot()
    alert_summary = obs_vitals.evaluate_and_dispatch(fleet)
    return jsonify(
        {
            "status": "ok",
            "beta": True,
            "fleet": fleet,
            "alerts": {
                "evaluated": alert_summary["evaluated"],
                "dispatched": alert_summary["dispatched"],
                "results": alert_summary["results"],
            },
        }
    ), 200
