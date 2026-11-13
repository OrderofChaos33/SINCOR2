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


def _effective_wallet(rec: Dict[str, Any]) -> str:
    """Wallet key used for indexing/lookup: wallet, falling back to principal."""
    return str(rec.get("wallet") or rec.get("principal") or "").lower()


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
        self.by_wallet: Dict[str, str] = raw.get("by_wallet") or {}
        if self.records and not self.by_wallet:
            # Backfill the wallet index for stores that predate it.
            for kid, rec in self.records.items():
                w = _effective_wallet(rec)
                if w:
                    self.by_wallet[w] = kid

    def _persist(self) -> None:
        self.store.save(
            {
                "records": self.records,
                "by_agent": self.by_agent,
                "by_wallet": self.by_wallet,
                "saved_at": _now(),
            }
        )

    def _reindex_wallet(self, kya_id: str) -> None:
        """Move the wallet index entry for kya_id. Caller must hold self.lock."""
        for key in [k for k, v in self.by_wallet.items() if v == kya_id]:
            del self.by_wallet[key]
        rec = self.records.get(kya_id)
        if rec:
            w = _effective_wallet(rec)
            if w:
                self.by_wallet[w] = kya_id

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
                kid = self.by_wallet.get(w)
                if kid and kid in self.records:
                    return dict(self.records[kid])
                # Fallback scan for records the index may have missed.
                for rec in self.records.values():
                    if _effective_wallet(rec) == w:
                        return dict(rec)
        return None

    def list_agent(self, body: Dict[str, Any]) -> Dict[str, Any]:
        agent_id = str(body.get("agent_id") or "").strip()
        if not agent_id:
            raise ValueError("agent_id required")
        wallet = str(body.get("wallet") or body.get("principal") or "").strip().lower()
        now = _now()
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
            "created_at": now,
            "updated_at": now,
            "chain_id": CHAIN_ID,
        }
        with self.lock:
            existing = self.records.get(rec["kya_id"])
            if existing is not None:
                # Preserve the audit trail across re-lists; the overwrite is
                # idempotent for everything else (same-wallet = current behavior).
                history = list(existing.get("history") or [])
                old_wallet = _effective_wallet(existing)
                if old_wallet != wallet:
                    # Identity change: a new wallet is a new attestation. Reset
                    # the record to fresh-listed state and keep the trace.
                    history.append(
                        {"event": "identity_changed", "old_wallet": old_wallet, "at": now}
                    )
                    rec["created_at"] = existing.get("created_at") or now
                if history:
                    rec["history"] = history
            rec["record_hash"] = record_hash(rec)
            self.records[rec["kya_id"]] = rec
            self.by_agent[agent_id] = rec["kya_id"]
            self._reindex_wallet(rec["kya_id"])
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
            self._reindex_wallet(rec["kya_id"])
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

    def snapshot(
        self,
        limit: int = 100,
        offset: int = 0,
        verified_only: bool = False,
    ) -> Dict[str, Any]:
        with self.lock:
            recs = list(self.records.values())
        if verified_only:
            recs = [r for r in recs if r.get("verified")]
        recs.sort(key=lambda r: r.get("kya_id") or "")
        page = recs[offset : offset + limit] if limit > 0 else []
        return {
            "token": AXM,
            "chain_id": CHAIN_ID,
            "listed": len(self.records),
            "verified": sum(1 for r in self.records.values() if r.get("verified")),
            "records": [dict(r) for r in page],
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
