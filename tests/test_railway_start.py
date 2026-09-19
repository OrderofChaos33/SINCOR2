"""railway_start must keep /health cheap and must not hide a failed mvp_app boot."""

from __future__ import annotations

import importlib
import os
import sys
import types

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "src")


def _fresh_module():
    for name in list(sys.modules):
        if name == "railway_start" or name.startswith("railway_start."):
            del sys.modules[name]
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    if SRC not in sys.path:
        sys.path.insert(0, SRC)
    return importlib.import_module("railway_start")


def _call(mod, path):
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = headers

    body = b"".join(mod.wsgi({"PATH_INFO": path}, start_response))
    return captured["status"], body


def test_liveness_does_not_need_mvp():
    mod = _fresh_module()
    status, body = _call(mod, "/health")
    assert status.startswith("200")
    assert b"railway_start" in body
    assert mod._full is None


def test_ready_is_503_until_loaded(monkeypatch):
    mod = _fresh_module()
    monkeypatch.setattr(mod, "start_background_load", lambda: None)
    status, body = _call(mod, "/ready")
    assert status.startswith("503")
    assert b'"loaded":false' in body


def test_boot_exposes_cached_error(monkeypatch):
    mod = _fresh_module()
    monkeypatch.setattr(mod, "start_background_load", lambda: None)
    mod._full_error = "RuntimeError: SECRET_KEY must be set in production"
    mod._attempts = 2
    status, body = _call(mod, "/boot")
    assert status.startswith("200")
    assert b"SECRET_KEY" in body
    assert b'"loaded":false' in body


def test_loaded_app_is_proxied(monkeypatch):
    mod = _fresh_module()
    monkeypatch.setattr(mod, "start_background_load", lambda: None)

    def flask_app(environ, start_response):
        start_response("200 OK", [("Content-Type", "text/html")])
        return [b"<html>buy</html>"]

    mod._full = flask_app
    mod._ready.set()
    status, body = _call(mod, "/buy")
    assert status.startswith("200")
    assert b"<html>buy</html>" in body


def test_load_once_retries_after_failure(monkeypatch):
    mod = _fresh_module()
    hits = {"n": 0}

    real_mvp = types.ModuleType("sincor2.mvp_app")

    def flask_app(environ, start_response):
        start_response("200 OK", [("Content-Type", "text/html")])
        return [b"HOME"]

    orig_import = __import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "sincor2.mvp_app" or (name == "sincor2" and fromlist and "mvp_app" in fromlist):
            hits["n"] += 1
            if hits["n"] == 1:
                raise RuntimeError("SECRET_KEY must be set in production")
            real_mvp.app = flask_app
            sys.modules["sincor2.mvp_app"] = real_mvp
            if name == "sincor2":
                pkg = types.ModuleType("sincor2")
                pkg.mvp_app = real_mvp
                sys.modules["sincor2"] = pkg
                return pkg
            return real_mvp
        return orig_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fake_import)

    assert mod._load_once() is False
    assert mod._full is None
    assert "SECRET_KEY" in (mod._full_error or "")

    assert mod._load_once() is True
    assert mod._full is flask_app
    assert mod._full_error is None


def test_safe_error_redacts_secrets():
    mod = _fresh_module()
    err = mod._safe_error(RuntimeError("bad token=abc123"))
    assert "abc123" not in err
    assert "redacted" in err.lower()
    kept = mod._safe_error(RuntimeError("SECRET_KEY must be set in production"))
    assert "SECRET_KEY" in kept
