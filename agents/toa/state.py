from __future__ import annotations

"""Lightweight persistent state store for TOA sessions.

Provides a simple key-value store backed by a JSON file (when
:pyattr:`TOAConfig.state_path` is set) or held in-memory only.  The store
supports:

* Arbitrary JSON-serializable values.
* Atomic writes (write-to-temp then rename).
* Session versioning via a monotonic counter.
"""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional

logger = logging.getLogger("sincor.toa.state")


class TOAStateStore:
    """Thread-safe key-value store for persisting TOA session data.

    Parameters
    ----------
    path:
        File path for persistence.  Pass an empty string or ``None`` to run
        in memory-only mode (state is lost between process restarts).
    """

    _SCHEMA_VERSION = 1

    def __init__(self, path: Optional[str] = None) -> None:
        self._path: Optional[Path] = Path(path) if path else None
        self._lock = Lock()
        self._data: Dict[str, Any] = {}
        self._version: int = 0
        if self._path and self._path.exists():
            self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """Return the value stored at *key*, or *default* if not present."""
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Store *value* at *key* and persist if a path is configured."""
        with self._lock:
            self._data[key] = value
            self._version += 1
            self._persist_locked()

    def delete(self, key: str) -> bool:
        """Remove *key* from the store.  Returns ``True`` if it existed."""
        with self._lock:
            existed = key in self._data
            if existed:
                del self._data[key]
                self._version += 1
                self._persist_locked()
            return existed

    def all(self) -> Dict[str, Any]:
        """Return a shallow copy of the entire store contents."""
        with self._lock:
            return dict(self._data)

    def reset(self) -> None:
        """Clear all stored data and persist the empty state."""
        with self._lock:
            self._data = {}
            self._version = 0
            self._persist_locked()

    @property
    def version(self) -> int:
        """Monotonically increasing version counter (incremented on each write)."""
        return self._version

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _persist_locked(self) -> None:
        """Write state to disk atomically (caller must hold ``_lock``)."""
        if self._path is None:
            return
        payload = {
            "schema_version": self._SCHEMA_VERSION,
            "version": self._version,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "data": self._data,
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, indent=2, default=str)
                os.replace(tmp, str(self._path))
            except Exception:
                os.unlink(tmp)
                raise
        except OSError as exc:
            logger.warning("toa_state: failed to persist state: %s", exc)

    def _load(self) -> None:
        """Load persisted state from disk."""
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))  # type: ignore[union-attr]
            self._data = raw.get("data", {})
            self._version = int(raw.get("version", 0))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("toa_state: could not load state from %s: %s", self._path, exc)
            self._data = {}
            self._version = 0
