"""A2A inbound routes + agent operations. Imported by a2a_inbound.register."""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, List

from flask import Flask, jsonify, request

from sincor2.contract_net import BASE_CHAIN_ID, probe_base_chain
from sincor2.a2a_adoption_metrics import record_agent_registered
from sincor2.a2a_inbound import (
    HEARTBEAT_TTL_S,
    MAX_AGENTS,
    MERIT_THRESHOLD_AXM,
    _AGENT_ID_RE,
    _HEARTBEAT_STOP,
    _PLATFORM_AGENT_ID,
    _WALLET_RE,
    _http_error,
    _normalize_registration,
    _now_ms,
    _save_agents,
    get_fabric,
    health_snapshot,
)

logger = logging.getLogger("sincor.a2a.inbound")
_HEARTBEAT_THREAD = None


def register_agent_record(body: Dict[str, Any]) -> Dict[str, Any]:
    parsed = _normalize_registration(body)
    agent_id = parsed["agent_id"]
    if not _AGENT_ID_RE.match(agent_id):
        raise ValueError("agent_id must be 1-128 chars of A-Za-z0-9._:-")
    wallet = parsed["wallet"]
    if wallet and not _WALLET_RE.match(wallet):
        raise ValueError("wallet must be a 0x-prefixed 20-byte hex address")
    fabric = get_fabric()
    ts = _now_ms()
    with fabric.lock:
        if agent_id not in fabric.agents and len(fabric.agents) >= MAX_AGENTS:
            raise OverflowError("directory full")
        existing = fabric.agents.get(agent_id) or {}
        reputation = float(body["reputation"]) if body.get("reputation") is not None else float(existing.get("reputation") or 0.0)
        agent = {
            "agent_id": agent_id,
            "name": parsed["name"],
            "description": parsed["description"],
            "version": parsed["version"],
            "capability_tags": parsed["capability_tags"],
            "skills": parsed["skills"],
            "rpc_callback": parsed["rpc_callback"],
            "wallet": wallet.lower() if wallet else "",
            "chain_id": parsed["chain_id"],
            "sinc_staked": parsed["sinc_stake"],
            "reputation": reputation,
            "sponsored": bool(existing.get("sponsored", True)),
            "requires_merit": reputation < 0.15,
            "probation": reputation < 0.15,
            "last_heartbeat": ts,
            "registered_at": int(existing.get("registered_at") or ts),
            "status": "probation" if reputation < 0.15 else "live",
        }
        fabric.agents[agent_id] = agent
        snapshot = dict(agent)
    _save_agents(fabric)
    fabric.publish("agent.registered", snapshot["capability_tags"], {"agent_id": agent_id, "tags": snapshot["capability_tags"], "wallet": snapshot["wallet"]})
    try:
        record_agent_registered(
            agent_id=agent_id,
            wallet=snapshot.get("wallet", ""),
            tags=list(snapshot.get("capability_tags") or []),
            probation=bool(snapshot.get("probation")),
        )
    except Exception as exc:
        logger.warning("[A2A] adoption metric register event failed: %s", exc)
    return snapshot


def heartbeat_agent(agent_id: str, signature: str = "") -> Dict[str, Any]:
    fabric = get_fabric()
    ts = _now_ms()
    with fabric.lock:
        agent = fabric.agents.get(agent_id)
        if not agent:
            raise KeyError(agent_id)
        agent["last_heartbeat"] = ts
        tags = list(agent.get("capability_tags") or [])
    fabric.publish("agent.heartbeat", tags, {"agent_id": agent_id, "ttl_s": HEARTBEAT_TTL_S})
    return {"ok": True, "agent_id": agent_id, "expires_at": ts + HEARTBEAT_TTL_S * 1000}


def ensure_platform_agent() -> Dict[str, Any]:
    base = (os.environ.get("PUBLIC_BASE_URL") or os.environ.get("SITE_URL") or "https://getsincor.com").rstrip("/")
    snapshot = register_agent_record({
        "agent_id": _PLATFORM_AGENT_ID,
        "name": "SINCOR Agent Swarm",
        "description": "Native SINCOR execution surface on getsincor.com",
        "version": "2.0.0",
        "capability_tags": ["lead-enrichment", "outreach-sequence", "competitor-intel", "axiom-payment", "x402"],
        "rpc_callback": f"{base}/api/a2a",
        "wallet": os.environ.get("TREASURY_ADDRESS", "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"),
        "chain_id": BASE_CHAIN_ID,
        "reputation": 1.0,
    })
    _start_platform_heartbeat()
    logger.info("[A2A] Platform agent live id=%s status=%s", snapshot.get("agent_id"), snapshot.get("status"))
    return snapshot


def _start_platform_heartbeat() -> None:
    global _HEARTBEAT_THREAD
    if _HEARTBEAT_THREAD and _HEARTBEAT_THREAD.is_alive():
        return

    def _loop() -> None:
        while not _HEARTBEAT_STOP.wait(HEARTBEAT_TTL_S / 2):
            try:
                heartbeat_agent(_PLATFORM_AGENT_ID)
            except Exception:
                try:
                    ensure_platform_agent()
                except Exception as seed_err:
                    logger.warning("[A2A] platform reseed failed: %s", seed_err)

    _HEARTBEAT_THREAD = threading.Thread(target=_loop, name="sincor-platform-heartbeat", daemon=True)
    _HEARTBEAT_THREAD.start()


def list_agents(live_only: bool = False) -> List[Dict[str, Any]]:
    fabric = get_fabric()
    ts = _now_ms()
    ttl_ms = HEARTBEAT_TTL_S * 1000
    with fabric.lock:
        out = []
        for agent in fabric.agents.values():
            age = ts - int(agent.get("last_heartbeat") or 0)
            status = "live" if age <= ttl_ms else "stale"
            if agent.get("probation") and status == "live":
                status = "probation"
            if live_only and status == "stale":
                continue
            row = dict(agent)
            row["status"] = status
            row["heartbeat_age_ms"] = age
            out.append(row)
        return sorted(out, key=lambda a: a.get("registered_at") or 0, reverse=True)


def mount(app: Flask) -> None:
    from flask import Blueprint
    from sincor2.a2a_inbound_market import attach_market_routes, seed_probation_tasks

    bp = Blueprint("a2a_inbound", __name__)

    @bp.post("/api/marketplace/register")
    @bp.post("/v1/a2a/register")
    @bp.post("/api/v1/a2a/register")
    def v1_register():
        body = request.get_json(silent=True) or {}
        try:
            agent = register_agent_record(body)
        except ValueError as err:
            return _http_error(str(err), 400)
        except OverflowError as err:
            return _http_error(str(err), 503)
        return jsonify({
            "agent_id": agent["agent_id"],
            "status": "registered",
            "name": agent["name"],
            "probation": bool(agent.get("probation")),
            "heartbeat_ttl_s": HEARTBEAT_TTL_S,
            "stream_url": "/v1/a2a/stream",
        }), 201

    @bp.post("/v1/a2a/heartbeat")
    def v1_heartbeat():
        body = request.get_json(silent=True) or {}
        agent_id = str(body.get("agent_id") or request.args.get("agent_id") or "").strip()
        if not agent_id:
            return _http_error("agent_id is required", 400)
        try:
            return jsonify(heartbeat_agent(agent_id))
        except KeyError:
            return _http_error("unknown agent", 404)

    @bp.get("/v1/a2a/agents")
    @bp.get("/api/marketplace/agents")
    def v1_agents():
        agents = list_agents(live_only=request.args.get("live", "0") in ("1", "true", "yes"))
        return jsonify({"agents": agents, "count": len(agents)})

    @bp.get("/v1/a2a/directory")
    def v1_directory():
        return jsonify({"agents": list_agents(), "kpis": health_snapshot()})

    @bp.get("/v1/a2a/chain")
    def v1_chain():
        return jsonify(probe_base_chain())

    attach_market_routes(bp)
    app.register_blueprint(bp)
    try:
        ensure_platform_agent()
    except Exception as err:
        logger.warning("[A2A] Platform agent seed skipped: %s", err)
    try:
        seeded = seed_probation_tasks()
        logger.info("[A2A] Seeded %s probation auctions", len(seeded))
    except Exception as err:
        logger.warning("[A2A] Probation seed skipped: %s", err)
    logger.info("[A2A] Inbound register + market + heartbeat mounted")
