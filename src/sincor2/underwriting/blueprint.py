"""Public underwriting HTTP surface. Additive. Does not replace /v1/kya."""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from sincor2.underwriting.engine import (
    AXM,
    CHAIN_ID,
    ERC8004_IDENTITY,
    TREASURY,
    UNDERWRITE_BPS,
    VERIFY_FEE_AXM,
    UnderwritingEngine,
)

logger = logging.getLogger("sincor.underwriting")

uw_bp = Blueprint("underwriting", __name__)
_engine = UnderwritingEngine()


def mount_underwriting(app) -> bool:
    names = getattr(app, "blueprints", {}) or {}
    if "underwriting" in names:
        return True
    app.register_blueprint(uw_bp)
    logger.info("Underwriting runtime mounted at /v1/agents and /v1/mandates")
    return True


def _err(msg: str, status: int = 400):
    return jsonify({"error": msg, "status": status}), status


@uw_bp.get("/v1/underwriting/health")
def health():
    return jsonify(
        {
            "ok": True,
            "product": "agent-underwriting-runtime",
            "chain_id": CHAIN_ID,
            "axm": AXM,
            "treasury": TREASURY,
            "erc8004_identity": ERC8004_IDENTITY,
            "verify_fee_axm": VERIFY_FEE_AXM,
            "underwrite_bps": UNDERWRITE_BPS,
            "jsonrpc": "POST /api/a2a method message/send — not GET /api/a2a/message/send",
        }
    )


@uw_bp.post("/v1/agents/register")
def register_agent():
    body = request.get_json(silent=True) or {}
    try:
        rec = _engine.register_agent(body)
        return jsonify(rec), 201
    except ValueError as exc:
        return _err(str(exc), 400)


@uw_bp.post("/v1/mandates/underwrite")
def underwrite():
    body = request.get_json(silent=True) or {}
    rec = _engine.underwrite(body)
    status = 200 if rec.get("decision") == "allow" else 403
    return jsonify(rec), status


@uw_bp.post("/v1/mandates/<envelope_id>/settle")
def settle(envelope_id: str):
    body = request.get_json(silent=True) or {}
    try:
        rec = _engine.settle(envelope_id, body)
        return jsonify(rec), 201
    except KeyError:
        return _err("envelope not found", 404)
    except PermissionError as exc:
        return _err(str(exc), 403)
    except ValueError as exc:
        return _err(str(exc), 400)


@uw_bp.post("/v1/mandates/<envelope_id>/revoke")
def revoke_envelope(envelope_id: str):
    body = request.get_json(silent=True) or {}
    mandate = _engine.store.find_one("mandates", "envelope_id", envelope_id)
    agent_id = str((body.get("agent_id") or (mandate or {}).get("agent_id") or ""))
    try:
        rec = _engine.revoke(agent_id, str(body.get("reason") or "revoke"))
        return jsonify(rec)
    except ValueError as exc:
        return _err(str(exc), 400)


@uw_bp.post("/v1/agents/<agent_id>/revoke")
def revoke_agent(agent_id: str):
    body = request.get_json(silent=True) or {}
    rec = _engine.revoke(agent_id, str(body.get("reason") or "operator_revoke"))
    return jsonify(rec)


@uw_bp.get("/v1/receipts/<receipt_id>")
def get_receipt(receipt_id: str):
    rec = _engine.receipt(receipt_id)
    if not rec:
        return _err("receipt not found", 404)
    return jsonify(rec)


@uw_bp.get("/v1/receipts")
def list_receipts():
    return jsonify({"receipts": _engine.list_receipts()})
