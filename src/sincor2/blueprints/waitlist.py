from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from sincor2.error_handling import ApiError
from sincor2.validation_models import WaitlistSignup, validate_request

waitlist_bp = Blueprint("waitlist", __name__, url_prefix="/api/waitlist")


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
