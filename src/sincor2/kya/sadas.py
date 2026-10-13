"""SADAS feed — paid scorecard, gated unless subscribed."""
from __future__ import annotations

import hmac
import os
import threading
import time
from typing import Any, Dict, List, Optional

from sincor2.kya.store import JsonStore

# Default token lifetime: 90 days, overridable via env.
_DEFAULT_TTL_SECONDS = 90 * 24 * 3600
_ENV_TTL_KEY = "KYA_SADAS_TOKEN_TTL_S"
_MAX_FEED_LIMIT = 200


def _now() -> int:
    return int(time.time())


def _default_ttl_s() -> int:
    try:
        ttl = int(os.environ.get(_ENV_TTL_KEY, "") or _DEFAULT_TTL_SECONDS)
    except (TypeError, ValueError):
        ttl = _DEFAULT_TTL_SECONDS
    return max(ttl, 1)


def _tokens_match(provided: Optional[str], stored: Optional[str]) -> bool:
    """Constant-time token comparison (safe for unequal lengths)."""
    if not provided or not stored:
        return False
    return hmac.compare_digest(
        str(provided).encode("utf-8"), str(stored).encode("utf-8")
    )


class SADASFeed:
    def __init__(self) -> None:
        self.store = JsonStore("sadas")
        raw = self.store.load() or {}
        self.lock = threading.Lock()
        self.items: List[Dict[str, Any]] = raw.get("items") or []
        self.subs: Dict[str, Dict[str, Any]] = raw.get("subs") or {}

    def _persist(self) -> None:
        self.store.save({"items": self.items[-400:], "subs": self.subs, "saved_at": _now()})

    @staticmethod
    def _expired(row: Dict[str, Any]) -> bool:
        expires_at = row.get("expires_at")
        return expires_at is not None and int(expires_at) <= _now()

    def _prune_expired_locked(self) -> int:
        stale = [k for k, row in self.subs.items() if self._expired(row)]
        for k in stale:
            del self.subs[k]
        if stale:
            self._persist()
        return len(stale)

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

    def subscribe(self, kya_id: str, token: str, ttl_s: Optional[int] = None) -> Dict[str, Any]:
        ttl = ttl_s if ttl_s is not None else _default_ttl_s()
        row = {"kya_id": kya_id, "token": token, "ts": _now(), "expires_at": _now() + max(int(ttl), 1)}
        with self.lock:
            self.subs[kya_id] = row
            self._prune_expired_locked()
            self._persist()
        return dict(row)

    def rotate_token(self, kya_id: str, new_token: str, ttl_s: Optional[int] = None) -> Dict[str, Any]:
        """Replace a subscriber's token without touching the subscription itself."""
        with self.lock:
            row = self.subs.get(kya_id)
            if row is None:
                raise KeyError(kya_id)
            if self._expired(row):
                raise KeyError(kya_id)
            ttl = ttl_s if ttl_s is not None else _default_ttl_s()
            row = dict(row)
            row["token"] = new_token
            row["expires_at"] = _now() + max(int(ttl), 1)
            self.subs[kya_id] = row
            self._persist()
        return dict(row)

    def prune_expired(self) -> int:
        """Drop all expired subscriptions. Returns the number removed."""
        with self.lock:
            return self._prune_expired_locked()

    def _authorized(self, token: Optional[str]) -> bool:
        if not token:
            return False
        return any(
            _tokens_match(token, row.get("token")) and not self._expired(row)
            for row in self.subs.values()
        )

    def feed(self, token: Optional[str] = None, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        limit = max(1, min(int(limit or 1), _MAX_FEED_LIMIT))
        offset = max(0, int(offset or 0))
        with self.lock:
            self._prune_expired_locked()
            gated = not self._authorized(token)
            if gated:
                items: List[Dict[str, Any]] = []
            else:
                end = len(self.items) - offset
                start = max(0, end - limit)
                items = list(self.items[max(start, 0):max(end, 0)])
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
