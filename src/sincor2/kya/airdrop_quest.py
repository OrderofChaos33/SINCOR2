"""KYA airdrop quest — merkle verification against the real drop list.

Quest: prove you are on the AXM disperse merkle list, then verify KYA,
then credit a ledger reward (not an auto-transfer).

Identity comes from sincor2.kya_registry (live /v1/kya). The kya.registry
module is a test/fallback only.

Production root lives in data/kya/airdrop_merkle.json (gitignored /data/).
Seed the leaves with `python scripts/kya_build_merkle.py --from-file path`.

Verify binds to KYA_PRODUCTION_ROOT (if set) by address. Replica sources
are refused — a 48-leaf desk tree must never be the drop root.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from sincor2.kya.merkle import MerkleTree, verify_proof
from sincor2.kya.pricing import PRICE_BOOK
from sincor2.kya.store import JsonStore

AXM = "0x4c3fb66f14fbaa2088c9ae91017ba770da53715a"
CHAIN_ID = 8453
REPLICA_SOURCES = {"replica", "desk-replica", "stub", "desk"}


def _now() -> int:
    return int(time.time())


def _claim_window() -> tuple[Optional[int], Optional[int]]:
    """Optional claim window from env, read at call time (no caching).

    KYA_QUEST_START_TS / KYA_QUEST_END_TS are unix seconds; unset (or empty
    or unparsable) means that bound is open. Read fresh on every call so
    ops can set or change the window without restarting the process.
    """

    def _parse(name: str) -> Optional[int]:
        raw = os.environ.get(name, "").strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    return _parse("KYA_QUEST_START_TS"), _parse("KYA_QUEST_END_TS")


def _window_open(now: int, start: Optional[int], end: Optional[int]) -> bool:
    return (start is None or now >= start) and (end is None or now <= end)


def _assert_claim_window_open(now: int) -> None:
    """Fail fast and cheap: runs FIRST in claim(), before the identity lookup."""
    start, end = _claim_window()
    if start is not None and now < start:
        raise PermissionError("quest not open")
    if end is not None and now > end:
        raise PermissionError("quest closed")


def _manifest_path() -> Path:
    override = os.environ.get("KYA_AIRDROP_MANIFEST", "").strip()
    if override:
        return Path(override)
    try:
        from sincor2.data_paths import data_dir

        return data_dir() / "kya" / "airdrop_merkle.json"
    except Exception:
        return Path(__file__).resolve().parents[3] / "data" / "kya" / "airdrop_merkle.json"


def _identity(agent_id: str) -> Optional[Dict[str, Any]]:
    try:
        from sincor2.kya_registry import get_by_agent

        rec = get_by_agent(agent_id)
        if rec:
            return dict(rec)
    except Exception:
        pass
    try:
        from sincor2.kya.registry import get_registry

        return get_registry().lookup(agent_id=agent_id)
    except Exception:
        return None


def _is_verified(rec: Dict[str, Any]) -> bool:
    if rec.get("revoked"):
        return False
    if rec.get("verified") is True:
        return True
    return rec.get("status") == "verified"


def _bound_wallet(rec: Dict[str, Any]) -> str:
    return (
        rec.get("airdrop_wallet")
        or rec.get("agent_wallet")
        or rec.get("wallet")
        or rec.get("principal")
        or ""
    ).lower()


def _committed_root() -> str:
    return os.environ.get("KYA_PRODUCTION_ROOT", "").strip().lower()


class AirdropQuest:
    def __init__(self) -> None:
        self.store = JsonStore("airdrop_quest")
        raw = self.store.load() or {}
        self.lock = threading.Lock()
        self.claims: Dict[str, Dict[str, Any]] = raw.get("claims") or {}
        self.tree: Optional[MerkleTree] = None
        self.root: str = str(raw.get("root") or "")
        self.count: int = int(raw.get("count") or 0)
        self.source: str = str(raw.get("source") or "unloaded")
        self._load_manifest()

    def _persist(self) -> None:
        self.store.save(
            {
                "claims": self.claims,
                "root": self.root,
                "count": self.count,
                "source": self.source,
                "saved_at": _now(),
            }
        )

    def _sync_from(self, state: Dict[str, Any]) -> None:
        """Adopt persisted state as this instance's in-memory snapshot.

        Caller must hold self.lock (or be in __init__).
        """
        self.claims = state.get("claims") or {}
        self.root = str(state.get("root") or "")
        try:
            self.count = int(state.get("count") or 0)
        except (TypeError, ValueError):
            self.count = 0
        self.source = str(state.get("source") or "unloaded")

    def _run_update(self, mutator) -> Any:
        """Apply ``mutator`` to the FRESH on-disk state under the store flock.

        W-214 fix: the mutator receives the claims/root/count/source
        reloaded from disk inside JsonStore.update (LockedJsonStore.mutate),
        never this instance's possibly-stale snapshot, and this instance is
        re-synced from the persisted result. The mutator takes a parts dict
        {"claims", "root", "count", "source"} (mutated in place) and returns
        the caller's result. Exceptions abort the write entirely.

        Locking: self.lock is held across store.update to serialize this
        instance's own threads and the re-sync; the mutator must not call
        any method that re-acquires self.lock. LockedJsonStore's own lock
        is a separate per-store object, so this cannot deadlock against it.
        """
        captured: Dict[str, Any] = {}

        def _apply(raw: Any) -> Dict[str, Any]:
            state = raw if isinstance(raw, dict) else {}
            parts = {
                "claims": state.get("claims") or {},
                "root": str(state.get("root") or ""),
                "count": state.get("count") or 0,
                "source": str(state.get("source") or "unloaded"),
            }
            try:
                parts["count"] = int(parts["count"])
            except (TypeError, ValueError):
                parts["count"] = 0
            captured["result"] = mutator(parts)
            return {
                "claims": parts["claims"],
                "root": parts["root"],
                "count": parts["count"],
                "source": parts["source"],
                "saved_at": _now(),
            }

        with self.lock:
            new_state = self.store.update(_apply)
            self._sync_from(new_state)
        return captured.get("result")

    def _load_manifest(self) -> None:
        path = _manifest_path()
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        addresses = data.get("addresses") or data.get("wallets") or []
        if addresses:
            self.seed(addresses, source=str(data.get("source") or path.name), persist_manifest=False)
            return
        self.root = str(data.get("root") or self.root)
        self.count = int(data.get("count") or self.count)
        self.source = str(data.get("source") or self.source)

    def _is_replica(self) -> bool:
        src = (self.source or "").lower()
        return "replica" in src or src in REPLICA_SOURCES

    def _assert_production_root(self) -> None:
        if self._is_replica():
            raise PermissionError("REPLICA_ROOT_FORBIDDEN")
        bound = _committed_root()
        if bound:
            got = (self.root or "").lower()
            if got != bound:
                raise PermissionError("ROOT_MISMATCH")

    def seed(self, wallets: List[str], source: str = "seed", persist_manifest: bool = True) -> int:
        src_l = (source or "").lower()
        if ("replica" in src_l or src_l in REPLICA_SOURCES) and _committed_root():
            raise PermissionError("REPLICA_ROOT_FORBIDDEN")
        tree = MerkleTree(wallets)
        bound = _committed_root()
        if bound and tree.hex_root().lower() != bound:
            raise PermissionError("ROOT_MISMATCH")
        new_root = tree.hex_root()
        new_count = tree.manifest()["count"]

        def _merge(parts):
            # Merge root/count/source into the FRESH state; claims written
            # by another instance since this instance loaded must survive.
            parts["root"] = new_root
            parts["count"] = new_count
            parts["source"] = source
            return new_count

        self._run_update(_merge)
        with self.lock:
            self.tree = tree
        if persist_manifest:
            path = _manifest_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        **tree.manifest(),
                        "source": source,
                        "token": AXM,
                        "addresses": tree.addresses,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        return tree.manifest()["count"]

    def eligibility(self, wallet: str, proof: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        wallet = (wallet or "").strip().lower()
        bound = _committed_root()
        tree = self.tree
        root = bound or (tree.hex_root() if tree is not None else self.root)
        if tree is None:
            if proof and root:
                ok = verify_proof(wallet, proof, root)
                return {"wallet": wallet, "eligible": ok, "root": root, "count": self.count, "bound": bool(bound)}
            return {"wallet": wallet, "eligible": False, "reason": "tree unloaded", "root": root}
        p = list(proof) if proof is not None else tree.proof(wallet)
        if p is None:
            return {"wallet": wallet, "eligible": False, "root": root, "count": self.count, "bound": bool(bound)}
        ok = verify_proof(wallet, p, root)
        return {
            "wallet": wallet,
            "eligible": ok,
            "proof": p,
            "root": root,
            "count": self.count,
            "bound": bool(bound),
        }

    def claim(
        self,
        wallet: str,
        agent_id: str,
        proof: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        # Order: window check FIRST (before _identity() and any merkle/proof
        # work), then identity/KYA verification, then root binding, then
        # merkle eligibility. Window check is cheap and fail-fast.
        now = _now()
        _assert_claim_window_open(now)

        wallet = (wallet or "").strip().lower()
        rec = _identity(agent_id)
        if not rec:
            raise KeyError("agent not listed")
        if not _is_verified(rec):
            raise PermissionError("verify KYA first")
        bound = _bound_wallet(rec)
        if bound and bound != wallet:
            raise PermissionError("wallet does not match KYA record")

        self._assert_production_root()

        elig = self.eligibility(wallet, proof)
        if not elig.get("eligible"):
            raise PermissionError("wallet not in merkle list")

        key = f"{wallet}:{self.root}"
        claim_root = elig.get("root") or self.root
        claim_proof = elig.get("proof") or list(proof or [])

        def _claim(parts):
            # W-214 double-claim fix: the already-claimed check is re-done
            # against the FRESH claims reloaded under the flock. A claim
            # written by another instance since this instance loaded is
            # returned as-is — no second row, no second credit.
            fresh_claims = parts["claims"]
            if key in fresh_claims:
                return dict(fresh_claims[key])
            row = {
                "wallet": wallet,
                "agent_id": agent_id,
                "kya_id": rec.get("kya_id"),
                "reward_axm": PRICE_BOOK["quest_reward_axm"],
                "status": "credited_ledger",
                "note": "treasury must settle; this is not an auto-transfer",
                "root": claim_root,
                "proof": claim_proof,
                "ts": _now(),
                "chain_id": CHAIN_ID,
                "token": AXM,
            }
            fresh_claims[key] = row
            return dict(row)

        return self._run_update(_claim)

    def stats(self) -> Dict[str, Any]:
        with self.lock:
            claimed = len(self.claims)
            start, end = _claim_window()
            now = _now()
            return {
                "eligible": self.count,
                "claimed": claimed,
                "unclaimed": max(0, self.count - claimed),
                "reward_axm": PRICE_BOOK["quest_reward_axm"],
                "root": self.root,
                "source": self.source,
                "loaded": self.tree is not None,
                "token": AXM,
                "chain_id": CHAIN_ID,
                "replica": self._is_replica(),
                "bound_root": _committed_root() or None,
                "window": {
                    "start": start,
                    "end": end,
                    "open": _window_open(now, start, end),
                },
            }


_Q: Optional[AirdropQuest] = None
_LOCK = threading.Lock()


def get_quest() -> AirdropQuest:
    global _Q
    if _Q is None:
        with _LOCK:
            if _Q is None:
                _Q = AirdropQuest()
    return _Q
