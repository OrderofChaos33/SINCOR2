"""P2-A3: every credential verifier gets rate limiting + progressive backoff.

Covered verifiers: legacy JSON login (/api/auth/login on sincor2.app),
mvp JSON login (/api/auth/login on mvp_app), mvp form login (POST /login).

- rapid failed attempts -> 429 (stacked IP + per-account limits)
- legitimate login still works (different IP / after success the failure
  counter clears)
- progressive backoff delays grow: 1s, 2s, 4s, 8s(cap), ...
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch):
    # Route tests assert on 429s; backoff delay growth is unit-tested
    # separately with a captured sleeper. Disable real sleeping here.
    monkeypatch.setenv("SINCOR_LOGIN_BACKOFF_MAX_SECONDS", "0")


@pytest.fixture
def mvp_client():
    os.environ.setdefault("FLASK_ENV", "test")
    os.environ.setdefault("ENVIRONMENT", "test")
    os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key-32-char-minimum-ok")
    os.environ.setdefault("ADMIN_USERNAME", "admin")
    os.environ.setdefault("ADMIN_PASSWORD", "admin-password-32-char-minimum-ok")
    from sincor2.mvp_app import app as mvp_app

    mvp_app.config["TESTING"] = True
    mvp_app.config["SERVER_NAME"] = None
    return mvp_app.test_client()


ADMIN_USER = "admin"
ADMIN_PASS = "admin-password-32-char-minimum-ok"


def test_legacy_json_login_rapid_failures_throttled(client):
    ip = "10.21.0.1"
    codes = []
    for _ in range(6):
        r = client.post(
            "/api/auth/login",
            json={"username": ADMIN_USER, "password": "wrong-password"},
            environ_base={"REMOTE_ADDR": ip},
        )
        codes.append(r.status_code)
    assert codes[:5] == [401] * 5
    assert codes[5] == 429  # 5 per minute per-IP rule trips
    # legitimate login from a different IP still works
    ok = client.post(
        "/api/auth/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASS},
        environ_base={"REMOTE_ADDR": "10.21.0.2"},
    )
    assert ok.status_code == 200
    assert ok.get_json()["access_token"]


def test_mvp_json_login_rapid_failures_throttled(mvp_client):
    ip = "10.22.0.1"
    codes = []
    for _ in range(6):
        r = mvp_client.post(
            "/api/auth/login",
            json={"username": ADMIN_USER, "password": "wrong-password"},
            environ_base={"REMOTE_ADDR": ip},
        )
        codes.append(r.status_code)
    assert codes[:5] == [401] * 5
    assert codes[5] == 429
    ok = mvp_client.post(
        "/api/auth/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASS},
        environ_base={"REMOTE_ADDR": "10.22.0.2"},
    )
    assert ok.status_code == 200
    assert ok.get_json()["access_token"]


def test_mvp_form_login_rapid_failures_throttled(mvp_client):
    ip = "10.23.0.1"
    codes = []
    for _ in range(6):
        r = mvp_client.post(
            "/login",
            data={"identifier": ADMIN_USER, "password": "wrong-password"},
            environ_base={"REMOTE_ADDR": ip},
        )
        codes.append(r.status_code)
    # P1 (#442): failed form logins now hit the disabled-customer path (403),
    # not 401. Throttling (429 on 6th) is the P2-A3 behavior under test.
    assert codes[:5] == [403] * 5
    assert codes[5] == 429
    # legitimate admin form login still works from another IP (302 -> /admin)
    ok = mvp_client.post(
        "/login",
        data={"identifier": ADMIN_USER, "password": ADMIN_PASS},
        environ_base={"REMOTE_ADDR": "10.23.0.2"},
    )
    assert ok.status_code == 302
    assert "/admin" in ok.headers.get("Location", "")


def test_form_login_get_not_counted_against_post_budget(mvp_client):
    ip = "10.24.0.1"
    for _ in range(10):
        r = mvp_client.get("/login", environ_base={"REMOTE_ADDR": ip})
        assert r.status_code == 200
    # POST budget untouched by the GETs: first POST attempt is processed (403
    # per P1 disabled-customer path), not pre-throttled.
    r = mvp_client.post(
        "/login",
        data={"identifier": ADMIN_USER, "password": "wrong-password"},
        environ_base={"REMOTE_ADDR": ip},
    )
    assert r.status_code == 403


def test_per_account_limit_contains_distributed_attack(client):
    # Same identifier hammered from many IPs: the per-account rule
    # (30 per 10 minutes) trips even though no single IP exceeds 5/min.
    codes = []
    for i in range(31):
        r = client.post(
            "/api/auth/login",
            json={"username": "target-user-a3", "password": "wrong-password"},
            environ_base={"REMOTE_ADDR": f"10.25.{i // 250}.{i % 250 + 1}"},
        )
        codes.append(r.status_code)
    assert 429 in codes
    assert codes[-1] == 429


def test_backoff_delays_grow_progressively(monkeypatch):
    from sincor2 import login_throttle

    slept = []
    monkeypatch.setattr(login_throttle, "_sleep", slept.append)
    monkeypatch.delenv("SINCOR_LOGIN_BACKOFF_MAX_SECONDS", raising=False)

    ident = "backoff-probe-user"
    delays = [login_throttle.note_login_failure(ident) for _ in range(6)]
    assert delays == [1.0, 2.0, 4.0, 8.0, 8.0, 8.0]
    assert slept == delays

    # a successful login clears the counter: next failure starts over at 1s
    login_throttle.note_login_success(ident)
    assert login_throttle.note_login_failure(ident) == 1.0


def test_backoff_never_raises_without_storage(monkeypatch):
    from sincor2 import login_throttle

    def _boom():
        raise RuntimeError("storage down")

    monkeypatch.setattr(login_throttle, "_backoff_storage", _boom)
    assert login_throttle.note_login_failure("anyone") >= 0
    login_throttle.note_login_success("anyone")  # must not raise
