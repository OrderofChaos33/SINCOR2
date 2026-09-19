"""Genesis landing + apply + stats + quest mount."""
from __future__ import annotations

import time
from flask import Blueprint, current_app, jsonify, make_response, render_template, request, session

from sincor2.genesis.store import (
    application_number, apply_email, connect, leaderboard, mark_verified, parse_ref, stats as store_stats,
)

bp = Blueprint("genesis", __name__)
_HOLDERS_CACHE: dict[str, float | int] = {"value": 0, "ts": 0.0}
_HOLDERS_TTL = 60.0
PRIORITY_TOTAL = 1000


def _db_path() -> str:
    from sincor2.mvp_app import DB_PATH
    return DB_PATH


def _conn():
    return connect(_db_path())


def _holders() -> int:
    now = time.time()
    if now - float(_HOLDERS_CACHE["ts"]) < _HOLDERS_TTL and _HOLDERS_CACHE["value"]:
        return int(_HOLDERS_CACHE["value"])
    holders = 3455
    try:
        from sincor2.onchain.live_snapshot import get_onchain_stats
        snap = get_onchain_stats()
        holders = int((snap.get("sinc") or {}).get("holders") or holders)
    except Exception:
        pass
    _HOLDERS_CACHE["value"] = holders
    _HOLDERS_CACHE["ts"] = now
    return holders


def _base_url() -> str:
    return current_app.config.get("PUBLIC_BASE_URL") or request.host_url.rstrip("/")


@bp.route("/genesis", methods=["GET"])
def genesis_page():
    ref = request.args.get("ref")
    parsed = parse_ref(ref)
    if parsed:
        session["genesis_ref"] = parsed
        resp = make_response(render_template("genesis.html", licenses_left=PRIORITY_TOTAL, ref=application_number(parsed)))
        resp.set_cookie("genesis_ref", str(parsed), max_age=60 * 60 * 24 * 30, samesite="Lax")
        return resp
    return render_template("genesis.html", licenses_left=PRIORITY_TOTAL, ref="")


@bp.route("/api/genesis/apply", methods=["POST"])
def genesis_apply():
    body = request.get_json(silent=True) or {}
    email = str(body.get("email") or "").strip()
    ref = parse_ref(str(body.get("ref") or session.get("genesis_ref") or request.cookies.get("genesis_ref") or ""))
    conn = _conn()
    try:
        row = apply_email(conn, email, referrer_id=ref)
    except ValueError:
        return jsonify({"error": "invalid_email"}), 400
    number = application_number(int(row["id"]))
    try:
        from sincor2.email_sender import get_email_sender
        sender = get_email_sender()
        sender.send_thank_you_email(email, email.split("@")[0], "Genesis", number, {"starter": f"{_base_url()}/genesis"})
    except Exception:
        current_app.logger.warning("genesis confirmation email skipped")
    return jsonify({
        "application_number": number,
        "referral_link": f"{_base_url()}/genesis?ref={number}",
        "status": row.get("status") or "applied",
    })


@bp.route("/api/genesis/leaderboard", methods=["GET"])
def genesis_leaderboard():
    rows = leaderboard(_conn(), 100)
    return jsonify({"leaderboard": [{
        "application_number": application_number(int(r["id"])),
        "converted": int(r["converted_referrals"]),
        "priority": bool(r["priority_id"]),
        "founding": bool(r["founding_badge"]),
        "status": r["status"],
    } for r in rows]})


@bp.route("/api/genesis/stats", methods=["GET"])
def genesis_stats():
    return jsonify(store_stats(_conn(), _holders()))


@bp.route("/genesis/quest", methods=["GET"])
def genesis_quest():
    try:
        from sincor2.kya.airdrop_quest import AirdropQuest
        quest = AirdropQuest()
        return jsonify({"quest": "kya_airdrop", "mount": "/genesis/quest", "root_bound": bool(getattr(quest, "root", None))})
    except Exception as exc:
        return jsonify({"quest": "kya_airdrop", "error": str(exc)}), 200


@bp.route("/genesis/verify", methods=["POST"])
def genesis_verify():
    body = request.get_json(silent=True) or {}
    email = str(body.get("email") or "").strip().lower()
    wallet = str(body.get("wallet") or "").strip()
    conn = _conn()
    row = conn.execute("SELECT * FROM applications WHERE email=?", (email,)).fetchone()
    if not row:
        return jsonify({"error": "not_found"}), 404
    updated = mark_verified(conn, int(row["id"]), wallet or None)
    left = store_stats(conn, _holders())["licenses_left"]
    return jsonify({
        "application_number": application_number(int(updated["id"])),
        "status": updated["status"],
        "licenses_left": left,
        "permit": f"/genesis/permit/{application_number(int(updated['id'])).lstrip('#')}",
    })


@bp.route("/genesis/permit/<license_no>", methods=["GET"])
def genesis_permit(license_no: str):
    conn = _conn()
    try:
        row_id = int(license_no.lstrip("#"))
    except ValueError:
        return jsonify({"error": "bad_license"}), 400
    row = conn.execute("SELECT * FROM applications WHERE id=?", (row_id,)).fetchone()
    if not row or row["status"] != "verified":
        return jsonify({"error": "not_verified"}), 403
    from sincor2.genesis.permit import render_permit
    pdf = render_permit(license_number=application_number(int(row["id"])), wallet=row["wallet"] or "")
    resp = make_response(pdf)
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = f'attachment; filename="permit-{application_number(int(row["id"])).lstrip("#")}.pdf"'
    return resp
