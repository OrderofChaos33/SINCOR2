"""Turn airdrop wallets into KYA listings.

Quest: verify your agent → credit AXM quest reward (ledger, not auto-transfer).
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional, Set

from sincor2.kya.pricing import PRICE_BOOK
from sincor2.kya.registry import get_registry
from sincor2.kya.store import JsonStore


def _now() -> int:
    return int(time.time())


class AirdropQuest:
    def __init__(self) -> None:
        self.store = JsonStore("airdrop_quest")
        raw = self.store.load() or {}
        self.lock = threading.Lock()
        self.wallets: Set[str] = {w.lower() for w in (raw.get("wallets") or [])}
        self.claims: Dict[str, Dict[str, Any]] = raw.get("claims") or {}

    def _persist(self) -> None:
        self.store.save({"wallets": sorted(self.wallets), "claims": self.claims, "saved_at": _now()})

    def seed(self, wallets: List[str]) -> int:
        with self.lock:
            before = len(self.wallets)
            for w in wallets:
                w = (w or "").strip().lower()
                if w.startswith("0x") and len(w) == 42:
                    self.wallets.add(w)
            self._persist()
            return len(self.wallets) - before

    def claim(self, wallet: str, agent_id: str) -> Dict[str, Any]:
        wallet = (wallet or "").strip().lower()
        rec = get_registry().lookup(agent_id=agent_id)
        if not rec:
            raise KeyError("agent not listed")
        if not rec.get("verified"):
            raise PermissionError("verify KYA first")
        bound = (rec.get("airdrop_wallet") or rec.get("wallet") or "").lower()
        if bound and bound != wallet:
            raise PermissionError("wallet does not match KYA record")
        with self.lock:
            if wallet not in self.wallets:
                raise PermissionError("wallet not in airdrop set")
            if wallet in self.claims:
                return dict(self.claims[wallet])
            row = {
                "wallet": wallet,
                "agent_id": agent_id,
                "kya_id": rec["kya_id"],
                "reward_axm": PRICE_BOOK["quest_reward_axm"],
                "status": "credited_ledger",
                "note": "treasury must settle; this is not an auto-transfer",
                "ts": _now(),
            }
            self.claims[wallet] = row
            self._persist()
            return dict(row)

    def stats(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "eligible": len(self.wallets),
                "claimed": len(self.claims),
                "unclaimed": max(0, len(self.wallets) - len(self.claims)),
                "reward_axm": PRICE_BOOK["quest_reward_axm"],
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
