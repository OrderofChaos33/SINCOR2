from __future__ import annotations

import hmac
import os

from flask import Blueprint, current_app, jsonify, request, session

from sincor2.error_handling import ApiError, api_error
from sincor2.validation_models import WaitlistSignup, validate_request

waitlist_bp = Blueprint("waitlist", __name__, url_prefix="/api/waitlist")


def _is_admin_request() -> bool:
    """Admin gate for the stats route — mirrors mvp_app._require_admin.

    mvp_app imports the blueprints, so importing its helpers here would
    be circular. This reproduces the same three credentials against the
    same sources (the ADMIN_USERNAME / ADMIN_PASSWORD env vars):

    * admin session: ``session["is_admin"]`` set and
      ``session["admin_username"]`` matching ADMIN_USERNAME
      (mvp_app._is_admin_session), or
    * a Bearer JWT (or ``access_token`` cookie) whose ``sub`` is the
      admin username AND whose ``role`` claim is ``'admin'``
      (mvp_app._check_admin_token), or
    * the ``X-Admin-Key`` header matching ADMIN_PASSWORD
      (mvp_app._check_admin_key).

    Fail-closed: any missing or mismatched credential returns False.
    On the production app this is a second layer — mvp_app also runs
    _require_admin as a before_request guard over /api/waitlist/stats.
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
            # Identity AND role (hunt-32 W-44): sub alone is never
            # sufficient — every real admin login mints role='admin'.
            if decoded.get("role") == "admin" and hmac.compare_digest(
                str(decoded.get("sub") or "").lower(), admin_username.lower()
            ):
                return True
        except Exception:
            pass

    admin_password = (os.environ.get("ADMIN_PASSWORD") or "").strip()
    key = request.headers.get("X-Admin-Key", "")
    if key and admin_password:
        try:
            if hmac.compare_digest(str(key), admin_password):
                return True
        except (TypeError, ValueError):
            # Non-ASCII header value: un-comparable in constant time —
            # fail closed instead of surfacing a 500 (hunt-32 W-44).
            pass

    return False


@waitlist_bp.post("")
@waitlist_bp.post("/join")
def join_waitlist():
    payload = request.get_json(silent=True) or {}
    validated, error = validate_request(WaitlistSignup, payload)
    if error:
        raise ApiError("invalid_waitlist_payload", error, status=400)

    manager = current_app.extensions.get("waitlist_manager")
    if not manager:
        raise ApiError("waitlist_unavailable", "Waitlist service unavailable", status=503)

    result = manager.add_to_waitlist(validated)
    if not result.get("success"):
        raise ApiError("waitlist_error", result.get("error", "Failed to join waitlist"), status=400)

    return jsonify(result), 201


@waitlist_bp.get("/stats")
def waitlist_stats():
    # Admin gate FIRST — before any manager access (fail-closed). No
    # count data of any kind may leak to anonymous or non-admin callers.
    if not _is_admin_request():
        return jsonify({"error": "Unauthorized"}), 401

    manager = current_app.extensions.get("waitlist_manager")
    if not manager:
        # Same payload the join handler's ApiError produces where error
        # handlers are registered; returned directly so the 503 holds on
        # apps without them too.
        return api_error("waitlist_unavailable", "Waitlist service unavailable", status=503)

    # Aggregates only: total / verified / latest signup timestamp,
    # computed from the waitlist table. No per-user data exists in this
    # response by construction (see WaitlistManager.get_signup_stats).
    return jsonify(manager.get_signup_stats()), 200
