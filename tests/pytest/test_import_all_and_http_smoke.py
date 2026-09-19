"""Pin the gunicorn boot path: imports + /health + / + /api/price/official."""
from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "sincor2"


def _top_level_modules() -> list[str]:
    names = []
    for item in pkgutil.iter_modules([str(SRC)]):
        if item.name.startswith("_"):
            continue
        names.append(item.name)
    return sorted(names)


def test_pages_modules_are_not_truncated() -> None:
    for rel in (
        "blueprints/pages.py",
        "mvp_blueprints/pages.py",
        "mvp_app.py",
        "mvp_blueprints/health.py",
        "mvp_blueprints/sinc.py",
    ):
        path = SRC / rel
        assert path.is_file(), rel
        source = path.read_text(encoding="utf-8")
        assert len(source) > 200, f"{rel} looks truncated ({len(source)} bytes)"
        ast.parse(source)


def test_critical_modules_import() -> None:
    for name in ("onchain", "onchain.live_snapshot", "onchain.constants", "settings"):
        importlib.import_module(f"sincor2.{name}")


def test_gunicorn_boot_smoke_routes() -> None:
    pytest.importorskip("flask")
    from sincor2.mvp_app import app

    client = app.test_client()
    health = client.get("/health")
    assert health.status_code == 200, health.data
    body = health.get_json() or {}
    assert body.get("status") in {"healthy", "degraded"}

    home = client.get("/")
    assert home.status_code in {200, 302}

    price = client.get("/api/price/official")
    assert price.status_code == 200, price.data
    payload = price.get_json() or {}
    assert payload.get("official_floor_usd") == 0.15
    assert "onchain" in payload or payload.get("sinc_holders") is not None
