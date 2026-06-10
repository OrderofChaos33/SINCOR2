from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib import error as urllib_error
from urllib import request as urllib_request

from flask import Blueprint, current_app, jsonify
from flask_jwt_extended import jwt_required

monitoring_bp = Blueprint("monitoring", __name__)


def _probe_base_rpc(rpc_url: str) -> tuple[bool, str]:
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": "health", "method": "eth_chainId", "params": []}
    ).encode("utf-8")
    req = urllib_request.Request(
        rpc_url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib_request.urlopen(req, timeout=3) as response:
            data = json.loads(response.read().decode("utf-8"))
        chain_id = data.get("result")
        if chain_id:
            return True, chain_id
        return False, "missing_chain_id"
    except (urllib_error.URLError, TimeoutError, ValueError, OSError) as exc:
        return False, str(exc)


@monitoring_bp.get("/health")
def health_check():
    settings = current_app.config["SINCOR_SETTINGS"]
    platform = current_app.extensions.get("sincor_platform", {})
    checks = {
        "settings": {"ready": bool(settings.secret_key and settings.jwt_secret_key)},
        "registry": {"ready": platform.get("registry") is not None},
        "router": {"ready": platform.get("router") is not None},
        "vertical_agents": {"ready": len(platform.get("vertical_agents", {})) > 0},
        "settlement": {"ready": platform.get("settlement") is not None},
    }

    if settings.base_rpc_url:
        rpc_ready, detail = _probe_base_rpc(settings.base_rpc_url)
        checks["base_rpc"] = {"ready": rpc_ready, "detail": detail}
    else:
        checks["base_rpc"] = {"ready": True, "detail": "not_configured"}

    overall_ready = all(check["ready"] for check in checks.values())
    payload = {
        "status": "healthy" if overall_ready else "degraded",
        "service": "SINCOR2",
        "environment": settings.environment,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "platform": {
            "agent_cards": platform.get("registered_cards", 0),
            "vertical_agents": len(platform.get("vertical_agents", {})),
            "marketplace": "available" if platform.get("registry") else "degraded",
        },
        "checks": checks,
    }
    return jsonify(payload), 200 if overall_ready else 503


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
