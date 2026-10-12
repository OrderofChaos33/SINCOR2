"""Flask blueprint — mount next to inbound A2A.

    from sincor2.kya_blueprint import kya_bp
    app.register_blueprint(kya_bp)
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from sincor2 import kya_registry as kya
from sincor2.authz import AuthzError, Role, resolve_principal

kya_bp = Blueprint("kya", __name__, url_prefix="/v1/kya")


def _err(msg: str, status: int = 400):
    return jsonify({"error": msg, "status": status}), status


def _authz_err(exc: AuthzError):
    # Generic message on purpose: never leak whether a record exists.
    code = "unauthorized" if exc.status_code == 401 else "forbidden"
    return jsonify({"error": code, "status": exc.status_code}), exc.status_code


def _require_kya_owner_or_admin(rec: dict, principal) -> None:
    """Enforce owner-or-admin on a KYA mutation.

    Raises:
        AuthzError: 403 when the principal is neither the record owner
            nor an admin.
    """
    if principal.role == Role.admin:
        return
    # Ownership is proven server-side: the principal's *verified* wallet
    # (signed JWT claim or server session — never a request parameter)
    # must match the record's principal/agent_wallet, or the principal must
    # be the registered agent itself (session-identified by agent_id).
    record_wallets = {
        str(rec.get("principal") or "").lower(),
        str(rec.get("agent_wallet") or "").lower(),
    } - {""}
    principal_wallet = str(principal.wallet or "").lower()
    if principal_wallet and principal_wallet in record_wallets:
        return
    agent_id = str(rec.get("agent_id") or "")
    if agent_id and principal.id == agent_id:
        return
    raise AuthzError("forbidden", 403)


def _guard_kya_mutation(kya_id: str):
    """Authenticate + authorize a KYA mutation. Returns (record, error_response).

    401 is returned before any record lookup so unauthenticated scanners
    learn nothing about record existence.
    """
    try:
        principal = resolve_principal()  # raises AuthzError(401) when unauthenticated
    except AuthzError as exc:
        return None, _authz_err(exc)
    rec = kya.get(kya_id)
    if rec is None:
        return None, _err("unknown kya", 404)
    try:
        _require_kya_owner_or_admin(rec, principal)
    except AuthzError as exc:
        return None, _authz_err(exc)
    return rec, None


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
    except ValueError:
        return _err("bad request")
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
        )
    except KeyError:
        return _err("unknown agent", 404)
    except ValueError:
        return _err("bad request")
    return jsonify(rec)


@kya_bp.post("/verify")
def verify():
    body = request.get_json(silent=True) or {}
    kya_id = str(body.get("kya_id") or "")
    if body.get("stake_tx") and body.get("stake_wei"):
        # W-76: stake application is a state mutation — authenticate and
        # authorize (owner or admin) BEFORE apply_stake persists anything.
        _, err = _guard_kya_mutation(kya_id)
        if err is not None:
            return err
        try:
            kya.apply_stake(kya_id, str(body["stake_wei"]), str(body["stake_tx"]))
        except (KeyError, ValueError) as exc:
            return _err("unknown kya" if isinstance(exc, KeyError) else "bad request", 404 if isinstance(exc, KeyError) else 400)
    try:
        rec = kya.verify(kya_id)
    except KeyError:
        return _err("unknown kya", 404)
    except ValueError:
        return _err("bad request")
    return jsonify(rec)


@kya_bp.post("/heartbeat")
def heartbeat():
    body = request.get_json(silent=True) or {}
    try:
        rec = kya.heartbeat(str(body.get("agent_id") or ""), ok=bool(body.get("ok", True)), latency_ms=body.get("latency_ms"))
    except ValueError:
        return _err("bad request")
    if rec is None:
        return _err("unknown agent", 404)
    return jsonify(rec)


@kya_bp.post("/sla")
def sla():
    body = request.get_json(silent=True) or {}
    try:
        latency_ms = int(body.get("latency_ms") or 0)
    except (TypeError, ValueError):
        return _err("bad latency_ms")
    try:
        rec = kya.post_sla(
            kya_id=str(body.get("kya_id") or ""),
            ok=bool(body.get("ok", True)),
            latency_ms=latency_ms,
            proof_tx=body.get("proof_tx"),
        )
    except KeyError:
        return _err("unknown kya", 404)
    return jsonify(rec)


@kya_bp.post("/revoke")
def revoke():
    body = request.get_json(silent=True) or {}
    kya_id = str(body.get("kya_id") or "")
    _, err = _guard_kya_mutation(kya_id)
    if err is not None:
        return err
    try:
        rec = kya.revoke(kya_id, reason=str(body.get("reason") or ""))
    except KeyError:
        return _err("unknown kya", 404)
    return jsonify(rec)


@kya_bp.post("/unstake/request")
def unstake_request():
    body = request.get_json(silent=True) or {}
    kya_id = str(body.get("kya_id") or "")
    _, err = _guard_kya_mutation(kya_id)
    if err is not None:
        return err
    try:
        rec = kya.request_unstake(kya_id)
    except KeyError:
        return _err("unknown kya", 404)
    except ValueError as exc:
        return _err(str(exc))
    return jsonify(rec)


@kya_bp.post("/unstake/finalize")
def unstake_finalize():
    body = request.get_json(silent=True) or {}
    kya_id = str(body.get("kya_id") or "")
    _, err = _guard_kya_mutation(kya_id)
    if err is not None:
        return err
    try:
        result = kya.finalize_unstake(kya_id)
    except KeyError:
        return _err("unknown kya", 404)
    except ValueError as exc:
        return _err(str(exc))
    return jsonify(result)


@kya_bp.get("/tombstones")
def tombstones():
    """Static path must sit above /<kya_id> or Flask treats 'tombstones' as an id."""
    return jsonify(kya.tombstones_snapshot())


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
