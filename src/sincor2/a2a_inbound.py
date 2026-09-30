"""Inbound A2A engine. Agents register, heartbeat, bid, prove; AXM-denominated
accounting, with user-initiated transfers verified on Base."""
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
from pathlib import Path
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

REPUTATION_MERIT_THRESHOLD = 0.15
"""Reputation at or above this clears probation / requires_merit.

Reputation is earned-only: it is never accepted from a registration
request body. New agents start at 0.0; settlement increments it;
ghosting and upheld quality disputes reduce it. KYA verifies identity,
not competence, so KYA status never feeds this number.
"""


def _apply_reputation(agent: Dict[str, Any], new_value: float) -> float:
    """Set earned reputation and recompute probation / merit / status.

    Single source of truth for the merit threshold so registration,
    settlement, ghosting, and adjudication cannot drift apart.
    Clamps to [0.0, 1.0]. Returns the applied value.
    """
    rep = max(0.0, min(1.0, float(new_value or 0.0)))
    agent["reputation"] = rep
    probation = rep < REPUTATION_MERIT_THRESHOLD
    agent["probation"] = probation
    agent["requires_merit"] = probation
    agent["status"] = "probation" if probation else "live"
    return rep
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


# Sealed-bid seeds (2026-09-26): a dozen tasks across the four target
# verticals, created with the commit/reveal shim.  (skill_id, bounty_axm)
SEALED_PROBATION_SEEDS = (
    # WebBuilder local sites
    ("local-business-site-builder", 2.5),
    ("local-business-site-builder", 3.0),
    ("local-business-site-builder", 1.8),
    # Auto detailing
    ("detailing-booking", 1.2),
    ("detailing-presence", 1.6),
    ("detailing-lead-ingest", 1.0),
    # Lead enrichment / outbound
    ("lead-enrichment", 1.4),
    ("outreach-sequence", 1.1),
    ("deal-scoring", 1.8),
    # Healthcare credentialing / RCM
    ("healthcare-credential-check", 2.2),
    ("dental-billing-scrub", 1.9),
    ("cashflow-recovery", 2.6),
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
        # Sealed-bid shim: (task_id, agent_id) -> commitment record. Keyed
        # with a NUL separator; see a2a_inbound_market.commit_bid.
        self.commits: Dict[str, Dict[str, Any]] = {}
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
                _load_tasks(_FABRIC)
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


def _tasks_persist_path():
    """File backing the task listings. ``SINCOR_A2A_TASKS_PATH`` overrides
    for tests; otherwise the persistent data dir (``SINCOR_DATA_DIR``)."""
    override = os.environ.get("SINCOR_A2A_TASKS_PATH", "").strip()
    if override:
        return Path(override)
    try:
        from sincor2.data_paths import data_dir
        return data_dir() / "a2a_inbound_tasks.json"
    except Exception:
        return None


def _tasks_persist_enabled() -> bool:
    """Write-through task persistence.

    On in production, and whenever ``SINCOR_A2A_TASKS_PATH`` explicitly
    opts in. Off by default under FLASK_ENV/ENVIRONMENT=test so existing
    suites keep full in-memory isolation (mirrors the platform_bootstrap
    temp-registry convention); persistence tests set the override.
    """
    if os.environ.get("SINCOR_A2A_TASKS_PATH", "").strip():
        return True
    env = (os.environ.get("FLASK_ENV") or os.environ.get("ENVIRONMENT") or "production").strip().lower()
    return env not in {"test", "testing"}


def _load_tasks(fabric: Fabric) -> None:
    """Restore task listings into a fresh fabric (boot path only)."""
    if not _tasks_persist_enabled():
        return
    path = _tasks_persist_path()
    if path is None or not path.is_file():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        tasks = raw.get("tasks") if isinstance(raw, dict) else raw
        items = tasks.values() if isinstance(tasks, dict) else tasks
        if items:
            for task in items:
                if isinstance(task, dict) and task.get("task_id"):
                    fabric.tasks[task["task_id"]] = task
    except Exception as err:
        logger.warning("[A2A] task restore failed: %s", err)


def _save_tasks(fabric: Fabric) -> None:
    """Write-through task persistence (atomic tmp + os.replace).

    Call outside ``fabric.lock`` after a task mutation, mirroring
    ``_save_agents``. Never raises: a failed write is logged, never fatal.
    """
    if not _tasks_persist_enabled():
        return
    path = _tasks_persist_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps({"tasks": list(fabric.tasks.values()), "saved_at": _now_ms()}),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except Exception as err:
        logger.warning("[A2A] task persist failed: %s", err)


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


class RegistrationAuthError(Exception):
    """Re-registration without valid proof of control (maps to HTTP 403)."""


# Re-registration proof (G2.2): the owner of an existing agent record proves
# control of the registered wallet with an EIP-191 signature over the exact
# new record contents plus a freshness timestamp. Binding the full record
# (not just the agent_id) keeps the signature from becoming a bearer token
# that could authorize a different wallet/callback swap.
REREGISTRATION_DOMAIN = "SINCOR-A2A-REREGISTER-v1"
REGISTRATION_PROOF_FRESHNESS_MS = 5 * 60 * 1000


def _reregistration_message_from_parsed(parsed: Dict[str, Any], ts_ms: int) -> str:
    skills = json.dumps(parsed.get("skills") or [], sort_keys=True,
                        separators=(",", ":"))
    return "\n".join([
        REREGISTRATION_DOMAIN,
        "agent_id:%s" % parsed["agent_id"],
        "name:%s" % parsed.get("name", ""),
        "description:%s" % parsed.get("description", ""),
        "version:%s" % parsed.get("version", ""),
        "capability_tags:%s" % ",".join(parsed.get("capability_tags") or []),
        "skills:%s" % skills,
        "rpc_callback:%s" % parsed.get("rpc_callback", ""),
        "wallet:%s" % (parsed.get("wallet") or "").lower(),
        "chain_id:%s" % parsed.get("chain_id", ""),
        "sinc_stake:%s" % parsed.get("sinc_stake", 0),
        "ts:%d" % int(ts_ms),
    ])


def build_reregistration_message(body: Dict[str, Any], ts_ms: int) -> str:
    """Canonical challenge message a client signs to authorize re-registering
    an existing agent record. Normalizes exactly like the server (same
    function the write path uses), so both sides must produce byte-identical
    text. Raises ValueError on invalid registration bodies."""
    return _reregistration_message_from_parsed(_normalize_registration(body),
                                               ts_ms)


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


def register_agent_record(body: Dict[str, Any],
                          _internal_reputation: Optional[float] = None) -> Dict[str, Any]:
    from sincor2.a2a_inbound_ext import register_agent_record as _impl
    return _impl(body, _internal_reputation=_internal_reputation)


def ensure_platform_agent() -> Dict[str, Any]:
    from sincor2.a2a_inbound_ext import ensure_platform_agent as _impl
    return _impl()


def register(app: Flask) -> None:
    from sincor2.a2a_inbound_ext import mount
    mount(app)
