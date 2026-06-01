from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import decode_token, get_jwt_identity, jwt_required

from sincor2.error_handling import ApiError

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")


@auth_bp.post("/login")
def login():
    auth = current_app.extensions["sincor_auth"]
    payload = request.get_json(silent=True) or {}
    username = payload.get("username", "")
    password = payload.get("password", "")
    if not username or not password:
        raise ApiError("invalid_request", "Username and password are required", status=400)

    result = auth.authenticate_user(username, password)
    status_code = 200 if result.get("success") else 401
    if not result.get("success"):
        raise ApiError(
            "auth_failed",
            result.get("error", "Authentication failed"),
            status=status_code,
        )
    return jsonify(result), status_code


@auth_bp.post("/verify-token")
def verify_token():
    payload = request.get_json(silent=True) or {}
    token = payload.get("token", "")
    if not token:
        raise ApiError("invalid_request", "token is required", status=400)

    decode_token(token)
    return jsonify({"status": "ok", "code": "token_valid", "message": "Token is valid"})


@auth_bp.get("/profile")
@jwt_required()
def profile():
    user_id = get_jwt_identity()
    return jsonify({"status": "ok", "user": {"id": user_id}})


@auth_bp.get("/admin/users")
@jwt_required()
def admin_users():
    return jsonify({"status": "ok", "users": []})
