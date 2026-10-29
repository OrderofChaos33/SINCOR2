"""Admin-gated waitlist signup stats: GET /api/waitlist/stats.

The founder needs to SEE the website signup number. Until now it was
trapped in the production SQLite file: the waitlist blueprint was
mounted only by the legacy app, so the production app served no
/api/waitlist/* routes at all, and the legacy read paths returned
product rollups / per-user rows — never a plain count.

This endpoint returns AGGREGATES ONLY (total signups, verified
signups, latest signup timestamp), computed directly from the waitlist
table, behind the production admin gate (_require_admin semantics):

  * anonymous / non-admin / forged session -> 401, no counts anywhere
  * admin session / admin JWT / admin key  -> 200, exact aggregates
  * empty waitlist                         -> 200, zeros + null latest
  * manager extension unavailable          -> 503, but only AFTER the
                                              admin gate (anon -> 401)

It must hold on BOTH apps: the legacy app (conftest ``client``) and the
production app (sincor2.mvp_app, served by railway_start).
"""
from __future__ import annotations

import os
import sqlite3

import pytest

STATS = "/api/waitlist/stats"

# (email_hash, is_verified, signup_date) — 5 signups, 2 verified,
# latest = carol's row. Hashes are seed markers: they must never appear
# in any response body.
SEED_ROWS = [
    ("seedhash-alice", 1, "2026-10-20 09:15:00"),
    ("seedhash-bob", 0, "2026-10-25 12:00:00"),
    ("seedhash-carol", 1, "2026-10-28 22:41:03"),
    ("seedhash-dan", 0, "2026-10-21 08:00:00"),
    ("seedhash-erin", 0, "2026-10-22 08:00:00"),
]
EXPECTED = {
    "total_signups": 5,
    "verified_signups": 2,
    "latest_signup_at": "2026-10-28 22:41:03",
}
EMPTY = {"total_signups": 0, "verified_signups": 0, "latest_signup_at": None}


def _insert_rows(db_path, rows=SEED_ROWS):
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO waitlist"
            " (email_hash, encrypted_email, product_interest, is_verified, signup_date)"
            " VALUES (?, 'encrypted-seed', 'sincor', ?, ?)",
            [(h, v, d) for h, v, d in rows],
        )


def _seed_isolated_manager():
    """Seed the conftest-isolated waitlist DB (fresh per test)."""
    from sincor2 import waitlist_system

    _insert_rows(waitlist_system.waitlist_manager.db_path)


def _admin_session(client):
    with client.session_transaction() as sess:
        sess["is_admin"] = True
        sess["admin_username"] = os.environ.get("ADMIN_USERNAME", "admin")


def _assert_no_counts(response):
    body = response.get_data(as_text=True)
    assert "total_signups" not in body
    assert "verified_signups" not in body
    assert "latest_signup_at" not in body


def _assert_no_pii(response):
    body = response.get_data(as_text=True)
    for marker, _v, _d in SEED_ROWS:
        assert marker not in body

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                lowered = str(key).lower()
                assert "email" not in lowered
                assert "hash" not in lowered
                assert "company" not in lowered
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(response.get_json())


# ---------------------------------------------------------------------------
# Legacy app (conftest client): the gate
# ---------------------------------------------------------------------------


def test_stats_anonymous_refused_no_counts(client):
    _seed_isolated_manager()
    response = client.get(STATS)
    assert response.status_code in (401, 403)
    _assert_no_counts(response)


def test_stats_non_admin_session_refused(client):
    _seed_isolated_manager()
    with client.session_transaction() as sess:
        sess["user_email"] = "customer@example.com"
        sess["is_admin"] = False
    response = client.get(STATS)
    assert response.status_code in (401, 403)
    _assert_no_counts(response)


def test_stats_forged_admin_session_wrong_username_refused(client):
    # is_admin flag alone must not suffice: the session username must
    # match the configured admin identity (the _is_admin_session contract).
    _seed_isolated_manager()
    with client.session_transaction() as sess:
        sess["is_admin"] = True
        sess["admin_username"] = "not-the-admin"
    response = client.get(STATS)
    assert response.status_code in (401, 403)
    _assert_no_counts(response)


# ---------------------------------------------------------------------------
# Legacy app: admin paths return exact aggregates
# ---------------------------------------------------------------------------


def test_stats_admin_session_exact_counts(client):
    _seed_isolated_manager()
    _admin_session(client)
    response = client.get(STATS)
    assert response.status_code == 200
    assert response.get_json() == EXPECTED
    _assert_no_pii(response)


def test_stats_admin_jwt_exact_counts(client, auth_headers):
    _seed_isolated_manager()
    response = client.get(STATS, headers=auth_headers)
    assert response.status_code == 200
    assert response.get_json() == EXPECTED


def test_stats_admin_key_exact_counts(client):
    _seed_isolated_manager()
    response = client.get(
        STATS, headers={"X-Admin-Key": os.environ["ADMIN_PASSWORD"]}
    )
    assert response.status_code == 200
    assert response.get_json() == EXPECTED


def test_stats_empty_db_zero_counts(client):
    _admin_session(client)
    response = client.get(STATS)
    assert response.status_code == 200
    assert response.get_json() == EMPTY


def test_stats_manager_unavailable_503_after_gate(app, client):
    _seed_isolated_manager()
    app.extensions.pop("waitlist_manager", None)

    # Anonymous still hits the gate first: 401, never the 503 path.
    anon = client.get(STATS)
    assert anon.status_code in (401, 403)
    _assert_no_counts(anon)

    _admin_session(client)
    response = client.get(STATS)
    assert response.status_code == 503
    assert response.get_json()["code"] == "waitlist_unavailable"


# ---------------------------------------------------------------------------
# Production app (sincor2.mvp_app via railway_start)
# ---------------------------------------------------------------------------


@pytest.fixture
def mvp_client():
    os.environ.setdefault("FLASK_ENV", "test")
    os.environ.setdefault("ENVIRONMENT", "test")
    from sincor2.mvp_app import app as mvp_app

    mvp_app.config["TESTING"] = True
    return mvp_app.test_client()


def test_production_app_registers_stats_route():
    from sincor2.mvp_app import app as mvp_app

    rules = {
        (rule.rule, method)
        for rule in mvp_app.url_map.iter_rules()
        for method in (rule.methods or ())
    }
    assert ("/api/waitlist/stats", "GET") in rules
    assert "waitlist_manager" in mvp_app.extensions


def test_production_stats_anonymous_refused(mvp_client):
    response = mvp_client.get(STATS)
    assert response.status_code in (401, 403)
    _assert_no_counts(response)


def test_production_stats_admin_exact_counts(mvp_client, tmp_path):
    from sincor2 import waitlist_system
    from sincor2.mvp_app import app as mvp_app

    seeded = waitlist_system.WaitlistManager(db_path=str(tmp_path / "prod_waitlist.db"))
    _insert_rows(seeded.db_path)

    original = mvp_app.extensions.get("waitlist_manager")
    mvp_app.extensions["waitlist_manager"] = seeded
    try:
        _admin_session(mvp_client)
        response = mvp_client.get(STATS)
        assert response.status_code == 200
        assert response.get_json() == EXPECTED
        _assert_no_pii(response)
    finally:
        if original is None:
            mvp_app.extensions.pop("waitlist_manager", None)
        else:
            mvp_app.extensions["waitlist_manager"] = original
