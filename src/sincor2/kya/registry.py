"""KYA registry for the kya/ package (quest, SLA, SADAS).

Does not replace sincor2.kya_registry (inbound hooks). Both may run.
Identity is the product: listed -> bound -> staked -> verified | revoked | expired.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional

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


_CAPABILITY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _clean_capabilities(raw: Any) -> List[str]:
    out: List[str] = []
    for item in raw or []:
        name = str(item or "").strip().lower()
        if name and _CAPABILITY_RE.match(name) and name not in out:
            out.append(name)
    return out


class _AbortUpdate(Exception):
    """Internal: abort a JsonStore.update without writing (no-op mutation)."""


_ABORTED = object()


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

    @staticmethod
    def _reindex_wallet_in(
        records: Dict[str, Dict[str, Any]],
        by_wallet: Dict[str, str],
        kya_id: str,
    ) -> None:
        """Move the wallet index entry for kya_id in the given state dicts."""
        for key in [k for k, v in by_wallet.items() if v == kya_id]:
            del by_wallet[key]
        rec = records.get(kya_id)
        if rec:
            w = _effective_wallet(rec)
            if w:
                by_wallet[w] = kya_id

    def _reindex_wallet(self, kya_id: str) -> None:
        """Move the wallet index entry for kya_id. Caller must hold self.lock."""
        self._reindex_wallet_in(self.records, self.by_wallet, kya_id)

    def _sync_from(self, state: Dict[str, Any]) -> None:
        """Adopt persisted state as this instance's in-memory snapshot.

        Caller must hold self.lock (or be in __init__).
        """
        self.records = state.get("records") or {}
        self.by_agent = state.get("by_agent") or {}
        self.by_wallet = state.get("by_wallet") or {}
        if self.records and not self.by_wallet:
            for kid, rec in self.records.items():
                w = _effective_wallet(rec)
                if w:
                    self.by_wallet[w] = kid

    def _run_update(
        self,
        mutator: Callable[
            [Dict[str, Dict[str, Any]], Dict[str, str], Dict[str, str]], Any
        ],
    ) -> Any:
        """Apply ``mutator`` to the FRESH on-disk state under the store flock.

        W-203 fix: the mutator receives the records/by_agent/by_wallet dicts
        reloaded from disk inside JsonStore.update (LockedJsonStore.mutate),
        never this instance's possibly-stale snapshot, and this instance is
        re-synced from the persisted result. Exceptions raised by the
        mutator abort the write entirely (nothing is persisted), matching
        the old raise-before-_persist behavior. ``_AbortUpdate`` is the
        no-op path (e.g. heartbeat for an unknown agent): nothing is
        written, the instance re-syncs from disk, and ``_ABORTED`` is
        returned.

        Locking: self.lock is held across store.update to serialize this
        instance's own threads and the re-sync. The mutator must operate
        only on the dicts it is given — it must not call lookup() or
        _persist() (both would re-enter self.lock / the stale snapshot).
        LockedJsonStore's own lock is a separate per-store object, so
        holding self.lock here cannot deadlock against it.
        """
        captured: Dict[str, Any] = {}

        def _apply(raw: Any) -> Dict[str, Any]:
            state = raw if isinstance(raw, dict) else {}
            records = state.get("records") or {}
            by_agent = state.get("by_agent") or {}
            by_wallet = state.get("by_wallet") or {}
            if records and not by_wallet:
                # Backfill the wallet index for stores that predate it.
                for kid, rec in records.items():
                    w = _effective_wallet(rec)
                    if w:
                        by_wallet[w] = kid
            captured["result"] = mutator(records, by_agent, by_wallet)
            return {
                "records": records,
                "by_agent": by_agent,
                "by_wallet": by_wallet,
                "saved_at": _now(),
            }

        with self.lock:
            try:
                new_state = self.store.update(_apply)
            except _AbortUpdate:
                fresh = self.store.load() or {}
                if isinstance(fresh, dict):
                    self._sync_from(fresh)
                return _ABORTED
            self._sync_from(new_state)
        return captured.get("result")

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
            "capabilities": _clean_capabilities(body.get("capabilities")),
        }

        def _mutate(records, by_agent, by_wallet):
            existing = records.get(rec["kya_id"])
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
            records[rec["kya_id"]] = rec
            by_agent[agent_id] = rec["kya_id"]
            self._reindex_wallet_in(records, by_wallet, rec["kya_id"])
            return dict(rec)

        return self._run_update(_mutate)

    def bind(self, agent_id: str, principal: str) -> Dict[str, Any]:
        def _mutate(records, by_agent, by_wallet):
            kid = by_agent.get(agent_id)
            rec = records.get(kid) if kid else None
            if not rec:
                raise KeyError("agent not listed")
            rec["principal"] = principal.lower()
            rec["wallet"] = rec["wallet"] or principal.lower()
            rec["status"] = "bound"
            rec["updated_at"] = _now()
            rec["record_hash"] = record_hash(rec)
            self._reindex_wallet_in(records, by_wallet, rec["kya_id"])
            return dict(rec)

        return self._run_update(_mutate)

    def apply_stake(self, kya_id: str, wei: str, tx: str) -> Dict[str, Any]:
        def _mutate(records, by_agent, by_wallet):
            rec = records.get(kya_id)
            if not rec:
                raise KeyError("unknown kya")
            rec["stake_axm_wei"] = str(int(wei))
            rec["stake_tx"] = tx
            rec["status"] = "staked" if int(wei) >= MIN_STAKE_WEI else rec.get("status")
            rec["updated_at"] = _now()
            rec["record_hash"] = record_hash(rec)
            return dict(rec)

        return self._run_update(_mutate)

    def heartbeat(self, agent_id: str, ok: bool = True) -> Optional[Dict[str, Any]]:
        def _mutate(records, by_agent, by_wallet):
            kid = by_agent.get(agent_id)
            rec = records.get(kid) if kid else None
            if not rec:
                raise _AbortUpdate()
            rec["heartbeat_ts"] = _now()
            rec["heartbeat_ok"] = bool(ok)
            rec["updated_at"] = _now()
            return dict(rec)

        result = self._run_update(_mutate)
        return None if result is _ABORTED else result

    def verify(self, kya_id: str) -> Dict[str, Any]:
        def _mutate(records, by_agent, by_wallet):
            rec = records.get(kya_id)
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
            return dict(rec)

        return self._run_update(_mutate)

    def grant_capability(self, agent_id: str, capability: str) -> Dict[str, Any]:
        name = str(capability or "").strip().lower()
        if not name or not _CAPABILITY_RE.match(name):
            raise ValueError("bad capability name")

        def _mutate(records, by_agent, by_wallet):
            kid = by_agent.get(agent_id)
            rec = records.get(kid) if kid else None
            if not rec:
                raise KeyError("agent not listed")
            caps = [str(c) for c in (rec.get("capabilities") or [])]
            if name not in caps:
                caps.append(name)
            rec["capabilities"] = caps
            rec["updated_at"] = _now()
            rec["record_hash"] = record_hash(rec)
            return dict(rec)

        return self._run_update(_mutate)

    def revoke(self, kya_id: str, reason: str = "") -> Dict[str, Any]:
        """Revoke a KYA identity. Terminal: no revive path on this record."""

        def _mutate(records, by_agent, by_wallet):
            rec = records.get(kya_id)
            if not rec:
                raise KeyError("unknown kya")
            rec["revoked"] = True
            rec["verified"] = False
            rec["status"] = "revoked"
            rec["revoked_at"] = _now()
            rec["revoked_reason"] = str(reason or "")[:300]
            rec["updated_at"] = _now()
            rec["record_hash"] = record_hash(rec)
            return dict(rec)

        return self._run_update(_mutate)

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
