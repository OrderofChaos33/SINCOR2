"""JSON persistence under SINCOR_DATA_DIR/kya or data/kya."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any


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

    def load(self) -> Any:
        if not self.path.is_file():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def save(self, payload: Any) -> None:
        self.path.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True), encoding="utf-8")
