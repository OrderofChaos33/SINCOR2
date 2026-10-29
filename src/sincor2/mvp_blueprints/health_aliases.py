"""Liveness aliases. Always 200. No outbound RPC."""
from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, jsonify

bp = Blueprint("health_aliases", __name__)


def _build_stamp():
    try:
        from sincor2.build_info import build_info

        return build_info()
    except Exception:
        return {"sha": "unknown", "short": "unknown", "time": "unknown"}


def _payload():
    return {
        "status": "healthy",
        "service": "SINCOR2 MVP",
        "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "version": "1.0.0-mvp",
        "build": _build_stamp(),
        "checks": {"process": {"ready": True, "critical": True, "detail": "up"}},
        "readiness": {"ready": True, "degraded": False, "confidence": 1.0},
    }


@bp.route("/api/health", methods=["GET"])
@bp.route("/healthz", methods=["GET"])
def health_alias():
    return jsonify(_payload()), 200
