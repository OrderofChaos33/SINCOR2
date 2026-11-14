"""Counts-only public signup endpoint + homepage cohort counter wiring.

GET /api/genesis/counts is the public, unauthenticated companion to the
admin-gated GET /api/waitlist/stats: it returns AGGREGATE COUNTS ONLY
(confirmed genesis members + waitlist signups), sourced read-only from
the same counters the admin surfaces use:

  * genesis total  <- sincor2.genesis_cohort.genesis_count()
  * waitlist total <- WaitlistManager.get_signup_stats()["total_signups"]
    (aggregates-only by construction; the manager is read through
    app.extensions exactly like blueprints/waitlist.py does)

No emails, names, wallet addresses, or per-record data may appear in
the response — the canary test seeds records carrying unique marker
strings and asserts none of them occur anywhere in the response body.

The homepage test pins the genesis gate's cohort counter to the same
counting logic: seeded confirmed members must render as the
zero-padded cohort number, never a hardcoded placeholder.
"""
from __future__ import annotations

import os
import sqlite3

import pytest

CANARY_EMAIL = "canary-alice-7f3d@example.com"
CANARY_EMAIL_2 = "canary-bob-9e1a@example.com"
CANARY_WALLET = "0x" + "ee" * 20
CANARY_WALLET_2 = "0x" + "dd" * 20
CANARY_COMPANY = "Canary Holdings 4c2b LLC"
CANARY_WAITLIST_EMAIL = "canary-waitlist-51f0@example.com"


@pytest.fixture
def mvp_client(monkeypatch):
    os.environ.setdefault("FLASK_ENV", "test")
    os.environ.setdefault("ENVIRONMENT", "test")
    os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key-32-char-minimum-ok")
    os.environ.setdefault("ADMIN_USERNAME", "admin")
    os.environ.setdefault("ADMIN_PASSWORD", "admin-password-32-char-minimum-ok")
    from sincor2.mvp_app import app, limiter

    limiter.reset()
    app.config["TESTING"] = True
    app.config["SERVER_NAME"] = None
    yield app.test_client()
    limiter.reset()


@pytest.fixture
def genesis_store(tmp_path, monkeypatch):
    """Point the genesis cohort store at a tmp dir; return its module."""
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    from sincor2 import genesis_cohort

    return genesis_cohort


def _seed_genesis_members(tmp_path, rows):
    """Insert confirmed genesis members directly (bypasses claim email).

    rows: list of (email, wallet). Ensures the schema exists first via
    the module's own counter, exactly like the production store layout.
    """
    from sincor2 import genesis_cohort

    genesis_cohort.genesis_count()  # creates genesis_cohort.db + tables
    db = sqlite3.connect(str(tmp_path / "genesis_cohort.db"))
    db.executemany(
        "INSERT INTO genesis_cohort (email, wallet, created_at) VALUES (?, ?, ?)",
        [(email, wallet, "2026-11-08T00:00:00+00:00") for email, wallet in rows],
    )
    db.commit()
    db.close()


@pytest.fixture
def waitlist_store(tmp_path, monkeypatch):
    """Swap in a tmp-backed WaitlistManager as the app extension."""
    from sincor2.mvp_app import app
    from sincor2.waitlist_system import WaitlistManager

    manager = WaitlistManager(db_path=str(tmp_path / "waitlist_test.db"))
    monkeypatch.setitem(app.extensions, "waitlist_manager", manager)
    return manager


def _seed_waitlist_signups(manager, rows):
    """Insert waitlist rows directly. rows: list of (email, company)."""
    db = sqlite3.connect(manager.db_path)
    db.executemany(
        "INSERT INTO waitlist (email_hash, encrypted_email, product_interest,"
        " company_name) VALUES (?, ?, ?, ?)",
        [
            (
                manager.hash_email(email),
                manager.encrypt_email(email),
                "Growth Engine",
                company,
            )
            for email, company in rows
        ],
    )
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Endpoint: counts match seeded data
# ---------------------------------------------------------------------------

def test_counts_endpoint_returns_seeded_counts(
    mvp_client, genesis_store, waitlist_store, tmp_path
):
    _seed_genesis_members(
        tmp_path,
        [
            (CANARY_EMAIL, CANARY_WALLET),
            (CANARY_EMAIL_2, CANARY_WALLET_2),
            ("member-three@example.com", "0x" + "cc" * 20),
        ],
    )
    _seed_waitlist_signups(
        waitlist_store,
        [(CANARY_WAITLIST_EMAIL, CANARY_COMPANY), ("second@example.com", "Other Co")],
    )

    resp = mvp_client.get("/api/genesis/counts")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["genesis_signups"] == 3
    assert payload["waitlist_signups"] == 2
    # Sibling public endpoints (/api/price/official) are CDN-cacheable.
    assert "max-age=60" in (resp.headers.get("Cache-Control") or "")


def test_counts_endpoint_empty_store_returns_zeros(
    mvp_client, genesis_store, waitlist_store
):
    resp = mvp_client.get("/api/genesis/counts")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["genesis_signups"] == 0
    assert payload["waitlist_signups"] == 0


# ---------------------------------------------------------------------------
# Endpoint: privacy — aggregates only, canaries never leak
# ---------------------------------------------------------------------------

def test_counts_endpoint_leaks_no_canary_pii(
    mvp_client, genesis_store, waitlist_store, tmp_path
):
    _seed_genesis_members(
        tmp_path,
        [(CANARY_EMAIL, CANARY_WALLET), (CANARY_EMAIL_2, CANARY_WALLET_2)],
    )
    _seed_waitlist_signups(waitlist_store, [(CANARY_WAITLIST_EMAIL, CANARY_COMPANY)])

    resp = mvp_client.get("/api/genesis/counts")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    for canary in (
        CANARY_EMAIL,
        CANARY_EMAIL_2,
        CANARY_WALLET,
        CANARY_WALLET_2,
        CANARY_COMPANY,
        CANARY_WAITLIST_EMAIL,
    ):
        assert canary not in body, f"canary leaked in counts response: {canary}"
    # The payload is counts and nothing else: every value is an int.
    payload = resp.get_json()
    assert payload, "counts payload must not be empty"
    assert all(isinstance(v, int) for v in payload.values()), payload


# ---------------------------------------------------------------------------
# Homepage: cohort counter renders the real seeded count
# ---------------------------------------------------------------------------

def test_homepage_renders_seeded_genesis_count(
    mvp_client, genesis_store, tmp_path, monkeypatch
):
    # Pin the launch gate on regardless of the calendar date the suite
    # runs on, so the gate (and its cohort counter) is what renders.
    monkeypatch.setenv("SINCOR_LAUNCH_DATE", "2099-01-01")
    _seed_genesis_members(
        tmp_path,
        [(f"member-{i}@example.com", "0x" + "ab" * 20) for i in range(7)],
    )

    resp = mvp_client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # genesis_gate.html renders the counter zero-padded to 5 digits.
    assert '<b id="count">00007</b>' in body
