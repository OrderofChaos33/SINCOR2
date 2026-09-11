from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

os.environ.setdefault("FLASK_ENV", "test")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.pop("CHROMA_LIVE_SEND", None)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("CHROMA_LIVE_SEND", raising=False)
    monkeypatch.setenv("CHROMA_DB_PATH", str(tmp_path / "chroma.db"))
    from verticals.auto_detailing.store import reset_store

    return reset_store(tmp_path / "chroma.db")
