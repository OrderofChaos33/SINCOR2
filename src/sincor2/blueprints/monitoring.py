from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify
from flask_jwt_extended import jwt_required

monitoring_bp = Blueprint("monitoring", __name__)


@monitoring_bp.get("/health")
def health_check():
    settings = current_app.config["SINCOR_SETTINGS"]
    platform = current_app.extensions.get("sincor_platform", {})
    return jsonify(
        {
            "status": "healthy",
            "service": "SINCOR2",
            "environment": settings.environment,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "platform": {
                "agent_cards": platform.get("registered_cards", 0),
                "vertical_agents": len(platform.get("vertical_agents", {})),
                "marketplace": "available" if platform.get("registry") else "degraded",
            },
        }
    )


@monitoring_bp.get("/api/monitoring/dashboard")
@jwt_required(optional=True)
def dashboard_metrics():
    payment_status = (
        "available" if current_app.extensions.get("stripe_checkout") else "degraded"
    )
    waitlist_status = (
        "available" if current_app.extensions.get("waitlist_manager") else "degraded"
    )
    return jsonify(
        {
            "status": "active",
            "metrics": {
                "payments": payment_status,
                "waitlist": waitlist_status,
            },
        }
    )
