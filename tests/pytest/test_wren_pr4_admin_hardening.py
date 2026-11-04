"""Wren PR 4 — Admin hardening & information leak reduction.

Covers, on the production app (sincor2.mvp_app) unless noted:

  * Dedicated ADMIN_API_KEY: when configured (>= 32 chars) it is the
    ONLY accepted X-Admin-Key value — a key equal to ADMIN_PASSWORD
    returns 401. While unset, the legacy ADMIN_PASSWORD header key
    keeps working (migration posture).
  * Failed-key rate limiting: the 11th bad X-Admin-Key attempt from
    one client address inside a minute returns 429 JSON; other
    addresses are unaffected and valid credentials never count.
  * /command-center page: a non-admin logged-in session (user_email
    only) is refused; an admin session is admitted.
  * /boot (railway_start WSGI): the public payload carries only the
    12-char build SHA + build time — never the full 40-char SHA.
  * command_center blueprint: every rule lives under
    /api/command-center/ or is explicitly allowlisted (the page).
  * polyclaw_scheduler._is_admin_request honors the dedicated key.
"""
from __future__ import annotations

import json
import os

import pytest

STATS = "/api/waitlist/stats"
INTAKES = "/api/admin/intakes"
DEDICATED_KEY = "pr4-dedicated-admin-key-0123456789abcdef"  # 40 chars


@pytest.fixture
def mvp_client():
    os.environ.setdefault("FLASK_ENV", "test")
    os.environ.setdefault("ENVIRONMENT", "test")
    from sincor2.mvp_app import app as mvp_app

    mvp_app.config["TESTING"] = True
    return mvp_app.test_client()


@pytest.fixture(autouse=True)
def _reset_key_rate_limit():
    from sincor2.admin_api_key import reset_admin_key_rate_limit

    reset_admin_key_rate_limit()
    yield
    reset_admin_key_rate_limit()


def _password() -> str:
    return os.environ["ADMIN_PASSWORD"]


# ---------------------------------------------------------------------------
# Dedicated ADMIN_API_KEY
# ---------------------------------------------------------------------------


def test_password_as_key_rejected_when_dedicated_key_configured(
    mvp_client, monkeypatch
):
    monkeypatch.setenv("ADMIN_API_KEY", DEDICATED_KEY)
    r = mvp_client.get(STATS, headers={"X-Admin-Key": _password()})
    assert r.status_code == 401


def test_dedicated_key_accepted_on_waitlist_stats(mvp_client, monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", DEDICATED_KEY)
    r = mvp_client.get(STATS, headers={"X-Admin-Key": DEDICATED_KEY})
    assert r.status_code == 200


def test_dedicated_key_accepted_on_admin_intakes(
    mvp_client, monkeypatch, tmp_path
):
    monkeypatch.setenv("ADMIN_API_KEY", DEDICATED_KEY)
    # Give the authorized handler a readable ledger: a fresh tmp DB
    # with the pack-intakes schema (same setup as test_pack_promises).
    import sqlite3

    from sincor2.payment_ledger import _PACK_INTAKE_SCHEMA

    db_path = tmp_path / "revenue.db"
    monkeypatch.setenv("SINCOR_REVENUE_DB", str(db_path))
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_PACK_INTAKE_SCHEMA)
    conn.commit()
    conn.close()

    r = mvp_client.get(INTAKES, headers={"X-Admin-Key": DEDICATED_KEY})
    assert r.status_code == 200
    assert r.get_json()["count"] == 0
    r = mvp_client.get(INTAKES, headers={"X-Admin-Key": _password()})
    assert r.status_code == 401


def test_password_key_still_works_when_dedicated_key_unset(
    mvp_client, monkeypatch
):
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    r = mvp_client.get(STATS, headers={"X-Admin-Key": _password()})
    assert r.status_code == 200


def test_short_dedicated_key_is_not_a_credential(mvp_client, monkeypatch):
    # < 32 chars: treated as unconfigured — it authenticates nothing,
    # and the legacy password fallback still governs header auth.
    monkeypatch.setenv("ADMIN_API_KEY", "short-key")
    r = mvp_client.get(STATS, headers={"X-Admin-Key": "short-key"})
    assert r.status_code == 401
    r = mvp_client.get(STATS, headers={"X-Admin-Key": _password()})
    assert r.status_code == 200


def test_dedicated_key_on_legacy_app_blueprint(client, monkeypatch):
    # The waitlist blueprint's own gate (legacy app mount) honors the
    # dedicated key independently of mvp_app's before_request guards.
    monkeypatch.setenv("ADMIN_API_KEY", DEDICATED_KEY)
    r = client.get(STATS, headers={"X-Admin-Key": _password()})
    assert r.status_code == 401
    r = client.get(STATS, headers={"X-Admin-Key": DEDICATED_KEY})
    assert r.status_code == 200


def test_polyclaw_admin_check_honors_dedicated_key(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", DEDICATED_KEY)
    from flask import request

    from sincor2 import polyclaw_scheduler
    from sincor2.mvp_app import app as mvp_app

    with mvp_app.test_request_context(
        headers={"X-Admin-Key": DEDICATED_KEY}
    ):
        assert polyclaw_scheduler._is_admin_request(request) is True
    with mvp_app.test_request_context(headers={"X-Admin-Key": _password()}):
        assert polyclaw_scheduler._is_admin_request(request) is False


# ---------------------------------------------------------------------------
# Failed-key rate limiting
# ---------------------------------------------------------------------------


def test_eleventh_bad_key_attempt_returns_429(mvp_client):
    addr = {"REMOTE_ADDR": "198.51.100.23"}
    for attempt in range(10):
        r = mvp_client.get(
            STATS, headers={"X-Admin-Key": "bogus-key"}, environ_base=addr
        )
        assert r.status_code == 401, f"attempt {attempt + 1}"

    r = mvp_client.get(
        STATS, headers={"X-Admin-Key": "bogus-key"}, environ_base=addr
    )
    assert r.status_code == 429
    assert r.get_json() is not None and "error" in r.get_json()

    # Keyed per client address: a different address is unaffected.
    r = mvp_client.get(
        STATS,
        headers={"X-Admin-Key": "bogus-key"},
        environ_base={"REMOTE_ADDR": "198.51.100.24"},
    )
    assert r.status_code == 401

    # Only FAILED attempts count: valid credentials from the limited
    # address still pass.
    r = mvp_client.get(
        STATS, headers={"X-Admin-Key": _password()}, environ_base=addr
    )
    assert r.status_code == 200


def test_valid_key_attempts_never_rate_limited(mvp_client):
    addr = {"REMOTE_ADDR": "198.51.100.25"}
    for _ in range(12):
        r = mvp_client.get(
            STATS, headers={"X-Admin-Key": _password()}, environ_base=addr
        )
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# /command-center page: admin session only
# ---------------------------------------------------------------------------


def test_command_center_page_rejects_non_admin_logged_in_user(mvp_client):
    with mvp_client.session_transaction() as sess:
        sess["user_email"] = "customer@example.com"
        sess["is_admin"] = False
    r = mvp_client.get("/command-center")
    assert r.status_code == 302
    assert r.headers["Location"].startswith("/login")


def test_command_center_page_admits_admin_session(mvp_client):
    with mvp_client.session_transaction() as sess:
        sess["is_admin"] = True
        sess["admin_username"] = os.environ.get("ADMIN_USERNAME", "admin")
    r = mvp_client.get("/command-center")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# /boot: public payload carries only the short SHA
# ---------------------------------------------------------------------------


def _call_wsgi(path):
    import railway_start

    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    body = b"".join(
        railway_start.wsgi(
            {"PATH_INFO": path, "REQUEST_METHOD": "GET"}, start_response
        )
    )
    return captured["status"], body


def test_boot_endpoint_returns_only_short_sha(monkeypatch):
    fake_sha = "b" * 40
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", fake_sha)
    status, body = _call_wsgi("/boot")
    assert status == "200 OK"
    payload = json.loads(body)
    build = payload["build"]
    assert build["short"] == fake_sha[:12]
    assert len(build["short"]) == 12
    assert build["time"]
    # The full 40-char SHA appears nowhere in the public payload.
    assert fake_sha not in body.decode("utf-8")
    assert build.get("sha", build["short"]) == build["short"]


# ---------------------------------------------------------------------------
# command_center blueprint rule surface
# ---------------------------------------------------------------------------

_CC_PAGE_ALLOWLIST = {"/command-center"}


def test_command_center_blueprint_rules_all_prefixed_or_allowlisted():
    from sincor2.mvp_app import app as mvp_app

    rules = [
        rule
        for rule in mvp_app.url_map.iter_rules()
        if rule.endpoint.startswith("command_center.")
    ]
    # Non-vacuous: the blueprint's API surface is really mounted.
    assert any("killswitch" in rule.rule for rule in rules)
    assert len(rules) >= 6
    for rule in rules:
        assert rule.rule.startswith("/api/command-center/") or (
            rule.rule in _CC_PAGE_ALLOWLIST
        ), f"unlisted command_center rule: {rule.rule}"
