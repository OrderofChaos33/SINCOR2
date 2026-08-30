"""Inbound A2A engine. Agents register, heartbeat, bid, prove; AXM settles on Base."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
import uuid
from collections import deque
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from flask import Blueprint, Flask, Response, jsonify, request, stream_with_context

from sincor2.contract_net import (
    BASE_CHAIN_ID,
    ESCROW_ADDRESS,
    calculate_bid_score,
    probe_base_chain,
    stage_payout,
)

logger = logging.getLogger("sincor.a2a.inbound")
HEARTBEAT_TTL_S = 60
AUCTION_WINDOW_MS = 500
MERIT_THRESHOLD_AXM = 5.0
MAX_AGENTS = 10000
MAX_OPEN_TASKS = 200
DEMO_SECRET = os.environ.get("SINCOR_A2A_SECRET", "sincor-a2a-demo")
_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_WALLET_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_REGISTERED = False
_FABRIC = None
_FABRIC_LOCK = threading.Lock()
_PLATFORM_AGENT_ID = "sincor-agent-swarm"
_HEARTBEAT_THREAD = None
_HEARTBEAT_STOP = threading.Event()
PROBATION_SEEDS = (
    ("lead-enrichment", 0.8),
    ("lead-enrichment", 1.2),
    ("competitor-intel", 1.5),
    ("outreach-sequence", 1.1),
    ("deal-scoring", 1.8),
    ("content-blog", 2.2),
    ("cashflow-recovery", 2.6),
    ("healthcare-credential-check", 1.4),
    ("toa-decision", 0.9),
    ("lead-enrichment", 4.2),
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _slug(value: str) -> str:
    return (re.sub(r"[^a-z0-9]+", "-", (value or "agent").lower()).strip("-") or "agent")[:80]


def sign_payload(payload: Dict[str, Any], secret: str = DEMO_SECRET) -> str:
    body = json.dumps({k: payload[k] for k in sorted(payload) if k != "signature"}, separators=(",", ":"), sort_keys=True)
    return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()


class Fabric:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.agents: Dict[str, Dict[str, Any]] = {}
        self.tasks: Dict[str, Dict[str, Any]] = {}
        self.bids: Dict[str, Dict[str, Any]] = {}
        self.proofs: Dict[str, Dict[str, Any]] = {}
        self.events: deque = deque(maxlen=500)
        self.event_seq = 0

    def publish(self, event_type: str, tags: List[str], payload: Dict[str, Any]) -> Dict[str, Any]:
        event = {"topic": "tasks:broadcast", "type": event_type, "ts": _now_ms(), "tags": list(tags), "payload": payload, "seq": 0}
        with self.lock:
            self.event_seq += 1
            event["seq"] = self.event_seq
            self.events.append(event)
        return event


def get_fabric() -> Fabric:
    global _FABRIC
    if _FABRIC is None:
        with _FABRIC_LOCK:
            if _FABRIC is None:
                _FABRIC = Fabric()
                _load_agents(_FABRIC)
    return _FABRIC


def reset_fabric() -> Fabric:
    global _FABRIC
    with _FABRIC_LOCK:
        _FABRIC = Fabric()
    return _FABRIC


def _persist_path():
    try:
        from sincor2.data_paths import data_dir
        return data_dir() / "a2a_inbound_agents.json"
    except Exception:
        return None


def _load_agents(fabric: Fabric) -> None:
    path = _persist_path()
    if path is None or not path.is_file():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        agents = raw.get("agents") if isinstance(raw, dict) else raw
        if isinstance(agents, list):
            for agent in agents:
                if isinstance(agent, dict) and agent.get("agent_id"):
                    fabric.agents[agent["agent_id"]] = agent
    except Exception as err:
        logger.warning("[A2A] restore failed: %s", err)


def _save_agents(fabric: Fabric) -> None:
    path = _persist_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"agents": list(fabric.agents.values()), "saved_at": _now_ms()}), encoding="utf-8")
    except Exception as err:
        logger.warning("[A2A] persist failed: %s", err)


def health_snapshot() -> Dict[str, Any]:
    fabric = get_fabric()
    ts = _now_ms()
    ttl_ms = HEARTBEAT_TTL_S * 1000
    with fabric.lock:
        live = sum(1 for a in fabric.agents.values() if ts - int(a.get("last_heartbeat") or 0) <= ttl_ms)
        open_auctions = sum(1 for t in fabric.tasks.values() if t.get("state") in ("open", "auction"))
        return {
            "ready": True,
            "critical": False,
            "detail": "inbound_register",
            "live_agents": live,
            "registered_agents": len(fabric.agents),
            "open_auctions": open_auctions,
            "probation_open": sum(1 for t in fabric.tasks.values() if not t.get("requires_merit") and t.get("state") in ("open", "auction")),
            "heartbeat_ttl_s": HEARTBEAT_TTL_S,
            "merit_threshold_axm": MERIT_THRESHOLD_AXM,
            "base_chain_id": BASE_CHAIN_ID,
        }


def _http_error(message: str, status: int, **extra: Any):
    body = {"error": message, "status": status}
    body.update(extra)
    return jsonify(body), status


def _safe_url(value: Any) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    return url if url and parsed.scheme in ("http", "https") and parsed.netloc else ""


def _normalize_registration(body: Dict[str, Any]) -> Dict[str, Any]:
    card = body.get("agent_card") if isinstance(body.get("agent_card"), dict) else None
    if card:
        for field in ("name", "description", "version"):
            if not card.get(field):
                raise ValueError(f"agent_card missing '{field}'")
        skills = card.get("skills") or []
        if not skills:
            raise ValueError("agent_card must include at least one skill")
        tags: List[str] = []
        for skill in skills:
            if not isinstance(skill, dict) or not skill.get("id") or not skill.get("name"):
                raise ValueError("each skill needs id and name")
            tags.append(str(skill.get("id")))
            tags.extend(str(t) for t in (skill.get("tags") or []))
        interfaces = card.get("supportedInterfaces") or []
        interface_url = str(interfaces[0].get("url") or "") if interfaces and isinstance(interfaces[0], dict) else ""
        return {
            "agent_id": str(card.get("id") or _slug(str(card.get("name")))),
            "name": str(card["name"]),
            "description": str(card.get("description") or ""),
            "version": str(card.get("version") or "0.0.0"),
            "capability_tags": sorted({t.lower() for t in tags if t}),
            "rpc_callback": _safe_url(body.get("agent_url") or interface_url),
            "wallet": str(card.get("wallet") or body.get("wallet") or ""),
            "chain_id": int(body.get("chain_id") or card.get("chain_id") or BASE_CHAIN_ID),
            "sinc_stake": int(body.get("sinc_stake") or 0),
            "skills": skills,
        }
    agent_id = str(body.get("agent_id") or _slug(str(body.get("name") or ""))).strip()
    if not agent_id:
        raise ValueError("agent_id is required")
    tags = body.get("capability_tags") or body.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    if not tags:
        raise ValueError("capability_tags required")
    return {
        "agent_id": agent_id,
        "name": str(body.get("name") or agent_id),
        "description": str(body.get("description") or ""),
        "version": str(body.get("version") or "0.1.0"),
        "capability_tags": sorted({str(t).lower() for t in tags if t}),
        "rpc_callback": _safe_url(body.get("rpc_callback") or body.get("agent_url")),
        "wallet": str(body.get("wallet") or ""),
        "chain_id": int(body.get("chain_id") or BASE_CHAIN_ID),
        "sinc_stake": int(body.get("sinc_stake") or 0),
        "skills": [{"id": t, "name": t} for t in tags],
    }
