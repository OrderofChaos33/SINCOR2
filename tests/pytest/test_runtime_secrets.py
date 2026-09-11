"""No committed fallback secrets. Production fails closed."""

from __future__ import annotations

import pytest

from sincor2.runtime_secrets import (
    is_production_runtime,
    resolve_flask_secret,
    resolve_jwt_secret,
)


def test_dev_generates_random_not_a_shared_string(monkeypatch):
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
    a = resolve_flask_secret()
    assert len(a) >= 32
    assert a != "development-key-change-in-production"
    assert a != "chroma-demo-secret-change-me"
    assert a != "sincor-secret-key-change-in-production"
    assert all(c in "0123456789abcdef" for c in a)


def test_prod_missing_secret_raises(monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
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
