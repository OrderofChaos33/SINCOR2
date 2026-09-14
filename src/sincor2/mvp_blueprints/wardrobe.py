"""Wardrobe, agent cards, SKU quotes, heartbeat."""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path

import yaml
from flask import Blueprint, Response, abort, jsonify, request

from sincor2.wardrobe.ids import valid_agent_id, wardrobe_path

bp = Blueprint("mvp_wardrobe", __name__)

ROOT = Path(__file__).resolve().parents[3]


def _token() -> str:
    return (os.environ.get("AGENT_HEARTBEAT_TOKEN") or "").strip()


def _check_heartbeat_auth() -> bool:
    expected = _token()
    if not expected:
        return False
    got = (
        request.headers.get("X-Sincor-Heartbeat")
        or request.headers.get("Authorization", "").removeprefix("Bearer ")
        or ""
    ).strip()
    if not got:
        return False
    return hmac.compare_digest(
        hashlib.sha256(got.encode()).digest(),
        hashlib.sha256(expected.encode()).digest(),
    )


def _docs() -> list[dict]:
    out = []
    for path in sorted((ROOT / "agents").glob("E-*.yaml")):
        if not valid_agent_id(path.stem):
            continue
        doc = yaml.safe_load(path.read_text())
        if isinstance(doc, dict) and "identity" in doc:
            out.append(doc)
    return out


def _doc(agent_id: str) -> dict | None:
    path = wardrobe_path(ROOT, agent_id)
    if path is None:
        return None
    doc = yaml.safe_load(path.read_text())
    return doc if isinstance(doc, dict) else None


@bp.route("/api/sku/quote")
def sku_quote():
    from sincor2.wardrobe.quote import quote_sku

    sku = request.args.get("sku") or ""
    asset = request.args.get("asset") or "USDC"
    return jsonify(quote_sku(sku, asset, axm_spot_usd=None))


@bp.route("/api/a2a/heartbeat", methods=["POST"])
def heartbeat():
    from sincor2.wardrobe.heartbeat import record_heartbeat

    if not _check_heartbeat_auth():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    agent_id = data.get("agent_id") or ""
    if not valid_agent_id(agent_id):
        return jsonify({"ok": False, "error": "invalid_agent_id"}), 400
    if _doc(agent_id) is None:
        return jsonify({"ok": False, "error": "unknown_agent"}), 404
    row = record_heartbeat(agent_id, status=data.get("status") or "WardrobeDraft")
    return jsonify({"ok": True, "beat": row})


@bp.route("/api/a2a/agents")
def agents_index():
    from sincor2.wardrobe.cards import swarm_index

    return jsonify(swarm_index(_docs()))


@bp.route("/.well-known/agents/<agent_id>.json")
def well_known_agent(agent_id: str):
    from sincor2.wardrobe.cards import machine_card

    doc = _doc(agent_id)
    if not doc:
        abort(404)
    return jsonify(machine_card(doc))


@bp.route("/agents")
def agents_page():
    from html import escape

    from sincor2.wardrobe.cards import swarm_index

    idx = swarm_index(_docs())
    rows = "".join(
        f"<tr><td><a href='/agents/{escape(a['id'])}'>{escape(a['name'])}</a></td>"
        f"<td>{escape(a['archetype'])}</td><td>{escape(a['status'])}</td>"
        f"<td>{int(a['trust'])}</td></tr>"
        for a in idx["agents"]
    )
    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'/><title>SINCOR roster</title>"
        "<style>body{font-family:Inter,system-ui;margin:40px;color:#1A1A1A}"
        "table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:8px;text-align:left}"
        "a{color:#0E6B6B}</style></head><body>"
        "<p><a href='/'>SINCOR</a></p><h1>Public roster</h1>"
        f"<p>{int(idx['loaded'])} of {int(idx['rosterPublic'])} public agents loaded. Command layer is internal.</p>"
        f"<table><tr><th>Agent</th><th>Archetype</th><th>Status</th><th>Trust</th></tr>{rows}</table>"
        "</body></html>"
    )
    return Response(html, mimetype="text/html")


@bp.route("/agents/<agent_id>")
def agent_page(agent_id: str):
    from sincor2.wardrobe.cards import human_card_html

    doc = _doc(agent_id)
    if not doc:
        abort(404)
    return Response(human_card_html(doc), mimetype="text/html")
