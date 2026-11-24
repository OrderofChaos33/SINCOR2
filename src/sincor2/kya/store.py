"""JSON persistence under SINCOR_DATA_DIR/kya or data/kya.

Backed by :class:`sincor2.locked_store.LockedJsonStore` (Phase 0/1 of the
shared-persistence migration, 2026-11-23): saves are flock-serialized and
atomic (pid-unique tmp + fsync + os.replace), which removes the torn-write
hazard behind W-203/W-214, and :meth:`JsonStore.update` gives callers the
reload-under-lock mutate path that closes the lost-update window. The
public API, constructor signature, and on-disk JSON format (compact
separators, sorted keys) are unchanged from the original implementation.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from sincor2.locked_store import LockedJsonStore


def _data_dir() -> Path:
    explicit = os.environ.get("SINCOR_DATA_DIR", "").strip()
    if explicit:
        base = Path(explicit) / "kya"
        base.mkdir(parents=True, exist_ok=True)
        return base
    try:
        from sincor2.data_paths import data_dir

        return data_dir() / "kya"
    except Exception:
        base = Path(__file__).resolve().parents[3] / "data" / "kya"
        base.mkdir(parents=True, exist_ok=True)
        return base


class JsonStore:
    def __init__(self, name: str) -> None:
        self.path = _data_dir() / f"{name}.json"
        self.lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._locked = LockedJsonStore(self.path, default=None, fail_closed=True)

    def load(self) -> Any:
        try:
            return self._locked.read()
        except Exception:
            return None

    def save(self, payload: Any) -> None:
        self._locked.write(payload)

    def update(self, mutator: Callable[[Any], Optional[Any]]) -> Any:
        """Reload-under-lock, apply ``mutator``, persist, return new state."""
        return self._locked.mutate(mutator)
