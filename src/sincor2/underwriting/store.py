"""Append-only JSONL store for agents, mandates, receipts, revokes."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def _default_dir() -> Path:
    raw = os.environ.get("SINCOR_UNDERWRITE_DIR") or os.environ.get("SINCOR_DATA_DIR")
    if raw:
        return Path(raw) / "underwriting"
    return Path("data") / "underwriting"


class UnderwriteStore:
    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = Path(root) if root else _default_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, name: str) -> Path:
        return self.root / f"{name}.jsonl"

    def append(self, collection: str, record: Dict[str, Any]) -> Dict[str, Any]:
        line = json.dumps(record, separators=(",", ":"), sort_keys=True)
        with self._lock:
            with self._path(collection).open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return record

    def iter(self, collection: str) -> Iterable[Dict[str, Any]]:
        path = self._path(collection)
        if not path.exists():
            return
        with path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    yield json.loads(raw)
                except json.JSONDecodeError:
                    continue

    def list(self, collection: str) -> List[Dict[str, Any]]:
        return list(self.iter(collection))

    def find_one(self, collection: str, key: str, value: Any) -> Optional[Dict[str, Any]]:
        last = None
        for rec in self.iter(collection):
            if rec.get(key) == value:
                last = rec
        return last
