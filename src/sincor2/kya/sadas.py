"""SADAS feed — paid scorecard, gated unless subscribed."""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

from sincor2.kya.store import JsonStore


def _now() -> int:
    return int(time.time())


class SADASFeed:
    def __init__(self) -> None:
        self.store = JsonStore("sadas")
        raw = self.store.load() or {}
        self.lock = threading.Lock()
        self.items: List[Dict[str, Any]] = raw.get("items") or []
        self.subs: Dict[str, Dict[str, Any]] = raw.get("subs") or {}

    def _persist(self) -> None:
        self.store.save({"items": self.items[-400:], "subs": self.subs, "saved_at": _now()})

    def publish(self, body: Dict[str, Any]) -> Dict[str, Any]:
        row = {
            "title": str(body.get("title") or "")[:160],
            "signal": str(body.get("signal") or "")[:800],
            "score": float(body.get("score") or 0),
            "ts": _now(),
        }
        with self.lock:
            self.items.append(row)
            self._persist()
        return dict(row)

    def subscribe(self, kya_id: str, token: str) -> Dict[str, Any]:
        row = {"kya_id": kya_id, "token": token, "ts": _now()}
        with self.lock:
            self.subs[kya_id] = row
            self._persist()
        return dict(row)

    def feed(self, token: Optional[str] = None) -> Dict[str, Any]:
        with self.lock:
            gated = not token or not any(s.get("token") == token for s in self.subs.values())
            items = list(self.items[-50:]) if not gated else []
        return {"gated": gated, "items": items, "count": 0 if gated else len(items)}

    def scorecard(self) -> Dict[str, Any]:
        with self.lock:
            n = len(self.items)
            subs = len(self.subs)
        return {"signals": n, "subscribers": subs}


_F: Optional[SADASFeed] = None
_LOCK = threading.Lock()


def get_sadas() -> SADASFeed:
    global _F
    if _F is None:
        with _LOCK:
            if _F is None:
                _F = SADASFeed()
    return _F
