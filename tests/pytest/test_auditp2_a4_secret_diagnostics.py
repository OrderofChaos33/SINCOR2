"""P2-A4: diagnostic entrypoints never print secrets.

The auth_system test_auth() diagnostic (including its ``__main__``
entrypoint) must report credential PRESENCE (SET/UNSET sentinels) and
never the configured values. Tests use synthetic sentinel secrets only —
this code path is never exercised against real secrets.
"""
from __future__ import annotations

import runpy

SENTINEL_PASSWORD = "-".join(["sentinel", "admin", "pw"])
SENTINEL_JWT = "s" * 40
SENTINEL_USER = "sentinel-admin-user"


def _seed_sentinels(monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", SENTINEL_USER)
    monkeypatch.setenv("ADMIN_PASSWORD", SENTINEL_PASSWORD)
    monkeypatch.setenv("JWT_SECRET_KEY", SENTINEL_JWT)


def _assert_no_secret_leak(out: str):
    assert "SET" in out  # presence marker still reported
    assert SENTINEL_PASSWORD not in out
    assert SENTINEL_JWT not in out
    assert SENTINEL_USER not in out
    assert "changeme123" not in out  # no fallback value either


def test_auth_diagnostic_prints_presence_not_value(monkeypatch, capsys):
    _seed_sentinels(monkeypatch)
    from sincor2 import auth_system

    auth_system.test_auth()
    _assert_no_secret_leak(capsys.readouterr().out)


def test_auth_main_entrypoint_prints_presence_not_value(monkeypatch, capsys):
    _seed_sentinels(monkeypatch)
    runpy.run_module("sincor2.auth_system", run_name="__main__")
    _assert_no_secret_leak(capsys.readouterr().out)


def test_auth_diagnostic_unset_markers(monkeypatch, capsys):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)
    from sincor2 import auth_system

    auth_system.test_auth()
    out = capsys.readouterr().out
    assert "UNSET" in out
    assert "changeme123" not in out
