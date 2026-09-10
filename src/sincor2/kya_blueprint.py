"""Flask blueprint — mount next to inbound A2A.

    from sincor2.kya_blueprint import kya_bp
    app.register_blueprint(kya_bp)
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from sincor2 import kya_registry as kya

kya_bp = Blueprint("kya", __name__, url_prefix="/v1/kya")


def _err(msg: str, status: int = 400):
    return jsonify({"error": msg, "status": status}), status


@kya_bp.get("/health")
def health():
    return jsonify({"ok": True, **kya.snapshot()})


@kya_bp.get("/directory")
def directory():
    """Static path must sit above /<kya_id> or Flask treats 'directory' as an id."""
    return jsonify(kya.snapshot())


@kya_bp.post("/list")
def list_agent():
    body = request.get_json(silent=True) or {}
    try:
        rec = kya.list_from_inbound(body, card=body.get("agent_card") if isinstance(body.get("agent_card"), dict) else None)
    except ValueError as exc:
        return _err(str(exc))
    return jsonify(rec), 201


@kya_bp.post("/bind")
def bind():
    body = request.get_json(silent=True) or {}
    try:
        rec = kya.bind(
            agent_id=str(body.get("agent_id") or ""),
            principal=str(body.get("principal") or ""),
            signature=str(body.get("signature") or ""),
            message=str(body.get("message") or ""),
            recovered=body.get("recovered"),
        )
    except KeyError:
        return _err("unknown agent", 404)
    except ValueError as exc:
        return _err(str(exc))
    return jsonify(rec)


@kya_bp.post("/verify")
def verify():
    body = request.get_json(silent=True) or {}
    kya_id = str(body.get("kya_id") or "")
    if body.get("stake_tx") and body.get("stake_wei"):
        try:
            kya.apply_stake(kya_id, str(body["stake_wei"]), str(body["stake_tx"]))
        except (KeyError, ValueError) as exc:
            return _err(str(exc), 404 if isinstance(exc, KeyError) else 400)
    try:
        rec = kya.verify(kya_id)
    except KeyError:
        return _err("unknown kya", 404)
    except ValueError as exc:
        return _err(str(exc))
    return jsonify(rec)


@kya_bp.post("/heartbeat")
def heartbeat():
    body = request.get_json(silent=True) or {}
    rec = kya.heartbeat(str(body.get("agent_id") or ""), ok=bool(body.get("ok", True)), latency_ms=body.get("latency_ms"))
    if rec is None:
        return _err("unknown agent", 404)
    return jsonify(rec)


@kya_bp.post("/sla")
def sla():
    body = request.get_json(silent=True) or {}
    try:
        rec = kya.post_sla(
            kya_id=str(body.get("kya_id") or ""),
            ok=bool(body.get("ok", True)),
            latency_ms=int(body.get("latency_ms") or 0),
            proof_tx=body.get("proof_tx"),
        )
    except KeyError:
        return _err("unknown kya", 404)
    return jsonify(rec)


@kya_bp.post("/revoke")
def revoke():
    body = request.get_json(silent=True) or {}
    try:
        rec = kya.revoke(str(body.get("kya_id") or ""), reason=str(body.get("reason") or ""))
    except KeyError:
        return _err("unknown kya", 404)
    return jsonify(rec)


@kya_bp.get("/agent/<agent_id>")
def by_agent(agent_id: str):
    rec = kya.get_by_agent(agent_id)
    if rec is None:
        return _err("unknown agent", 404)
    return jsonify(kya.refresh_status(rec))


@kya_bp.get("/lookup")
def lookup():
    wallet = str(request.args.get("wallet") or "")
    if not kya.WALLET_RE.match(wallet):
        return _err("bad wallet")
    return jsonify({"records": kya.lookup_wallet(wallet)})


@kya_bp.get("/<kya_id>")
def get_one(kya_id: str):
    rec = kya.get(kya_id)
    if rec is None:
        return _err("unknown kya", 404)
    return jsonify(kya.refresh_status(rec))
