"""KYA registry for the kya/ package (quest, SLA, SADAS).

Does not replace sincor2.kya_registry (inbound hooks). Both may run.
Identity is the product: listed -> bound -> staked -> verified | revoked | expired.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any, Dict, Optional

from sincor2.kya.store import JsonStore

MIN_STAKE_WEI = 10 * 10**18
VERIFY_FEE_WEI = 2 * 10**18
AXM = "0x4c3fb66f14fbaa2088c9ae91017ba770da53715a"
CHAIN_ID = 8453


def _now() -> int:
    return int(time.time())


def record_hash(obj: Any) -> str:
    blob = json.dumps(obj, separators=(",", ":"), sort_keys=True, default=str)
    return "0x" + hashlib.sha256(blob.encode()).hexdigest()


class KYARegistry:
    def __init__(self) -> None:
        self.store = JsonStore("registry")
        raw = self.store.load() or {}
        self.lock = threading.Lock()
        self.records: Dict[str, Dict[str, Any]] = raw.get("records") or {}
        self.by_agent: Dict[str, str] = raw.get("by_agent") or {}

    def _persist(self) -> None:
        self.store.save({"records": self.records, "by_agent": self.by_agent, "saved_at": _now()})

    def lookup(
        self,
        agent_id: Optional[str] = None,
        wallet: Optional[str] = None,
        kya_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with self.lock:
            if kya_id and kya_id in self.records:
                return dict(self.records[kya_id])
            if agent_id:
                kid = self.by_agent.get(agent_id)
                if kid and kid in self.records:
                    return dict(self.records[kid])
            if wallet:
                w = wallet.lower()
                for rec in self.records.values():
                    if (rec.get("wallet") or rec.get("principal") or "").lower() == w:
                        return dict(rec)
        return None

    def list_agent(self, body: Dict[str, Any]) -> Dict[str, Any]:
        agent_id = str(body.get("agent_id") or "").strip()
        if not agent_id:
            raise ValueError("agent_id required")
        wallet = str(body.get("wallet") or body.get("principal") or "").strip().lower()
        rec = {
            "kya_id": "kya_" + hashlib.sha256(agent_id.encode()).hexdigest()[:16],
            "agent_id": agent_id,
            "wallet": wallet,
            "principal": wallet,
            "airdrop_wallet": wallet,
            "status": "listed",
            "verified": False,
            "stake_axm_wei": "0",
            "heartbeat_ts": 0,
            "created_at": _now(),
            "updated_at": _now(),
            "chain_id": CHAIN_ID,
        }
        rec["record_hash"] = record_hash(rec)
        with self.lock:
            self.records[rec["kya_id"]] = rec
            self.by_agent[agent_id] = rec["kya_id"]
            self._persist()
        return dict(rec)

    def bind(self, agent_id: str, principal: str) -> Dict[str, Any]:
        rec = self.lookup(agent_id=agent_id)
        if not rec:
            raise KeyError("agent not listed")
        rec["principal"] = principal.lower()
        rec["wallet"] = rec["wallet"] or principal.lower()
        rec["status"] = "bound"
        rec["updated_at"] = _now()
        rec["record_hash"] = record_hash(rec)
        with self.lock:
            self.records[rec["kya_id"]] = rec
            self._persist()
        return dict(rec)

    def apply_stake(self, kya_id: str, wei: str, tx: str) -> Dict[str, Any]:
        rec = self.lookup(kya_id=kya_id)
        if not rec:
            raise KeyError("unknown kya")
        rec["stake_axm_wei"] = str(int(wei))
        rec["stake_tx"] = tx
        rec["status"] = "staked" if int(wei) >= MIN_STAKE_WEI else rec.get("status")
        rec["updated_at"] = _now()
        rec["record_hash"] = record_hash(rec)
        with self.lock:
            self.records[kya_id] = rec
            self._persist()
        return dict(rec)

    def heartbeat(self, agent_id: str, ok: bool = True) -> Optional[Dict[str, Any]]:
        rec = self.lookup(agent_id=agent_id)
        if not rec:
            return None
        rec["heartbeat_ts"] = _now()
        rec["heartbeat_ok"] = bool(ok)
        rec["updated_at"] = _now()
        with self.lock:
            self.records[rec["kya_id"]] = rec
            self._persist()
        return dict(rec)

    def verify(self, kya_id: str) -> Dict[str, Any]:
        rec = self.lookup(kya_id=kya_id)
        if not rec:
            raise KeyError("unknown kya")
        if rec.get("status") not in ("bound", "staked", "verified"):
            raise PermissionError("bind first")
        if int(rec.get("stake_axm_wei") or 0) < MIN_STAKE_WEI:
            raise PermissionError("stake below minimum")
        rec["verified"] = True
        rec["status"] = "verified"
        rec["updated_at"] = _now()
        rec["record_hash"] = record_hash(rec)
        with self.lock:
            self.records[kya_id] = rec
            self._persist()
        return dict(rec)

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            recs = list(self.records.values())
        return {
            "token": AXM,
            "chain_id": CHAIN_ID,
            "listed": len(recs),
            "verified": sum(1 for r in recs if r.get("verified")),
        }


_REG: Optional[KYARegistry] = None
_LOCK = threading.Lock()


def get_registry() -> KYARegistry:
    global _REG
    if _REG is None:
        with _LOCK:
            if _REG is None:
                _REG = KYARegistry()
    return _REG
