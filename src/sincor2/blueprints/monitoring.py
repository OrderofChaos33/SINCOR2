from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from urllib import error as urllib_error
from urllib import request as urllib_request

from flask import Blueprint, current_app, jsonify
from flask_jwt_extended import jwt_required

logger = logging.getLogger(__name__)

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


@monitoring_bp.get("/api/metrics/treasury")
def treasury_metrics():
    """
    CEO KPI endpoint: treasury balances + 24h inflow ledger.

    On-chain snapshot is best-effort. Ledger is always local and authoritative
    for recorded events. Never exposes keys or signer material.
    """
    try:
        from sincor2.treasury_inflow import get_treasury_snapshot, ledger_summary_24h
    except ImportError:
        try:
            from src.sincor2.treasury_inflow import (
                get_treasury_snapshot,
                ledger_summary_24h,
            )
        except ImportError as exc:
            logger.error("treasury_inflow import failed: %s", exc)
            return jsonify({"status": "error", "detail": "treasury_inflow unavailable"}), 503

    include_onchain = True
    try:
        settings = current_app.config.get("SINCOR_SETTINGS")
        if settings and not getattr(settings, "base_rpc_url", None):
            # still attempt default public RPC inside get_treasury_snapshot
            pass
    except Exception:
        pass

    try:
        snap = get_treasury_snapshot(include_onchain=include_onchain)
        summary = ledger_summary_24h()
        return jsonify(
            {
                "status": "ok",
                "kpi": "treasury_inflow",
                "snapshot": snap.to_dict(),
                "ledger_24h": summary,
            }
        ), 200
    except Exception as exc:
        logger.exception("treasury metrics failed")
        return jsonify({"status": "error", "detail": str(exc)[:200]}), 500
