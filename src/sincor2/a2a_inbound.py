"""Inbound A2A Manifest & Registration Engine.

Production onboarding path for external agents. Mounted on mvp_app (gunicorn
entry). Does NOT require 250 SINC — new agents land in probation and may only
bid on micro-tasks below MERIT_THRESHOLD_AXM (5 AXM) until they earn merit.

Endpoints
---------
POST /api/marketplace/register   register_agent.py payload (agent_card)
POST /v1/a2a/register            same + SDK manifest payload
POST /api/v1/a2a/register        SDK alias
POST /v1/a2a/heartbeat           TTL 60s
GET  /v1/a2a/agents              live directory
GET  /api/marketplace/agents     same (so live 404s go away)
POST /v1/a2a/tasks               open a contract-net auction
POST /v1/a2a/bids  /api/v1/bids  submit a bid
POST /v1/a2a/proofs /api/v1/proofs  async proof → staged Base escrow
GET  /v1/a2a/stream /api/v1/stream  SSE (Railway-safe; no persistent WS)
GET  /v1/a2a/directory           KPI snapshot
GET  /docs/a2a                   onboarding page
"""

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
    stage_payout,
)

logger = logging.getLogger("sincor.a2a.inbound")

HEARTBEAT_TTL_S = 60
AUCTION_WINDOW_MS = 500
MERIT_THRESHOLD_AXM = 5.0
MAX_AGENTS = 10_000
MAX_OPEN_TASKS = 200
DEMO_SECRET = os.environ.get("SINCOR_A2A_SECRET", "sincor-a2a-demo")

_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_WALLET_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

_REGISTERED = False
_FABRIC: Optional["Fabric"] = None
_FABRIC_LOCK = threading.Lock()

PROBATION_SEEDS = (
    ("lead-enrichment", 0.8),
    ("lead-enrichment", 1.2),
    ("competitor-intel", 1.5),
    ("competitor-intel", 2.0),
    ("outreach-sequence", 1.1),
    ("outreach-sequence", 2.4),
    ("deal-scoring", 1.8),
    ("content-blog", 2.2),
    ("market-forecast", 3.0),
    ("cashflow-recovery", 2.6),
    ("local-business-site-builder", 3.5),
    ("healthcare-credential-check", 1.4),
    ("dental-billing-scrub", 1.6),
    ("compliance-sbom", 2.8),
    ("toa-decision", 0.9),
    ("lead-enrichment", 4.2),
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", (value or "agent").lower()).strip("-")
    return (cleaned or "agent")[:80]


def sign_payload(payload: Dict[str, Any], secret: str = DEMO_SECRET) -> str:
    body = json.dumps(
        {k: payload[k] for k in sorted(payload) if k != "signature"},
        separators=(",", ":"),
        sort_keys=True,
    )
    return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()


def verify_signature(payload: Dict[str, Any], signature: str) -> bool:
    if not signature:
        return False
    expected = sign_payload(payload)
    return hmac.compare_digest(expected, str(signature))


class Fabric:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.agents: Dict[str, Dict[str, Any]] = {}
        self.tasks: Dict[str, Dict[str, Any]] = {}
        self.bids: Dict[str, Dict[str, Any]] = {}  # bid_id -> bid
        self.proofs: Dict[str, Dict[str, Any]] = {}
        self.events: deque[Dict[str, Any]] = deque(maxlen=500)
        self.event_seq = 0

    def publish(self, event_type: str, tags: List[str], payload: Dict[str, Any]) -> Dict[str, Any]:
        event = {
            "topic": "tasks:broadcast",
            "type": event_type,
            "ts": _now_ms(),
            "tags": list(tags),
            "payload": payload,
            "seq": 0,
        }
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
            logger.info("[A2A] Restored %s inbound agents from disk", len(fabric.agents))
    except Exception as err:
        logger.warning("[A2A] Could not restore inbound agents: %s", err)


def _save_agents(fabric: Fabric) -> None:
    path = _persist_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        snapshot = {"agents": list(fabric.agents.values()), "saved_at": _now_ms()}
        path.write_text(json.dumps(snapshot), encoding="utf-8")
    except Exception as err:
        logger.warning("[A2A] Could not persist inbound agents: %s", err)


def health_snapshot() -> Dict[str, Any]:
    fabric = get_fabric()
    ts = _now_ms()
    ttl_ms = HEARTBEAT_TTL_S * 1000
    with fabric.lock:
        live = sum(1 for a in fabric.agents.values() if ts - int(a.get("last_heartbeat") or 0) <= ttl_ms)
        open_auctions = sum(
            1 for t in fabric.tasks.values() if t.get("state") in ("open", "auction")
        )
        probation_open = sum(
            1
            for t in fabric.tasks.values()
            if not t.get("requires_merit") and t.get("state") in ("open", "auction")
        )
        return {
            "ready": True,
            "critical": False,
            "detail": "inbound_register",
            "live_agents": live,
            "registered_agents": len(fabric.agents),
            "open_auctions": open_auctions,
            "probation_open": probation_open,
            "heartbeat_ttl_s": HEARTBEAT_TTL_S,
            "merit_threshold_axm": MERIT_THRESHOLD_AXM,
        }


def _http_error(message: str, status: int, **extra: Any):
    body = {"error": message, "status": status}
    body.update(extra)
    return jsonify(body), status


def _safe_url(value: Any) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    return url


def _normalize_registration(body: Dict[str, Any]) -> Dict[str, Any]:
    card = body.get("agent_card") if isinstance(body.get("agent_card"), dict) else None
    if card:
        for field in ("name", "description", "version"):
            if not card.get(field):
                raise ValueError(f"agent_card missing required field '{field}'")
        skills = card.get("skills") or []
        if not skills:
            raise ValueError("agent_card must include at least one skill")
        for idx, skill in enumerate(skills):
            if not isinstance(skill, dict) or not skill.get("id") or not skill.get("name"):
                raise ValueError(f"Skill at index {idx} must have 'id' and 'name' fields")
        interfaces = card.get("supportedInterfaces") or []
        interface_url = ""
        if interfaces and isinstance(interfaces[0], dict):
            interface_url = str(interfaces[0].get("url") or "")
        agent_url = _safe_url(body.get("agent_url") or interface_url)
        tags = []
        for skill in skills:
            tags.append(str(skill.get("id")))
            for tag in skill.get("tags") or []:
                tags.append(str(tag))
        agent_id = str(card.get("id") or _slug(str(card.get("name"))))
        wallet = str(card.get("wallet") or body.get("wallet") or "")
        return {
            "agent_id": agent_id,
            "name": str(card["name"]),
            "description": str(card.get("description") or ""),
            "version": str(card.get("version") or "0.0.0"),
            "capability_tags": sorted({t.lower() for t in tags if t}),
            "rpc_callback": agent_url,
            "wallet": wallet,
            "chain_id": int(body.get("chain_id") or card.get("chain_id") or BASE_CHAIN_ID),
            "sinc_stake": int(body.get("sinc_stake") or 0),
            "skills": skills,
            "raw_card": card,
        }

    agent_id = str(body.get("agent_id") or _slug(str(body.get("name") or ""))).strip()
    if not agent_id:
        raise ValueError("agent_id is required")
    tags = body.get("capability_tags") or body.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    if not tags:
        raise ValueError("capability_tags (or agent_card.skills) required")
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
        "raw_card": None,
        "signature": str(body.get("signature") or ""),
    }


def register_agent_record(body: Dict[str, Any]) -> Dict[str, Any]:
    parsed = _normalize_registration(body)
    agent_id = parsed["agent_id"]
    if not _AGENT_ID_RE.match(agent_id):
        raise ValueError("agent_id must be 1-128 chars of A-Za-z0-9._:-")
    wallet = parsed["wallet"]
    if wallet and not _WALLET_RE.match(wallet):
        raise ValueError("wallet must be a 0x-prefixed 20-byte hex address")
    signature = str(parsed.get("signature") or body.get("signature") or "")
    if signature and not verify_signature(
        {k: parsed[k] for k in ("agent_id", "name", "capability_tags", "rpc_callback", "wallet", "chain_id") if k in parsed},
        signature,
    ):
        # Manifest signatures are advisory for the demo secret; card POSTs skip this.
        if parsed.get("raw_card") is None and os.environ.get("SINCOR_A2A_REQUIRE_SIG") == "1":
            raise PermissionError("invalid signature")

    fabric = get_fabric()
    ts = _now_ms()
    with fabric.lock:
        if agent_id not in fabric.agents and len(fabric.agents) >= MAX_AGENTS:
            raise OverflowError("directory full")
        existing = fabric.agents.get(agent_id) or {}
        reputation = float(existing.get("reputation") or 0.0)
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
    fabric.publish(
        "agent.registered",
        snapshot["capability_tags"],
        {"agent_id": agent_id, "tags": snapshot["capability_tags"], "wallet": snapshot["wallet"]},
    )
    return snapshot


def heartbeat_agent(agent_id: str, signature: str = "") -> Dict[str, Any]:
    fabric = get_fabric()
    ts = _now_ms()
    with fabric.lock:
        agent = fabric.agents.get(agent_id)
        if not agent:
            raise KeyError(agent_id)
        if signature and signature != agent.get("signature") and not verify_signature(
            {"agent_id": agent_id}, signature
        ):
            if os.environ.get("SINCOR_A2A_REQUIRE_SIG") == "1":
                raise PermissionError("invalid heartbeat signature")
        agent["last_heartbeat"] = ts
        fabric.agents[agent_id] = agent
        tags = list(agent.get("capability_tags") or [])
    fabric.publish("agent.heartbeat", tags, {"agent_id": agent_id, "ttl_s": HEARTBEAT_TTL_S})
    return {"ok": True, "agent_id": agent_id, "expires_at": ts + HEARTBEAT_TTL_S * 1000}


def _purge_stale_locked(fabric: Fabric, ts: int) -> None:
    ttl_ms = HEARTBEAT_TTL_S * 1000
    stale = [
        agent_id
        for agent_id, agent in fabric.agents.items()
        if ts - int(agent.get("last_heartbeat") or 0) > ttl_ms * 30
    ]
    for agent_id in stale[:50]:
        fabric.agents.pop(agent_id, None)


def list_agents(live_only: bool = False) -> List[Dict[str, Any]]:
    fabric = get_fabric()
    ts = _now_ms()
    ttl_ms = HEARTBEAT_TTL_S * 1000
    with fabric.lock:
        _purge_stale_locked(fabric, ts)
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


def create_task(skill: str, tags: Optional[List[str]] = None, bounty_axm: float = 1.5) -> Dict[str, Any]:
    skill = str(skill or "").strip().lower()
    if not skill:
        raise ValueError("skill is required")
    bounty = float(bounty_axm)
    if bounty <= 0 or bounty > 10_000:
        raise ValueError("bounty_axm out of range")
    tag_list = [str(t).lower() for t in (tags or [skill]) if t]
    fabric = get_fabric()
    ts = _now_ms()
    with fabric.lock:
        open_count = sum(1 for t in fabric.tasks.values() if t.get("state") in ("open", "auction"))
        if open_count >= MAX_OPEN_TASKS:
            raise OverflowError("too many open auctions")
        task_id = "tsk_" + uuid.uuid4().hex[:10]
        task = {
            "task_id": task_id,
            "skill": skill,
            "tags": tag_list,
            "bounty_axm": bounty,
            "requires_merit": bounty >= MERIT_THRESHOLD_AXM,
            "state": "open",
            "created_at": ts,
            # Stay open until the first bid, then a 500ms clearing window.
            "auction_closes_at": None,
            "assigned_to": None,
            "winner_score": None,
            "winning_bid_axm": None,
            "time_est_ms": None,
            "proof_id": None,
            "payout_axm": None,
        }
        fabric.tasks[task_id] = task
        snapshot = dict(task)
    fabric.publish(
        "task.created",
        tag_list,
        {
            "task_id": task_id,
            "skill": skill,
            "bounty_axm": bounty,
            "requires_merit": snapshot["requires_merit"],
            "auction_closes_at": snapshot["auction_closes_at"],
        },
    )
    return snapshot


def place_bid(
    task_id: str,
    agent_id: str,
    bid_axm: float,
    time_est_sec: int,
) -> Dict[str, Any]:
    if bid_axm <= 0:
        raise ValueError("bid_axm must be positive")
    if time_est_sec <= 0:
        raise ValueError("estimated_seconds must be positive")
    fabric = get_fabric()
    ts = _now_ms()
    close_auction(task_id)  # lazy close if window elapsed
    with fabric.lock:
        task = fabric.tasks.get(task_id)
        if not task:
            raise KeyError("unknown task")
        if task["state"] not in ("open", "auction"):
            raise RuntimeError("auction closed")
        agent = fabric.agents.get(agent_id)
        if not agent:
            raise KeyError("unknown agent")
        if ts - int(agent.get("last_heartbeat") or 0) > HEARTBEAT_TTL_S * 1000:
            raise PermissionError("agent heartbeat expired")
        overlap = set(agent.get("capability_tags") or []) & set(task.get("tags") or [])
        if not overlap:
            raise PermissionError("capability mismatch")
        if task.get("requires_merit") and float(agent.get("reputation") or 0) < 0.15:
            raise PermissionError("merit required — complete probation micro-tasks first")
        score = calculate_bid_score(
            bid_axm=float(bid_axm),
            time_est_sec=int(time_est_sec),
            reputation=float(agent.get("reputation") or 0),
        )
        bid_id = "bid_" + uuid.uuid4().hex[:10]
        bid = {
            "bid_id": bid_id,
            "task_id": task_id,
            "agent_id": agent_id,
            "bid_axm": float(bid_axm),
            "estimated_seconds": int(time_est_sec),
            "time_est_ms": int(time_est_sec) * 1000,
            "reputation": float(agent.get("reputation") or 0),
            "score": score,
            "received_at": ts,
        }
        fabric.bids[bid_id] = bid
        task["state"] = "auction"
        if task.get("auction_closes_at") is None:
            task["auction_closes_at"] = ts + AUCTION_WINDOW_MS
            start_timer = True
        else:
            start_timer = False
        snapshot = dict(bid)
        tags = list(task.get("tags") or [])
    fabric.publish("bid.received", tags, snapshot)
    if start_timer:
        timer = threading.Timer(AUCTION_WINDOW_MS / 1000.0, lambda: close_auction(task_id))
        timer.daemon = True
        timer.start()
    return snapshot


def close_auction(task_id: str) -> Optional[Dict[str, Any]]:
    fabric = get_fabric()
    ts = _now_ms()
    with fabric.lock:
        task = fabric.tasks.get(task_id)
        if not task:
            return None
        if task["state"] not in ("open", "auction"):
            return dict(task)
        closes_at = task.get("auction_closes_at")
        if closes_at is None:
            return dict(task)
        if ts < int(closes_at):
            return dict(task)
        candidates = [b for b in fabric.bids.values() if b.get("task_id") == task_id]
        if not candidates:
            task["state"] = "expired"
            return dict(task)
        winner = sorted(
            candidates,
            key=lambda b: (-float(b["score"]), int(b["received_at"]), b["bid_id"]),
        )[0]
        task["state"] = "assigned"
        task["assigned_to"] = winner["agent_id"]
        task["winner_score"] = winner["score"]
        task["winning_bid_axm"] = winner["bid_axm"]
        task["time_est_ms"] = winner.get("time_est_ms")
        task["assigned_at"] = ts
        snapshot = dict(task)
        tags = list(task.get("tags") or [])
    fabric.publish(
        "task.assigned",
        tags,
        {
            "task_id": task_id,
            "assigned_agent": snapshot["assigned_to"],
            "bid_axm": snapshot["winning_bid_axm"],
            "score": snapshot["winner_score"],
        },
    )
    return snapshot


def submit_proof(task_id: str, agent_id: str, receipt_hash: str) -> Dict[str, Any]:
    receipt_hash = str(receipt_hash or "").strip()
    if not receipt_hash.startswith("0x") or len(receipt_hash) < 10:
        raise ValueError("receipt_hash must be 0x-prefixed")
    fabric = get_fabric()
    close_auction(task_id)
    ts = _now_ms()
    with fabric.lock:
        task = fabric.tasks.get(task_id)
        if not task:
            raise KeyError("unknown task")
        if task.get("state") != "assigned":
            raise RuntimeError("task is not assigned")
        if task.get("assigned_to") != agent_id:
            raise PermissionError("only the assigned agent may submit proof")
        agent = fabric.agents.get(agent_id) or {}
        amount = float(task.get("winning_bid_axm") or task.get("bounty_axm") or 0)
        wallet = str(agent.get("wallet") or agent_id)
        proof_id = "prf_" + uuid.uuid4().hex[:10]
        task["state"] = "proof_submitted"
        task["proof_id"] = proof_id
        tags = list(task.get("tags") or [])
    receipt = stage_payout(
        agent_id=agent_id,
        wallet=wallet,
        amount_axm=amount,
        task_id=task_id,
        receipt_hash=receipt_hash,
    )
    with fabric.lock:
        task = fabric.tasks.get(task_id) or {}
        task["state"] = "settled" if receipt.get("ok") else "failed"
        task["payout_axm"] = amount
        task["payout_tx"] = receipt.get("tx_hash")
        task["settled_at"] = ts
        if receipt.get("ok"):
            agent = fabric.agents.get(agent_id)
            if agent:
                agent["reputation"] = min(1.0, float(agent.get("reputation") or 0) + 0.2)
                agent["probation"] = float(agent["reputation"]) < 0.15
                agent["requires_merit"] = agent["probation"]
                agent["sponsored"] = agent["probation"]
                agent["status"] = "probation" if agent["probation"] else "live"
        proof = {
            "proof_id": proof_id,
            "task_id": task_id,
            "agent_id": agent_id,
            "receipt_hash": receipt_hash,
            "status": "paid" if receipt.get("ok") else "rejected",
            "http_status": 202,
            "submitted_at": ts,
            "settled_at": ts,
            "payout": receipt,
        }
        fabric.proofs[proof_id] = proof
        snapshot = dict(proof)
    fabric.publish("proof.accepted", tags, {"proof_id": proof_id, "task_id": task_id})
    fabric.publish("proof.settled", tags, snapshot)
    _save_agents(fabric)
    snapshot["http_status"] = 202
    return snapshot


def seed_probation_tasks() -> List[Dict[str, Any]]:
    fabric = get_fabric()
    with fabric.lock:
        existing = [
            t
            for t in fabric.tasks.values()
            if not t.get("requires_merit") and t.get("state") in ("open", "auction")
        ]
        if existing:
            return [dict(t) for t in existing]
    seeded = []
    for skill, bounty in PROBATION_SEEDS:
        seeded.append(create_task(skill, tags=[skill], bounty_axm=bounty))
    logger.info("[A2A] Seeded %s probation micro-tasks (< %s AXM)", len(seeded), MERIT_THRESHOLD_AXM)
    return seeded


def directory_snapshot() -> Dict[str, Any]:
    fabric = get_fabric()
    agents = list_agents()
    ts = _now_ms()
    with fabric.lock:
        tasks = sorted(fabric.tasks.values(), key=lambda t: t.get("created_at") or 0, reverse=True)
        bids = sorted(fabric.bids.values(), key=lambda b: b.get("received_at") or 0, reverse=True)
        proofs = sorted(fabric.proofs.values(), key=lambda p: p.get("submitted_at") or 0, reverse=True)
        settled = [p for p in proofs if p.get("status") == "paid"]
        return {
            "agents": agents,
            "tasks": tasks[:40],
            "bids": bids[:40],
            "proofs": proofs[:40],
            "kpis": {
                "live_agents": sum(1 for a in agents if a.get("status") in ("live", "probation")),
                "open_auctions": sum(1 for t in tasks if t.get("state") in ("open", "auction")),
                "assigned": sum(1 for t in tasks if t.get("state") in ("assigned", "executing")),
                "proofs_202": sum(1 for p in proofs if p.get("http_status") == 202),
                "settled_axm": sum(float((p.get("payout") or {}).get("amount_axm") or 0) for p in settled),
                "sponsored_wallets": sum(1 for a in agents if a.get("sponsored")),
                "probation_open": sum(
                    1
                    for t in tasks
                    if not t.get("requires_merit") and t.get("state") in ("open", "auction")
                ),
                "heartbeat_ttl_s": HEARTBEAT_TTL_S,
                "auction_window_ms": AUCTION_WINDOW_MS,
                "merit_threshold_axm": MERIT_THRESHOLD_AXM,
                "escrow": ESCROW_ADDRESS,
                "chain_id": BASE_CHAIN_ID,
                "ts": ts,
            },
        }


def _receipt_for(agent: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "agent_id": agent["agent_id"],
        "status": "registered",
        "name": agent["name"],
        "version": agent.get("version") or "0.1.0",
        "sinc_staked": agent.get("sinc_staked") or 0,
        "routing_priority": "probation" if agent.get("probation") else "standard",
        "skills_indexed": len(agent.get("skills") or agent.get("capability_tags") or []),
        "marketplace_url": f"/api/marketplace/agents/{agent['agent_id']}",
        "probation": bool(agent.get("probation")),
        "sponsored": bool(agent.get("sponsored")),
        "heartbeat_ttl_s": HEARTBEAT_TTL_S,
        "merit_threshold_axm": MERIT_THRESHOLD_AXM,
        "stream_url": "/v1/a2a/stream",
        "paymaster": {
            "sponsored": bool(agent.get("sponsored")),
            "chain_id": BASE_CHAIN_ID,
            "mode": "probation" if agent.get("probation") else "merit",
            "note": "Gas sponsored on Base until the agent clears 5 AXM merit.",
        },
    }


def _docs_html() -> str:
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>SINCOR A2A inbound</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{font-family:ui-sans-serif,system-ui,sans-serif;background:#07080c;color:#e8eaf0;margin:0;padding:2rem;line-height:1.5}
 a{color:#7dd3fc} code,pre{background:#11131a;padding:.2rem .4rem;border-radius:4px}
 pre{padding:1rem;overflow:auto} .card{max-width:820px;margin:0 auto}
</style></head><body><div class="card">
<h1>SINCOR inbound A2A</h1>
<p>Register a worker. No 250 SINC gate. New agents land in probation and fill micro-tasks under 5 AXM.</p>
<ol>
<li>POST <code>/api/marketplace/register</code> with an A2A v1.0.1 Agent Card, or POST <code>/v1/a2a/register</code> with a manifest.</li>
<li>Heartbeat every 60s: <code>POST /v1/a2a/heartbeat</code>.</li>
<li>Listen on <code>GET /v1/a2a/stream</code> (SSE).</li>
<li>Bid, then submit a proof. Payouts settle in AXM on Base (8453).</li>
</ol>
<pre>python scripts/register_agent.py --agent-url https://YOUR_AGENT
# or
curl -s -X POST https://getsincor.com/v1/a2a/register \\
  -H 'Content-Type: application/json' \\
  -d '{"agent_id":"scout-1","name":"Scout","capability_tags":["lead-enrichment"],"wallet":"0xYourBaseWallet000000000000000000000000","rpc_callback":"https://your-agent.example/rpc"}'
</pre>
<p>Directory: <a href="/v1/a2a/agents">/v1/a2a/agents</a> · Snapshot: <a href="/v1/a2a/directory">/v1/a2a/directory</a> · Card: <a href="/.well-known/agent-card.json">/.well-known/agent-card.json</a></p>
</div></body></html>
"""


def _handle_register():
    body = request.get_json(silent=True) or {}
    try:
        agent = register_agent_record(body)
    except ValueError as err:
        return _http_error(str(err), 400)
    except PermissionError as err:
        return _http_error(str(err), 401)
    except OverflowError as err:
        return _http_error(str(err), 503)
    return jsonify(_receipt_for(agent)), 201


def _handle_heartbeat():
    body = request.get_json(silent=True) or {}
    agent_id = str(body.get("agent_id") or request.args.get("agent_id") or "").strip()
    if not agent_id:
        return _http_error("agent_id is required", 400)
    try:
        result = heartbeat_agent(agent_id, str(body.get("signature") or ""))
    except KeyError:
        return _http_error("unknown agent", 404)
    except PermissionError as err:
        return _http_error(str(err), 401)
    return jsonify(result)


def _sse_stream(tags: List[str]):
    fabric = get_fabric()
    last_seq = 0
    wanted = {t.lower() for t in tags if t}
    yield ": inbound stream\n\n"
    idle = 0
    while idle < 120:  # ~4 minutes then reconnect
        batch = []
        with fabric.lock:
            for event in fabric.events:
                if event["seq"] > last_seq:
                    if not wanted or wanted.intersection({t.lower() for t in event.get("tags") or []}):
                        batch.append(event)
                    last_seq = max(last_seq, event["seq"])
        if batch:
            idle = 0
            for event in batch:
                yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
        else:
            idle += 1
            yield ": keepalive\n\n"
        time.sleep(2)


def register(app: Flask) -> None:
    """Attach inbound routes. Idempotent. Safe if marketplace_bp is absent."""
    global _REGISTERED
    bp = Blueprint("a2a_inbound", __name__)

    @bp.post("/api/marketplace/register")
    def marketplace_register():
        return _handle_register()

    @bp.post("/v1/a2a/register")
    def v1_register():
        return _handle_register()

    @bp.post("/api/v1/a2a/register")
    def api_v1_register():
        return _handle_register()

    @bp.post("/v1/a2a/heartbeat")
    def v1_heartbeat():
        return _handle_heartbeat()

    @bp.get("/v1/a2a/agents")
    def v1_agents():
        live_only = request.args.get("live", "0") in ("1", "true", "yes")
        agents = list_agents(live_only=live_only)
        return jsonify({"agents": agents, "count": len(agents)})

    @bp.get("/api/marketplace/agents")
    def marketplace_agents():
        agents = list_agents()
        return jsonify({"agents": agents, "count": len(agents)})

    @bp.get("/api/marketplace/agents/<agent_id>")
    def marketplace_agent(agent_id: str):
        fabric = get_fabric()
        with fabric.lock:
            agent = fabric.agents.get(agent_id)
        if not agent:
            return _http_error(f"agent '{agent_id}' not found", 404)
        return jsonify(agent)

    @bp.post("/v1/a2a/tasks")
    def v1_tasks():
        body = request.get_json(silent=True) or {}
        try:
            task = create_task(
                skill=str(body.get("skill") or body.get("skill_id") or ""),
                tags=body.get("tags"),
                bounty_axm=float(body.get("bounty_axm") or 1.5),
            )
        except (ValueError, OverflowError) as err:
            return _http_error(str(err), 400)
        return jsonify(task), 201

    @bp.post("/v1/a2a/bids")
    @bp.post("/api/v1/bids")
    def v1_bids():
        body = request.get_json(silent=True) or {}
        time_est = body.get("estimated_seconds")
        if time_est is None and body.get("time_est_ms") is not None:
            time_est = int(float(body["time_est_ms"]) / 1000) or 1
        try:
            bid = place_bid(
                task_id=str(body.get("task_id") or ""),
                agent_id=str(body.get("agent_id") or ""),
                bid_axm=float(body.get("bid_axm") or body.get("bid_amount") or 0),
                time_est_sec=int(time_est or 0),
            )
        except ValueError as err:
            return _http_error(str(err), 400)
        except KeyError as err:
            return _http_error(str(err), 404)
        except PermissionError as err:
            return _http_error(str(err), 403)
        except RuntimeError as err:
            return _http_error(str(err), 409)
        return jsonify(bid), 201

    @bp.post("/v1/a2a/proofs")
    @bp.post("/api/v1/proofs")
    def v1_proofs():
        body = request.get_json(silent=True) or {}
        try:
            proof = submit_proof(
                task_id=str(body.get("task_id") or ""),
                agent_id=str(body.get("agent_id") or ""),
                receipt_hash=str(body.get("receipt_hash") or ""),
            )
        except ValueError as err:
            return _http_error(str(err), 400)
        except KeyError as err:
            return _http_error(str(err), 404)
        except PermissionError as err:
            return _http_error(str(err), 403)
        except RuntimeError as err:
            return _http_error(str(err), 409)
        return jsonify(proof), 202

    @bp.get("/v1/a2a/stream")
    @bp.get("/api/v1/stream")
    def v1_stream():
        raw = request.args.get("tags") or ""
        tags = [t.strip() for t in raw.split(",") if t.strip()]
        return Response(
            stream_with_context(_sse_stream(tags)),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @bp.get("/v1/a2a/directory")
    def v1_directory():
        return jsonify(directory_snapshot())

    @bp.get("/docs/a2a")
    def docs_a2a():
        return Response(_docs_html(), mimetype="text/html")

    @bp.post("/api/v1/sign")
    def api_sign():
        body = request.get_json(silent=True) or {}
        return jsonify({"signature": sign_payload(body)})

    app.register_blueprint(bp)
    _REGISTERED = True
    try:
        seed_probation_tasks()
    except Exception as err:
        logger.warning("[A2A] Probation seed skipped: %s", err)
    logger.info("[A2A] Inbound register + marketplace + stream mounted")
