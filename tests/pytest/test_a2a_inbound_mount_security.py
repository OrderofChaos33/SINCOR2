"""w55 (P3 item 26): no dormant signing secrets; mount()/register() idempotent.

Covers G2.20 (DEMO_SECRET removal + fail-closed sign_payload) and G2.17
(blueprint re-registration guard). Run with the standard A2A env; does not
touch the network, keys, or production state.
"""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from flask import Flask

from sincor2 import a2a_inbound
from sincor2.a2a_inbound import register as register_inbound
from sincor2.a2a_inbound import reset_fabric, sign_payload


def test_demo_secret_removed():
    """DEMO_SECRET and its hardcoded 'sincor-a2a-demo' fallback are gone."""
    assert not hasattr(a2a_inbound, "DEMO_SECRET")
    with open(a2a_inbound.__file__, encoding="utf-8") as fh:
        src = fh.read()
    assert "sincor-a2a-demo" not in src
    assert "DEMO_SECRET" not in src


def test_dead_registered_flag_removed():
    """The never-read _REGISTERED flag is gone; idempotence is per-app."""
    assert not hasattr(a2a_inbound, "_REGISTERED")


def test_sign_payload_fails_closed_without_env(monkeypatch):
    """No env secret and no explicit secret -> raise, never sign with a fallback."""
    monkeypatch.delenv("SINCOR_A2A_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="SINCOR_A2A_SECRET"):
        sign_payload({"a": 1})


def test_sign_payload_fails_closed_empty_env(monkeypatch):
    """Empty env value is treated the same as unset."""
    monkeypatch.setenv("SINCOR_A2A_SECRET", "   ")
    with pytest.raises(RuntimeError, match="SINCOR_A2A_SECRET"):
        sign_payload({"a": 1})


def test_sign_payload_explicit_secret_unchanged():
    """Explicit callers keep byte-identical HMAC behaviour ('signature' excluded)."""
    payload = {"b": 2, "a": 1, "signature": "ignored"}
    body = json.dumps({"a": 1, "b": 2}, separators=(",", ":"), sort_keys=True)
    expected = hmac.new(b"test-secret", body.encode(), hashlib.sha256).hexdigest()
    assert sign_payload(payload, "test-secret") == expected


def test_sign_payload_env_secret(monkeypatch):
    """Env-supplied secret is used when no explicit secret is passed."""
    monkeypatch.setenv("SINCOR_A2A_SECRET", "env-secret-abc")
    payload = {"b": 2, "a": 1, "signature": "ignored"}
    body = json.dumps({"a": 1, "b": 2}, separators=(",", ":"), sort_keys=True)
    expected = hmac.new(b"env-secret-abc", body.encode(), hashlib.sha256).hexdigest()
    assert sign_payload(payload) == expected


def _mounted_app(name: str):
    reset_fabric()
    app = Flask(name)
    register_inbound(app)
    return app


def test_double_mount_is_safe():
    """Second register()/mount() on the same app is a no-op (no ValueError,
    no duplicated before_request hooks, no re-seed)."""
    app = _mounted_app("w55-double")
    register_inbound(app)  # would raise ValueError on duplicate blueprint name before the fix
    assert "a2a_inbound" in (app.blueprints or {})
    client = app.test_client()
    resp = client.get("/v1/a2a/agents")
    assert resp.status_code == 200


def test_mount_guard_is_per_app_not_global():
    """A fresh app still mounts independently (guard is not a module-global flag)."""
    app1 = _mounted_app("w55-app1")
    app2 = _mounted_app("w55-app2")
    assert "a2a_inbound" in (app1.blueprints or {})
    assert "a2a_inbound" in (app2.blueprints or {})
    assert app2.test_client().get("/v1/a2a/agents").status_code == 200
