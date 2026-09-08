"""SINCOR KYA v0 — in-process registry.

Hooks the existing inbound fabric without replacing it.
Persist path: data_dir()/kya_records.json
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

WALLET_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
AGENT_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
AXM = "0x4c3fb66f14fbaa2088c9ae91017ba770da53715a"
CHAIN_ID = 8453
MIN_STAKE_WEI = 10 * 10**18
VERIFY_FEE_WEI = 2 * 10**18
HEARTBEAT_TTL_MS = 60_000
LIVE_GRACE = 3

_LOCK = threading.Lock()
_STORE: Dict[str, Dict[str, Any]] = {}
_BY_AGENT: Dict[str, str] = {}


def _now_ms() -> int:
    return int(time.time() * 1000)


def reset() -> None:
    """Test-only: wipe in-memory index. Does not delete disk until next save."""
    with _LOCK:
        _STORE.clear()
        _BY_AGENT.clear()


def _persist_path() -> Optional[Path]:
    import os

    override = os.environ.get("KYA_STORE_PATH")
    if override:
        return Path(override)
    try:
        from sincor2.data_paths import data_dir

        return data_dir() / "kya_records.json"
    except Exception:
        return Path("/tmp/kya_records.json")


def load() -> None:
    path = _persist_path()
    if path is None or not path.is_file():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        records = raw.get("records") if isinstance(raw, dict) else raw
        if isinstance(records, list):
            with _LOCK:
                for rec in records:
                    if isinstance(rec, dict) and rec.get("kya_id"):
                        _STORE[rec["kya_id"]] = rec
                        _BY_AGENT[rec["agent_id"]] = rec["kya_id"]
    except Exception:
        pass


def save() -> None:
    path = _persist_path()
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        payload = {"records": list(_STORE.values()), "saved_at": _now_ms()}
    path.write_text(json.dumps(payload), encoding="utf-8")


def _canonical(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True, ensure_ascii=True)


def card_hash(card: Dict[str, Any]) -> str:
    digest = hashlib.sha256(_canonical(card).encode()).hexdigest()
    return "0x" + digest


def make_kya_id(agent_id: str, principal: str, c_hash: str) -> str:
    raw = f"{agent_id}:{principal.lower()}:{c_hash}".encode()
    return "kya_" + hashlib.sha256(raw).hexdigest()[:16]


def _safe_url(value: Any) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    return url if url and parsed.scheme in ("http", "https") and parsed.netloc else ""


def score(rec: Dict[str, Any]) -> int:
    sla = rec.get("sla") or {}
    jobs = rec.get("jobs") or {}
    live = 1 if rec.get("status") in ("verified", "staked", "bound") and _is_live(rec) else 0
    bound = 1 if rec.get("status") in ("bound", "staked", "verified") else 0
    staked = 1 if int(rec.get("stake_axm_wei") or 0) >= MIN_STAKE_WEI else 0
    uptime = float(sla.get("uptime_30d") or 0)
    completed = int(jobs.get("completed") or 0)
    failed = int(jobs.get("failed") or 0)
    total = completed + failed
    completion = (completed / total) if total else 0.0
    slashed = int(jobs.get("slashed") or 0)
    disputed = int(jobs.get("disputed") or 0)
    raw = (
        200 * live
        + 200 * bound
        + 200 * staked
        + 200 * uptime
        + 200 * completion
        - 50 * slashed
        - 100 * disputed
    )
    return max(0, min(1000, int(raw)))


def _is_live(rec: Dict[str, Any]) -> bool:
    last = int((rec.get("sla") or {}).get("last_heartbeat_ms") or 0)
    return (_now_ms() - last) <= HEARTBEAT_TTL_MS * LIVE_GRACE if last else False


def refresh_status(rec: Dict[str, Any]) -> Dict[str, Any]:
    if rec.get("revoked"):
        rec["status"] = "revoked"
        rec["score"] = score(rec)
        return rec
    valid_until = ((rec.get("scope") or {}).get("valid_until") or "")
    expired = False
    if valid_until:
        try:
            from datetime import datetime, timezone

            dt = datetime.fromisoformat(valid_until.replace("Z", "+00:00"))
            expired = dt.timestamp() * 1000 < _now_ms()
        except Exception:
            expired = False
    if expired or (rec.get("status") == "verified" and not _is_live(rec)):
        rec["status"] = "expired"
    rec["score"] = score(rec)
    rec["updated_at"] = _now_ms()
    return rec


def list_from_inbound(agent: Dict[str, Any], card: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    agent_id = str(agent.get("agent_id") or "")
    if not AGENT_ID_RE.match(agent_id):
        raise ValueError("bad agent_id")
    wallet = str(agent.get("wallet") or "")
    if wallet and not WALLET_RE.match(wallet):
        raise ValueError("bad wallet")
    card = card or {
        "id": agent_id,
        "name": agent.get("name"),
        "description": agent.get("description"),
        "version": agent.get("version"),
        "skills": agent.get("skills") or [],
    }
    c_hash = card_hash(card)
    principal = wallet or "0x0000000000000000000000000000000000000000"
    kya_id = make_kya_id(agent_id, principal, c_hash)
    rec = {
        "kya_id": kya_id,
        "agent_id": agent_id,
        "principal": principal,
        "agent_wallet": wallet or principal,
        "card_url": _safe_url(agent.get("rpc_callback")),
        "card_hash": c_hash,
        "name": agent.get("name") or agent_id,
        "version": agent.get("version") or "0.0.0",
        "skills": agent.get("skills") or [],
        "status": "listed",
        "scope": {
            "max_axm_per_tx": str(5 * 10**18),
            "daily_axm_limit": str(50 * 10**18),
            "allowed_skills": [s.get("id") for s in (agent.get("skills") or []) if isinstance(s, dict)],
            "allowed_counterparties": [],
            "valid_until": "2027-01-01T00:00:00Z",
        },
        "stake_axm_wei": "0",
        "stake_tx": None,
        "score": 0,
        "sla": {
            "last_ok": False,
            "last_heartbeat_ms": int(agent.get("last_heartbeat") or 0),
            "uptime_30d": 0.0,
            "last_proof_tx": None,
            "latency_ms": None,
        },
        "jobs": {"completed": 0, "failed": 0, "disputed": 0, "slashed": 0},
        "attestation": None,
        "revoked": False,
        "revoke_reason": None,
        "created_at": _now_ms(),
        "updated_at": _now_ms(),
        "chain_id": CHAIN_ID,
    }
    rec["score"] = score(rec)
    with _LOCK:
        _STORE[kya_id] = rec
        _BY_AGENT[agent_id] = kya_id
    save()
    return rec


def bind(agent_id: str, principal: str, signature: str, message: str, recovered: Optional[str] = None) -> Dict[str, Any]:
    if not WALLET_RE.match(principal):
        raise ValueError("bad principal")
    rec = get_by_agent(agent_id)
    if rec is None:
        raise KeyError("unknown agent")
    if rec.get("revoked"):
        raise ValueError("revoked")
    signer = (recovered or principal).lower()
    if signer != principal.lower():
        raise ValueError("signer mismatch")
    rec["principal"] = principal
    rec["attestation"] = {
        "scheme": "eip191",
        "message": message,
        "signature": signature,
        "recovered": principal,
    }
    rec["status"] = "bound"
    refresh_status(rec)
    save()
    return rec


def apply_stake(kya_id: str, stake_wei: str, stake_tx: str) -> Dict[str, Any]:
    rec = get(kya_id)
    if rec is None:
        raise KeyError("unknown kya")
    if rec.get("revoked"):
        raise ValueError("revoked")
    rec["stake_axm_wei"] = str(int(stake_wei))
    rec["stake_tx"] = stake_tx
    rec["status"] = "staked" if int(stake_wei) >= MIN_STAKE_WEI else rec.get("status")
    refresh_status(rec)
    save()
    return rec


def verify(kya_id: str) -> Dict[str, Any]:
    rec = get(kya_id)
    if rec is None:
        raise KeyError("unknown kya")
    if rec.get("revoked"):
        raise ValueError("revoked")
    if not rec.get("attestation"):
        raise ValueError("not bound")
    if int(rec.get("stake_axm_wei") or 0) < MIN_STAKE_WEI:
        raise ValueError("stake below minimum")
    if not _is_live(rec):
        raise ValueError("heartbeat stale")
    rec["status"] = "verified"
    refresh_status(rec)
    save()
    return rec


def heartbeat(agent_id: str, ok: bool = True, latency_ms: Optional[int] = None) -> Optional[Dict[str, Any]]:
    rec = get_by_agent(agent_id)
    if rec is None:
        return None
    sla = rec.setdefault("sla", {})
    sla["last_heartbeat_ms"] = _now_ms()
    sla["last_ok"] = bool(ok)
    if latency_ms is not None:
        sla["latency_ms"] = int(latency_ms)
    if rec.get("status") == "expired" and rec.get("attestation") and int(rec.get("stake_axm_wei") or 0) >= MIN_STAKE_WEI:
        rec["status"] = "verified"
    refresh_status(rec)
    save()
    return rec


def post_sla(kya_id: str, ok: bool, latency_ms: int, proof_tx: Optional[str] = None) -> Dict[str, Any]:
    rec = get(kya_id)
    if rec is None:
        raise KeyError("unknown kya")
    sla = rec.setdefault("sla", {})
    sla["last_ok"] = bool(ok)
    sla["latency_ms"] = int(latency_ms)
    sla["last_heartbeat_ms"] = _now_ms()
    if proof_tx:
        sla["last_proof_tx"] = proof_tx
    prev = float(sla.get("uptime_30d") or 0)
    sla["uptime_30d"] = min(1.0, prev * 0.99 + (1.0 if ok else 0.0) * 0.01)
    refresh_status(rec)
    save()
    return rec


def revoke(kya_id: str, reason: str = "") -> Dict[str, Any]:
    rec = get(kya_id)
    if rec is None:
        raise KeyError("unknown kya")
    rec["revoked"] = True
    rec["revoke_reason"] = reason
    rec["status"] = "revoked"
    refresh_status(rec)
    save()
    return rec


def get(kya_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        return _STORE.get(kya_id)


def get_by_agent(agent_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        kid = _BY_AGENT.get(agent_id)
        rec = _STORE.get(kid) if kid else None
        return rec


def lookup_wallet(wallet: str) -> List[Dict[str, Any]]:
    wallet_l = wallet.lower()
    with _LOCK:
        return [
            dict(r)
            for r in _STORE.values()
            if r.get("principal", "").lower() == wallet_l or r.get("agent_wallet", "").lower() == wallet_l
        ]


def snapshot() -> Dict[str, Any]:
    with _LOCK:
        recs = list(_STORE.values())
    return {
        "token": AXM,
        "chain_id": CHAIN_ID,
        "min_stake_wei": str(MIN_STAKE_WEI),
        "verify_fee_wei": str(VERIFY_FEE_WEI),
        "listed": len(recs),
        "verified": sum(1 for r in recs if r.get("status") == "verified"),
        "revoked": sum(1 for r in recs if r.get("revoked")),
    }


def hook_listed(agent: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Best-effort list after inbound register. Never raises into A2A."""
    try:
        if not agent or not agent.get("agent_id"):
            return None
        return list_from_inbound(agent)
    except Exception:
        return None


def hook_heartbeat(agent_id: str, ok: bool = True) -> Optional[Dict[str, Any]]:
    """Best-effort liveness copy. Never raises into A2A."""
    try:
        return heartbeat(agent_id, ok=ok)
    except Exception:
        return None


load()
