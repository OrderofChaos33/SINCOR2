"""KYA registry for the kya/ package (quest, SLA, SADAS).

Does not replace sincor2.kya_registry (inbound hooks). Both may run.
Identity is the product: listed -> bound -> staked -> verified | revoked | expired.

Verification TTL: verify() sets expires_at = now + KYA_VERIFY_TTL_S (env,
default 90 days = 7776000). A verified record whose TTL has passed reports
"expired" via status_of() (computed, never mutating the stored status string)
and is_live() returns False. lookup() returns the stored record untouched.

Heartbeat refresh policy: heartbeat() on a verified record that is still live
extends expires_at by KYA_HEARTBEAT_EXTEND_S (env, default 30 days = 2592000),
capped at now + KYA_VERIFY_TTL_S (the original TTL measured from now). A
heartbeat does NOT revive an expired record -- only a live verified record is
extended; an expired record must re-verify to become live again.

No-revive guard: revoke() tombstones the kya_id in a package-local tombstone
set persisted alongside the records. A tombstoned kya_id can never become
verified again -- verify() on it raises PermissionError. Re-listing the same
agent_id derives a NEW kya_id (successor of the tombstoned one); the old one
stays revoked forever. This guard is package-local to this registry and is
DISTINCT from the on-chain per-actor tombstone in contracts/kya/KYARegistry.sol
(W-10 no-revive guard), which is enforced by the contract for the chain
identity layer. The two layers do not share state.

ADVERSARIAL SELF-REVIEW: The tombstone set lives in the same JsonStore as the
records, so anyone who can wipe or restore the store file defeats the no-revive
guard -- package-local persistence is weaker than the on-chain tombstone, and
this registry makes no claim of BFT-grade finality. Scope limit: the guard only
governs THIS registry's verify(); a revoked agent can re-enter with a fresh
kya_id by design (new identity, new stake, new trust). TTL bypass: heartbeat()
extends expiry with no rate limiting in this package -- a verified agent that
keeps heartbeating never expires, so a malicious or compromised verifier-caller
can keep an identity live indefinitely. Rate-limiting heartbeat extension and
operator-side forced revocation are out of scope for this module but are flagged
here as the real-world backstop. expires_at relies on the host clock; skew or
rollback of time.time() shifts effective expiry. KYA_VERIFY_TTL_S and
KYA_HEARTBEAT_EXTEND_S are read from the environment at call time, so tests and
operators can tune them per run.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from typing import Any, Dict, Optional, Set

from sincor2.kya.store import JsonStore

MIN_STAKE_WEI = 10 * 10**18
VERIFY_FEE_WEI = 2 * 10**18
AXM = "0x4c3fb66f14fbaa2088c9ae91017ba770da53715a"
CHAIN_ID = 8453

DEFAULT_VERIFY_TTL_S = 90 * 24 * 3600  # 7776000
DEFAULT_HEARTBEAT_EXTEND_S = 30 * 24 * 3600  # 2592000


def _now() -> int:
    return int(time.time())


def _ttl_s() -> int:
    try:
        return int(os.environ.get("KYA_VERIFY_TTL_S", "") or DEFAULT_VERIFY_TTL_S)
    except ValueError:
        return DEFAULT_VERIFY_TTL_S


def _heartbeat_extend_s() -> int:
    try:
        return int(os.environ.get("KYA_HEARTBEAT_EXTEND_S", "") or DEFAULT_HEARTBEAT_EXTEND_S)
    except ValueError:
        return DEFAULT_HEARTBEAT_EXTEND_S


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
        self.tombstones: Set[str] = set(raw.get("tombstones") or [])

    def _persist(self) -> None:
        self.store.save(
            {
                "records": self.records,
                "by_agent": self.by_agent,
                "tombstones": sorted(self.tombstones),
                "saved_at": _now(),
            }
        )

    @staticmethod
    def _append_history(rec: Dict[str, Any], event: str, detail: Optional[Dict[str, Any]] = None) -> None:
        entry: Dict[str, Any] = {"event": event, "at": _now()}
        if detail:
            entry.update(detail)
        rec.setdefault("history", []).append(entry)

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
        base_kya_id = "kya_" + hashlib.sha256(agent_id.encode()).hexdigest()[:16]
        rec = {
            "kya_id": base_kya_id,
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
        with self.lock:
            # No-revive guard: a tombstoned kya_id stays revoked forever. Re-listing
            # the same agent_id derives a NEW successor kya_id; the derivation
            # sha256(agent_id)[:16] is unchanged for first-time listings.
            kya_id = base_kya_id
            n = 2
            while kya_id in self.tombstones:
                kya_id = f"{base_kya_id}-r{n}"
                n += 1
            rec["kya_id"] = kya_id
            self._append_history(rec, "listed")
            rec["record_hash"] = record_hash(rec)
            self.records[kya_id] = rec
            self.by_agent[agent_id] = kya_id
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
        now = _now()
        rec["heartbeat_ts"] = now
        rec["heartbeat_ok"] = bool(ok)
        # Verified-but-live records get their verification expiry extended.
        # Policy: extension = min(expires_at + KYA_HEARTBEAT_EXTEND_S,
        #                         now + KYA_VERIFY_TTL_S). Expired records are NOT
        # revived by heartbeat; they must re-verify. Non-verified records are
        # untouched. Note: no rate limiting here -- a constantly-heartbeating
        # verified agent never expires (flagged in the module docstring).
        if rec.get("status") == "verified" and rec["kya_id"] not in self.tombstones:
            exp = rec.get("expires_at") or 0
            if now <= exp:
                rec["expires_at"] = min(exp + _heartbeat_extend_s(), now + _ttl_s())
        rec["updated_at"] = now
        rec["record_hash"] = record_hash(rec)
        with self.lock:
            self.records[rec["kya_id"]] = rec
            self._persist()
        return dict(rec)

    def verify(self, kya_id: str) -> Dict[str, Any]:
        with self.lock:
            rec = self.records.get(kya_id)
            if not rec:
                raise KeyError("unknown kya")
            # No-revive guard: tombstoned kya_ids can never verify again.
            if kya_id in self.tombstones or rec.get("status") == "revoked":
                raise PermissionError("kya revoked: tombstoned, cannot re-verify")
        rec = dict(rec)
        if rec.get("status") not in ("bound", "staked", "verified"):
            raise PermissionError("bind first")
        if int(rec.get("stake_axm_wei") or 0) < MIN_STAKE_WEI:
            raise PermissionError("stake below minimum")
        now = _now()
        rec["verified"] = True
        rec["status"] = "verified"
        rec["verified_at"] = now
        rec["expires_at"] = now + _ttl_s()
        rec["updated_at"] = now
        self._append_history(rec, "verified", {"expires_at": rec["expires_at"]})
        rec["record_hash"] = record_hash(rec)
        with self.lock:
            if kya_id in self.tombstones:
                raise PermissionError("kya revoked: tombstoned, cannot re-verify")
            self.records[kya_id] = rec
            self._persist()
        return dict(rec)

    def revoke(self, kya_id: str, reason: str = "") -> Dict[str, Any]:
        rec = self.lookup(kya_id=kya_id)
        if not rec:
            raise KeyError("unknown kya")
        now = _now()
        rec["verified"] = False
        rec["status"] = "revoked"
        rec["revoked_at"] = now
        rec["revoke_reason"] = reason
        rec["updated_at"] = now
        self._append_history(rec, "revoked", {"reason": reason})
        rec["record_hash"] = record_hash(rec)
        with self.lock:
            self.records[kya_id] = rec
            self.tombstones.add(kya_id)
            self._persist()
        return dict(rec)

    def is_live(self, kya_id: str) -> bool:
        """False when the record is missing, status != "verified", or now > expires_at."""
        rec = self.lookup(kya_id=kya_id)
        if not rec:
            return False
        if rec.get("status") != "verified":
            return False
        return _now() <= (rec.get("expires_at") or 0)

    def status_of(self, kya_id: str) -> str:
        """Effective status: "expired" if verified and past TTL, else the stored status.

        Computed on observation -- the stored status string is never mutated to
        "expired" (avoids racy write-on-read). lookup() callers are unaffected.
        """
        rec = self.lookup(kya_id=kya_id)
        if not rec:
            raise KeyError("unknown kya")
        stored = rec.get("status")
        if stored == "verified" and _now() > (rec.get("expires_at") or 0):
            return "expired"
        return stored

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
