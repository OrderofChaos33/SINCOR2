"""Path 48 — Governance & Treasury Risk Briefings API.

  GET  /api/briefings/plans                 tier list (from PLATFORM_PLANS)
  POST /api/briefings/subscribe             {wallet, email, plan_id}
                                            -> subscriber record + token
  POST /api/briefings/generate              auth; body = snapshot contract
                                            (see sincor2.briefing_product)
                                            -> generates + stores a delivery
  GET  /api/briefings/latest                auth -> subscriber's latest
  GET  /api/briefings/deliveries/<id>       auth + ownership -> one delivery
  POST /api/briefings/status                auth; {status} -> transition

Auth: ``X-Briefing-Token`` header issued at subscribe time. Delivery is
generate + store + subscriber-retrievable only — nothing is emailed or
sent anywhere by this surface (outbound sending stays founder-gated).
No new payment rail: tiers are PLATFORM_PLANS keys (report / intel).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from sincor2 import briefing_product as bp_mod

briefings_bp = Blueprint("briefings", __name__)


def _auth():
    sub = bp_mod.authenticate(request.headers.get("X-Briefing-Token", ""))
    if sub is None:
        return None, (jsonify({"ok": False, "error": "unauthorized"}), 401)
    return sub, None


@briefings_bp.get("/api/briefings/plans")
def briefing_plans():
    return jsonify({"ok": True, "plans": bp_mod.briefing_plans()})


@briefings_bp.post("/api/briefings/subscribe")
def briefing_subscribe():
    body = request.get_json(silent=True) or {}
    result = bp_mod.create_subscriber(
        wallet=str(body.get("wallet") or ""),
        plan_id=str(body.get("plan_id") or ""),
        email=str(body.get("email") or ""),
    )
    if not result.get("ok"):
        return jsonify(result), 400
    return jsonify(result), 201


@briefings_bp.post("/api/briefings/generate")
def briefing_generate():
    sub, err = _auth()
    if err:
        return err
    snapshot = request.get_json(silent=True)
    if not isinstance(snapshot, dict) or "holds" not in snapshot:
        return jsonify({"ok": False, "error": "snapshot_required"}), 400
    result = bp_mod.generate_delivery(sub, snapshot)
    if not result.get("ok"):
        return jsonify(result), 403
    return jsonify(result), 201


@briefings_bp.get("/api/briefings/latest")
def briefing_latest():
    sub, err = _auth()
    if err:
        return err
    delivery = bp_mod.latest_delivery(sub["id"])
    if delivery is None:
        return jsonify({"ok": False, "error": "no_delivery"}), 404
    return jsonify({"ok": True, "delivery": delivery})


@briefings_bp.get("/api/briefings/deliveries/<delivery_id>")
def briefing_delivery(delivery_id: str):
    sub, err = _auth()
    if err:
        return err
    delivery = bp_mod.get_delivery(delivery_id, sub["id"])
    if delivery is None:
        return jsonify({"ok": False, "error": "not_found"}), 404
    return jsonify({"ok": True, "delivery": delivery})


@briefings_bp.post("/api/briefings/status")
def briefing_status():
    sub, err = _auth()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    result = bp_mod.set_subscriber_status(sub["id"], str(body.get("status") or ""))
    if not result.get("ok"):
        return jsonify(result), 400
    return jsonify(result)
