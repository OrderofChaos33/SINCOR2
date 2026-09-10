"""KYA airdrop quest — merkle verification against the real drop list.

Quest: prove you are on the AXM disperse merkle list, then verify KYA,
then credit a ledger reward (not an auto-transfer).

Identity comes from sincor2.kya_registry (live /v1/kya). The kya.registry
module is a test/fallback only.

Production root lives in data/kya/airdrop_merkle.json (gitignored /data/).
Seed the leaves with `python scripts/kya_build_merkle.py --from-file path`.
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


def _now() -> int:
    return int(time.time())


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

    def seed(self, wallets: List[str], source: str = "seed", persist_manifest: bool = True) -> int:
        tree = MerkleTree(wallets)
        with self.lock:
            self.tree = tree
            self.root = tree.hex_root()
            self.count = tree.manifest()["count"]
            self.source = source
            self._persist()
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
        tree = self.tree
        if tree is None:
            if proof and self.root:
                ok = verify_proof(wallet, proof, self.root)
                return {"wallet": wallet, "eligible": ok, "root": self.root, "count": self.count}
            return {"wallet": wallet, "eligible": False, "reason": "tree unloaded", "root": self.root}
        p = list(proof) if proof is not None else tree.proof(wallet)
        if p is None:
            return {"wallet": wallet, "eligible": False, "root": tree.hex_root(), "count": self.count}
        ok = verify_proof(wallet, p, tree.hex_root())
        return {
            "wallet": wallet,
            "eligible": ok,
            "proof": p,
            "root": tree.hex_root(),
            "count": self.count,
        }

    def claim(
        self,
        wallet: str,
        agent_id: str,
        proof: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        wallet = (wallet or "").strip().lower()
        rec = _identity(agent_id)
        if not rec:
            raise KeyError("agent not listed")
        if not _is_verified(rec):
            raise PermissionError("verify KYA first")
        bound = _bound_wallet(rec)
        if bound and bound != wallet:
            raise PermissionError("wallet does not match KYA record")

        elig = self.eligibility(wallet, proof)
        if not elig.get("eligible"):
            raise PermissionError("wallet not in merkle list")

        with self.lock:
            key = f"{wallet}:{self.root}"
            if key in self.claims:
                return dict(self.claims[key])
            row = {
                "wallet": wallet,
                "agent_id": agent_id,
                "kya_id": rec.get("kya_id"),
                "reward_axm": PRICE_BOOK["quest_reward_axm"],
                "status": "credited_ledger",
                "note": "treasury must settle; this is not an auto-transfer",
                "root": self.root,
                "proof": elig.get("proof") or list(proof or []),
                "ts": _now(),
                "chain_id": CHAIN_ID,
                "token": AXM,
            }
            self.claims[key] = row
            self._persist()
            return dict(row)

    def stats(self) -> Dict[str, Any]:
        with self.lock:
            claimed = len(self.claims)
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
