"""Admin visibility for B2B pack intakes: GET /api/admin/intakes.

A pack intake that reaches ``intake_complete`` is a buyer who did their
part and is now waiting on a human (owner review -> human-reviewed
deliverable). Nothing else surfaces those rows, so a completed intake
could sit unnoticed indefinitely. This endpoint lists the
``intake_complete`` records at least 24h old — the ones an operator
must act on — and nothing else.

Admin-gated exactly like /api/waitlist/stats: this module reproduces
the _require_admin credential check (admin session / admin JWT /
X-Admin-Key) and the production app additionally runs _require_admin
as a before_request guard over this path. The response carries the
fields an operator needs for follow-up — intake id, pack, status,
buyer email, age — and no other buyer data.
"""
from __future__ import annotations

import hmac
import os
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, session

admin_intakes_bp = Blueprint("admin_intakes", __name__, url_prefix="/api/admin")

STALE_INTAKE_MIN_AGE_HOURS = 24.0


def _is_admin_request() -> bool:
    """Admin gate — mirrors mvp_app._require_admin via blueprints/waitlist.

    mvp_app imports the blueprints, so importing its helpers here would
    be circular. This reproduces the same three credentials against the
    same sources (the ADMIN_USERNAME / ADMIN_PASSWORD env vars):

    * admin session: ``session["is_admin"]`` set and
      ``session["admin_username"]`` matching ADMIN_USERNAME, or
    * a Bearer JWT (or ``access_token`` cookie) whose ``sub`` is the
      admin username, or
    * the ``X-Admin-Key`` header matching ADMIN_PASSWORD.

    Fail-closed: any missing or mismatched credential returns False.
    """
    admin_username = (os.environ.get("ADMIN_USERNAME") or "").strip()
    if session.get("is_admin") and admin_username:
        if hmac.compare_digest(
            str(session.get("admin_username") or "").lower(), admin_username.lower()
        ):
            return True

    token = None
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[len("Bearer ") :].strip()
    elif request.cookies:
        token = request.cookies.get("access_token")
    if token and admin_username:
        try:
            from flask_jwt_extended import decode_token

            decoded = decode_token(token)
            if hmac.compare_digest(
                str(decoded.get("sub") or "").lower(), admin_username.lower()
            ):
                return True
        except Exception:
            pass

    admin_password = (os.environ.get("ADMIN_PASSWORD") or "").strip()
    key = request.headers.get("X-Admin-Key", "")
    if key and admin_password:
        if hmac.compare_digest(str(key), admin_password):
            return True

    return False


@admin_intakes_bp.get("/intakes")
def list_stale_intakes():
    # Admin gate FIRST — before any ledger access (fail-closed). No
    # intake data of any kind may leak to anonymous or non-admin callers.
    if not _is_admin_request():
        return jsonify({"error": "Unauthorized"}), 401

    from sincor2.payment_ledger import (
        _parse_iso_utc,
        list_stale_completed_pack_intakes,
    )
    from sincor2.rail_routes import _rail_db_path

    now = datetime.now(timezone.utc)
    stale = list_stale_completed_pack_intakes(
        _rail_db_path(),
        min_age_hours=STALE_INTAKE_MIN_AGE_HOURS,
        now=now,
    )
    rows = []
    for intake in stale:
        created = _parse_iso_utc(intake.created_at)
        age_hours = (
            round((now - created).total_seconds() / 3600.0, 1)
            if created is not None
            else None
        )
        rows.append(
            {
                "intake_id": intake.intake_id,
                "pack_sku": intake.pack_sku,
                "status": intake.status,
                "customer_email": intake.customer_email,
                "created_at": intake.created_at,
                "age_hours": age_hours,
            }
        )
    return jsonify(
        {
            "ok": True,
            "min_age_hours": STALE_INTAKE_MIN_AGE_HOURS,
            "count": len(rows),
            "intakes": rows,
        }
    ), 200
