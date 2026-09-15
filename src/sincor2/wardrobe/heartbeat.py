"""In-process heartbeat register. Stale agents suspend."""

from __future__ import annotations

import threading
import time
from typing import Any

_LOCK = threading.Lock()
_BEATS: dict[str, dict[str, Any]] = {}


def record_heartbeat(agent_id: str, *, status: str = "Active", extra: dict | None = None) -> dict[str, Any]:
    row = {
        "agent_id": agent_id,
        "status": status,
        "ts": time.time(),
        "extra": extra or {},
    }
    with _LOCK:
        _BEATS[agent_id] = row
    return row


def stale_ids(now: float | None = None) -> list[str]:
    now = now if now is not None else time.time()
    out: list[str] = []
    with _LOCK:
        items = list(_BEATS.items())
    for agent_id, row in items:
        interval = float((row.get("extra") or {}).get("stale_after_s") or 180)
        if now - float(row["ts"]) > interval:
            out.append(agent_id)
    return out


def get(agent_id: str) -> dict[str, Any] | None:
    with _LOCK:
        row = _BEATS.get(agent_id)
        return dict(row) if row else None


def live_count() -> int:
    stale = set(stale_ids())
    with _LOCK:
        return sum(1 for aid in _BEATS if aid not in stale)
