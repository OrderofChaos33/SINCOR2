"""Additive Flask mount: merkle quest, SLA receipts, SADAS, Polyclaw.

Does not replace Blueprint('kya') at /v1/kya (see kya_blueprint.py).
Colliding /v1/kya/list|bind|verify|lookup|health|heartbeat routes are
intentionally omitted so the live identity registry stays the source of truth.
"""
from __future__ import annotations

import hmac
import logging
import os

from flask import Blueprint, jsonify, request

from sincor2.kya.airdrop_quest import get_quest
from sincor2.kya.pricing import PRICE_BOOK
from sincor2.kya.receipts import get_receipts
from sincor2.kya.sadas import get_sadas
from sincor2.kya.sla import get_sla
from sincor2.treasury_hold import standing_order

logger = logging.getLogger("sincor.kya")

kya_stack_bp = Blueprint("kya_stack", __name__)


def _err(msg: str, status: int = 400):
    return jsonify({"error": msg, "status": status}), status


def _admin_ok() -> bool:
    expected = (os.environ.get("KYA_ADMIN_KEY") or "").strip()
    if not expected:
        return False
    provided = (
        request.headers.get("X-KYA-Admin")
        or request.headers.get("X-Admin-Key")
        or ""
    ).strip()
    if not provided:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def mount_kya(app) -> bool:
    names = getattr(app, "blueprints", {}) or {}
    if "kya_stack" in names:
        return True
    app.register_blueprint(kya_stack_bp)
    logger.info("KYA additive stack mounted (quest, SLA receipts, SADAS)")
    return True


@kya_stack_bp.get("/v1/kya/stack")
def stack_health():
    return jsonify(
        {
            "ok": True,
            "prices": PRICE_BOOK,
            "quest": get_quest().stats(),
            "hold": standing_order(),
            "sadas": get_sadas().scorecard(),
        }
    )


@kya_stack_bp.post("/v1/sla/subscribe")
def sla_sub():
    body = request.get_json(silent=True) or {}
    return jsonify(get_sla().subscribe(str(body.get("kya_id") or "")))


@kya_stack_bp.post("/v1/sla/ping")
def sla_ping():
    body = request.get_json(silent=True) or {}
    return jsonify(
        get_sla().ping(
            str(body.get("kya_id") or ""),
            ok=bool(body.get("ok", True)),
            latency_ms=int(body.get("latency_ms") or 0),
        )
    )


@kya_stack_bp.get("/v1/sla/receipts")
def sla_receipts():
    return jsonify({"receipts": get_sla().list_receipts(request.args.get("kya_id"))})


@kya_stack_bp.post("/v1/sadas/publish")
def sadas_pub():
    if not _admin_ok():
        return _err("admin key required", 401)
    body = request.get_json(silent=True) or {}
    return jsonify(get_sadas().publish(body))


@kya_stack_bp.post("/v1/sadas/subscribe")
def sadas_sub():
    body = request.get_json(silent=True) or {}
    return jsonify(get_sadas().subscribe(str(body.get("kya_id") or ""), str(body.get("token") or "")))


@kya_stack_bp.get("/v1/sadas/scorecard")
def sadas_card():
    return jsonify(get_sadas().scorecard())


@kya_stack_bp.get("/v1/sadas/feed")
def sadas_feed():
    return jsonify(get_sadas().feed(request.args.get("token")))


@kya_stack_bp.post("/v1/polyclaw/day")
def poly_day():
    if not _admin_ok():
        return _err("admin key required", 401)
    body = request.get_json(silent=True) or {}
    return jsonify(get_receipts().record_day(body))


@kya_stack_bp.get("/v1/polyclaw/scorecard")
def poly_card():
    return jsonify(get_receipts().scorecard())


@kya_stack_bp.get("/v1/quest")
def quest_stats():
    return jsonify(get_quest().stats())


@kya_stack_bp.post("/v1/quest/seed")
def quest_seed():
    if not _admin_ok():
        return _err("set KYA_ADMIN_KEY and send X-KYA-Admin to load the merkle list", 401)
    body = request.get_json(silent=True) or {}
    wallets = body.get("wallets") or body.get("addresses") or []
    if not isinstance(wallets, list):
        return _err("wallets[] required")
    n = get_quest().seed([str(w) for w in wallets], source=str(body.get("source") or "api"))
    return jsonify({"loaded": n, **get_quest().stats()})


@kya_stack_bp.get("/v1/quest/eligibility")
def quest_elig():
    wallet = str(request.args.get("wallet") or "")
    return jsonify(get_quest().eligibility(wallet))


@kya_stack_bp.post("/v1/quest/claim")
def quest_claim():
    body = request.get_json(silent=True) or {}
    try:
        rec = get_quest().claim(
            wallet=str(body.get("wallet") or ""),
            agent_id=str(body.get("agent_id") or ""),
            proof=body.get("proof"),
        )
        return jsonify(rec)
    except KeyError:
        return _err("agent not listed", 404)
    except PermissionError as exc:
        return _err(str(exc), 403)
