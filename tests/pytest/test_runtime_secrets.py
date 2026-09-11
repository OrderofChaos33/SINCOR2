"""No committed fallback secrets. Production fails closed."""

from __future__ import annotations

from pathlib import Path

import pytest

from sincor2.runtime_secrets import (
    BANNED_SECRETS,
    is_banned_secret,
    is_production_runtime,
    resolve_flask_secret,
    resolve_jwt_secret,
)

_RUNTIME_ROOTS = (
    Path("src/sincor2"),
    Path("verticals"),
    Path("scripts"),
)
_ALLOWED_MENTIONS = {
    Path("src/sincor2/runtime_secrets.py"),
    Path("tests/pytest/test_runtime_secrets.py"),
}


def test_dev_generates_random_not_a_shared_string(monkeypatch):
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
    a = resolve_flask_secret()
    assert len(a) >= 32
    assert a not in BANNED_SECRETS
    assert all(c in "0123456789abcdef" for c in a)


def test_prod_missing_secret_raises(monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        resolve_flask_secret()


def test_prod_rejects_banned_placeholder(monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.setenv("SECRET_KEY", "development-key-change-in-production")
    monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        resolve_flask_secret()


def test_prod_rejects_short_secret(monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.setenv("SECRET_KEY", "tooshort")
    monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="too short"):
        resolve_flask_secret()


def test_prod_uses_env(monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.setenv("SECRET_KEY", "railway-set-secret-key-32chars-min")
    assert resolve_flask_secret() == "railway-set-secret-key-32chars-min"


def test_jwt_reuses_flask_secret_when_jwt_unset(monkeypatch):
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.setenv("SECRET_KEY", "shared-process-secret-key-32chars")
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    assert resolve_jwt_secret() == "shared-process-secret-key-32chars"


def test_railway_counts_as_production(monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.setenv("FLASK_ENV", "development")
    assert is_production_runtime() is True


def test_banned_detects_historical_fallbacks():
    assert is_banned_secret("chroma-demo-secret-change-me")
    assert is_banned_secret("dev-secret-key-CHANGE-IN-PRODUCTION-min-32-chars")
    assert is_banned_secret("")
    assert not is_banned_secret("railway-set-secret-key-32chars-min")


def test_runtime_python_has_no_hardcoded_secret_fallbacks():
    needles = (
        "chroma-demo-secret-change-me",
        "development-key-change-in-production",
        "sincor-secret-key-change-in-production",
        "dev-secret-key-CHANGE-IN-PRODUCTION-min-32-chars",
    )
    hits = []
    for root in _RUNTIME_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if path in _ALLOWED_MENTIONS:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for needle in needles:
                if needle in text:
                    hits.append(f"{path}: {needle}")
    assert hits == []
