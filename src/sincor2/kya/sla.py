"""5-minute SLA pings bound to a kya_id. Receipts, not theater."""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

from sincor2.kya.pricing import PRICE_BOOK
from sincor2.kya.registry import record_hash
from sincor2.kya.store import JsonStore

INTERVAL_S = int(PRICE_BOOK["sla_epoch"]["interval_s"])


def _now() -> int:
    return int(time.time())


class SLAMonitor:
    def __init__(self) -> None:
        self.store = JsonStore("sla")
        raw = self.store.load() or {}
        self.lock = threading.Lock()
        self.subs: Dict[str, Dict[str, Any]] = raw.get("subs") or {}
        self.receipts: List[Dict[str, Any]] = raw.get("receipts") or []

    def _persist(self) -> None:
        self.store.save({"subs": self.subs, "receipts": self.receipts[-800:], "saved_at": _now()})

    def subscribe(self, kya_id: str) -> Dict[str, Any]:
        row = {"kya_id": kya_id, "interval_s": INTERVAL_S, "ok": 0, "miss": 0, "last_ts": 0}
        with self.lock:
            self.subs[kya_id] = row
            self._persist()
        return dict(row)

    def ping(self, kya_id: str, ok: bool = True, latency_ms: int = 0) -> Dict[str, Any]:
        with self.lock:
            sub = self.subs.get(kya_id)
            if sub is None:
                sub = {"kya_id": kya_id, "interval_s": INTERVAL_S, "ok": 0, "miss": 0, "last_ts": 0}
                self.subs[kya_id] = sub
            now = _now()
            last = int(sub.get("last_ts") or 0)
            if last and now - last > INTERVAL_S * 2:
                sub["miss"] = int(sub.get("miss") or 0) + 1
            if ok:
                sub["ok"] = int(sub.get("ok") or 0) + 1
            else:
                sub["miss"] = int(sub.get("miss") or 0) + 1
            sub["last_ts"] = now
            sub["latency_ms"] = latency_ms
            receipt = {
                "kya_id": kya_id,
                "ok": bool(ok),
                "latency_ms": latency_ms,
                "ts": now,
                "epoch": now // INTERVAL_S,
            }
            receipt["attestation_hash"] = record_hash(receipt)
            self.receipts.append(receipt)
            self._persist()
            return dict(receipt)

    def list_receipts(self, kya_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.lock:
            rows = list(self.receipts)
        if kya_id:
            rows = [r for r in rows if r.get("kya_id") == kya_id]
        return rows[-200:]


_S: Optional[SLAMonitor] = None
_LOCK = threading.Lock()


def get_sla() -> SLAMonitor:
    global _S
    if _S is None:
        with _LOCK:
            if _S is None:
                _S = SLAMonitor()
    return _S
