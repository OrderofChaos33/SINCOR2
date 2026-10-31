"""WREN PR 2 — airdrop registration persistence.

POST /api/airdrop/register used to validate the wallet, log it, return
201 "Successfully registered" — and persist NOTHING. Every Genesis
registrant would have been lost. The sin-airdrop.html template also
saved to localStorage and showed success even when the API failed.

Pinned here:
  * valid wallet      -> 201 and the row EXISTS in the SQLite store
                         (wallet lowercased, created_at set, ip stored
                         only as a hash — never the raw IP)
  * duplicate wallet  -> 200 {"status": "already_registered"}, still
                         exactly one row (case-insensitive)
  * invalid wallet    -> 400 and no row
  * source / utm      -> stored on the row when provided
  * GET /api/airdrop/stats -> 401 anonymous, 200 + exact count for admin
                         (handler re-check AND the production app's
                         before_request guard, mirroring waitlist stats)
  * template          -> no localStorage fallback remains; submitted is
                         set only on response.ok
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path

import pytest
from flask import Flask

# Production import order matters: mvp_app must be imported before the
# billing blueprint module (same note as test_kya_trust_score.py) —
# billing binds mvp_app's namespace at import, and mvp_app registers
# the blueprint on the production app during its own import.
import sincor2.mvp_app  # noqa: F401  (imported for binding side effects)
from sincor2.mvp_blueprints import billing

REGISTER = "/api/airdrop/register"
STATS = "/api/airdrop/stats"

# Mixed-case valid EVM address (0x + 40 hex chars).
WALLET_MIXED = "0x" + "aB" * 20
WALLET_LOWER = WALLET_MIXED.lower()
WALLET_2 = "0x" + "cd" * 20


def _db_path() -> Path:
    from sincor2.data_paths import data_dir

    return data_dir() / "airdrop_registrations.db"


def _rows():
    path = _db_path()
    if not path.exists():
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM airdrop_registrations ORDER BY wallet")]
        except sqlite3.OperationalError:
            return []


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data"))
    app = Flask(__name__)
    app.secret_key = "airdrop-test-secret"
    app.register_blueprint(billing.bp)
    app.config.update(TESTING=True)
    return app.test_client()


# ---------------------------------------------------------------------------
# Registration persists
# ---------------------------------------------------------------------------


def test_register_valid_wallet_persists_row(client):
    response = client.post(REGISTER, json={"wallet": WALLET_MIXED})
    assert response.status_code == 201
    assert response.get_json()["status"] == "success"

    rows = _rows()
    assert len(rows) == 1
    row = rows[0]
    # Stored lowercased even though the buyer submitted mixed case.
    assert row["wallet"] == WALLET_LOWER
    assert row["created_at"]
    # The IP is stored only as a SHA-256 hash — never the raw address.
    assert row["ip_hash"]
    assert row["ip_hash"] != "127.0.0.1"
    assert row["ip_hash"] == hashlib.sha256(b"127.0.0.1").hexdigest()


def test_register_duplicate_wallet_idempotent(client):
    first = client.post(REGISTER, json={"wallet": WALLET_MIXED})
    assert first.status_code == 201

    # Same wallet, different case spelling: still the same registrant.
    second = client.post(REGISTER, json={"wallet": WALLET_LOWER})
    assert second.status_code == 200
    assert second.get_json()["status"] == "already_registered"

    assert len(_rows()) == 1


def test_register_invalid_wallet_400_no_row(client):
    for bad in ("", "not-a-wallet", "0x123", "0x" + "zz" * 20, None):
        response = client.post(REGISTER, json={"wallet": bad})
        assert response.status_code == 400, bad
    assert _rows() == []


def test_register_stores_source_and_utm(client):
    response = client.post(REGISTER, json={
        "wallet": WALLET_2,
        "source": "landing",
        "utm_source": "twitter",
        "utm_medium": "social",
        "utm_campaign": "genesis",
    })
    assert response.status_code == 201
    rows = _rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "landing"
    assert row["utm_source"] == "twitter"
    assert row["utm_medium"] == "social"
    assert row["utm_campaign"] == "genesis"


# ---------------------------------------------------------------------------
# Stats: admin-only count
# ---------------------------------------------------------------------------


def _assert_no_count(response):
    body = response.get_data(as_text=True)
    assert "total_registrations" not in body


def test_stats_anonymous_refused_no_count(client):
    client.post(REGISTER, json={"wallet": WALLET_MIXED})
    response = client.get(STATS)
    assert response.status_code == 401
    _assert_no_count(response)


def test_stats_admin_key_exact_count(client):
    client.post(REGISTER, json={"wallet": WALLET_MIXED})
    client.post(REGISTER, json={"wallet": WALLET_2})
    client.post(REGISTER, json={"wallet": WALLET_LOWER})  # duplicate
    response = client.get(
        STATS, headers={"X-Admin-Key": os.environ["ADMIN_PASSWORD"]})
    assert response.status_code == 200
    assert response.get_json()["total_registrations"] == 2


def test_stats_wrong_admin_key_refused(client):
    response = client.get(STATS, headers={"X-Admin-Key": "wrong-key"})
    assert response.status_code == 401
    _assert_no_count(response)


# ---------------------------------------------------------------------------
# Production app (sincor2.mvp_app): route registered + guard covers it
# ---------------------------------------------------------------------------


@pytest.fixture
def mvp_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data"))
    os.environ.setdefault("FLASK_ENV", "test")
    os.environ.setdefault("ENVIRONMENT", "test")
    from sincor2.mvp_app import app as mvp_app

    mvp_app.config["TESTING"] = True
    return mvp_app.test_client()


def test_production_app_registers_airdrop_stats_route():
    from sincor2.mvp_app import app as mvp_app

    rules = {
        (rule.rule, method)
        for rule in mvp_app.url_map.iter_rules()
        for method in (rule.methods or ())
    }
    assert ("/api/airdrop/stats", "GET") in rules
    assert ("/api/airdrop/register", "POST") in rules


def test_production_stats_anonymous_refused(mvp_client):
    response = mvp_client.get(STATS)
    assert response.status_code == 401
    _assert_no_count(response)


def test_production_register_then_admin_stats(mvp_client):
    created = mvp_client.post(REGISTER, json={"wallet": WALLET_MIXED})
    assert created.status_code == 201
    response = mvp_client.get(
        STATS, headers={"X-Admin-Key": os.environ["ADMIN_PASSWORD"]})
    assert response.status_code == 200
    assert response.get_json()["total_registrations"] == 1


# ---------------------------------------------------------------------------
# Template: no localStorage fallback, success only on response.ok
# ---------------------------------------------------------------------------


def _template_text() -> str:
    path = (Path(__file__).resolve().parents[2]
            / "templates" / "sin-airdrop.html")
    return path.read_text(encoding="utf-8")


def test_template_has_no_localstorage_fallback():
    text = _template_text()
    # The removed fallback: submissions were mirrored into localStorage
    # on BOTH success and API failure, and submitted was set regardless.
    assert "localStorage" not in text
    assert "sin_airdrop_submissions" not in text
    assert "Still save locally" not in text
    assert "Also store locally" not in text


def test_template_sets_submitted_only_on_ok():
    text = _template_text()
    # The success flag is gated on the API response being ok.
    assert "response.ok" in text
    assert "registrationError" in text
