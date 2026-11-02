"""WREN PR 6: B2B pack promises vs delivery + admin intake visibility.

The B2B pack fulfillment on main is a kickoff receipt only:
``fulfill_pack_agent`` (rail_routes.py) instantiates the pack's vertical
agent and records ``agent_kickoff`` — the buyer's real work starts when
they complete the intake form, the owner reviews it, and a human
produces the deliverable. Only WebBuilder builds an artifact
automatically. The pricing / buy / pay pages must say exactly that
sequence ("What happens after you pay") and must not promise artifacts,
setups, samples, or timelines the code does not produce.

The admin half: pack intakes that reached ``intake_complete`` are
waiting on a human. GET /api/admin/intakes (admin-gated, mirroring
/api/waitlist/stats) lists the ones at least 24h old so none of them
sits unnoticed.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from flask import Flask

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = REPO_ROOT / "templates"

INTAKES = "/api/admin/intakes"

BLOCK_HEADING = "What happens after you pay"
BLOCK_PHRASES = [
    BLOCK_HEADING,
    "complete the intake form",
    "owner reviews your intake",
    "human-reviewed deliverable",
]

# Copy that outran the code and had to go (see the PR 6 report). These
# strings must not come back on the rendered pages.
OFFENDING_PHRASES = [
    "SBOM-ready artifacts",
    "deliverable samples",
    "Delivery artifacts for pilot sign-off",
    "SBOM skill activation",
    "Outbound sequence templates included",
    "dedicated setup",
    "workflow kickoff",
    "adapter setup",
    "automatically delivered",
]

B2B_RESOURCES = [
    "rail-pack-credentialing_launch",
    "rail-pack-chroma_compliance",
    "rail-pack-sadas_execution",
]
NON_B2B_RESOURCES = [
    "rail-pack-webbuilder_site",       # WebBuilder DOES build automatically
    "rail-pack-digital_bi_template_pack",  # token-gated download, no intake
    "rail-sub-starter",                # subscription, no intake
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pages_client():
    from sincor2.blueprints.pages import pages_bp

    app = Flask(__name__, template_folder=str(TEMPLATES))
    app.register_blueprint(pages_bp)
    return app.test_client()


@pytest.fixture(scope="module")
def pay_client():
    from sincor2.blueprints.rail_pay import rail_pay_bp

    app = Flask(__name__, template_folder=str(TEMPLATES))
    app.register_blueprint(rail_pay_bp)
    return app.test_client()


def _seed_intakes(db_path, rows):
    """Insert pack_intakes rows at controlled ages/statuses.

    rows: (intake_id, customer_email, pack_sku, status, age_hours)
    """
    from sincor2.payment_ledger import _PACK_INTAKE_SCHEMA

    now = datetime.now(timezone.utc)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(_PACK_INTAKE_SCHEMA)
        for intake_id, email, sku, status, age_hours in rows:
            created = (now - timedelta(hours=age_hours)).isoformat()
            conn.execute(
                "INSERT INTO pack_intakes (intake_id, customer_email,"
                " pack_sku, event_id, status, intake_url, created_at,"
                " details) VALUES (?,?,?,?,?,?,?,?)",
                (intake_id, email, sku, f"evt-{intake_id}", status, "",
                 created, "{}"),
            )
        conn.commit()
    finally:
        conn.close()


# (id, email, sku, status, age_hours) — only the two intake_complete
# rows aged >= 24h may be listed.
SEED_ROWS = [
    ("intake-stale-72h", "stale72@example.com", "credentialing_launch",
     "intake_complete", 72),
    ("intake-stale-25h", "stale25@example.com", "chroma_compliance",
     "intake_complete", 25),
    ("intake-fresh-1h", "fresh@example.com", "sadas_execution",
     "intake_complete", 1),
    ("intake-kickoff-72h", "kickoff@example.com", "credentialing_launch",
     "agent_kickoff", 72),
    ("intake-progress-72h", "progress@example.com", "chroma_compliance",
     "in_progress", 72),
    ("intake-delivered-72h", "done@example.com", "sadas_execution",
     "delivered", 72),
    ("intake-created-72h", "new@example.com", "credentialing_launch",
     "created", 72),
]
EXPECTED_IDS = {"intake-stale-72h", "intake-stale-25h"}


@pytest.fixture()
def admin_env(tmp_path, monkeypatch):
    """Minimal app mounting ONLY the admin-intakes blueprint."""
    db_path = tmp_path / "revenue.db"
    monkeypatch.setenv("SINCOR_REVENUE_DB", str(db_path))
    _seed_intakes(db_path, SEED_ROWS)
    from sincor2.blueprints.admin_intakes import admin_intakes_bp

    app = Flask(__name__)
    app.secret_key = "test-secret-key-with-32-char-minimum-ok"
    app.config["TESTING"] = True
    app.register_blueprint(admin_intakes_bp)
    return app.test_client(), db_path


def _admin_session(client):
    with client.session_transaction() as sess:
        sess["is_admin"] = True
        sess["admin_username"] = os.environ.get("ADMIN_USERNAME", "admin")


# ---------------------------------------------------------------------------
# Templates: the true sequence is stated, the overreach is gone
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/pricing", "/buy"])
def test_pages_state_what_happens_after_you_pay(pages_client, path):
    body = pages_client.get(path).get_data(as_text=True)
    for phrase in BLOCK_PHRASES:
        assert phrase in body, f"{phrase!r} missing from {path}"


@pytest.mark.parametrize("resource_id", B2B_RESOURCES)
def test_pay_page_states_sequence_for_b2b_packs(pay_client, resource_id):
    body = pay_client.get(f"/rail/pay/{resource_id}").get_data(as_text=True)
    for phrase in BLOCK_PHRASES:
        assert phrase in body, f"{phrase!r} missing for {resource_id}"


@pytest.mark.parametrize("resource_id", NON_B2B_RESOURCES)
def test_pay_page_block_is_b2b_only(pay_client, resource_id):
    body = pay_client.get(f"/rail/pay/{resource_id}").get_data(as_text=True)
    assert BLOCK_HEADING not in body


@pytest.mark.parametrize("path", ["/pricing", "/buy"])
def test_pages_drop_overpromising_copy(pages_client, path):
    body = pages_client.get(path).get_data(as_text=True)
    lowered = body.lower()
    for phrase in OFFENDING_PHRASES:
        assert phrase.lower() not in lowered, (
            f"overpromising copy {phrase!r} still on {path}")


def test_pay_page_has_no_automatic_delivery_claim(pay_client):
    body = pay_client.get(
        "/rail/pay/rail-pack-chroma_compliance").get_data(as_text=True)
    assert "automatically delivered" not in body.lower()


# ---------------------------------------------------------------------------
# payment_ledger reader
# ---------------------------------------------------------------------------


def test_reader_lists_only_stale_completed_intakes(tmp_path):
    from sincor2.payment_ledger import list_stale_completed_pack_intakes

    db_path = tmp_path / "revenue.db"
    _seed_intakes(db_path, SEED_ROWS)
    stale = list_stale_completed_pack_intakes(db_path)
    assert {i.intake_id for i in stale} == EXPECTED_IDS
    assert all(i.status == "intake_complete" for i in stale)


def test_reader_min_age_is_configurable(tmp_path):
    from sincor2.payment_ledger import list_stale_completed_pack_intakes

    db_path = tmp_path / "revenue.db"
    _seed_intakes(db_path, SEED_ROWS)
    stale = list_stale_completed_pack_intakes(db_path, min_age_hours=100)
    assert stale == []


# ---------------------------------------------------------------------------
# GET /api/admin/intakes: the gate
# ---------------------------------------------------------------------------


def test_intakes_anonymous_refused(admin_env):
    client, _db = admin_env
    response = client.get(INTAKES)
    assert response.status_code == 401
    body = response.get_data(as_text=True)
    for _id, email, *_rest in SEED_ROWS:
        assert email not in body


def test_intakes_non_admin_session_refused(admin_env):
    client, _db = admin_env
    with client.session_transaction() as sess:
        sess["user_email"] = "customer@example.com"
        sess["is_admin"] = False
    response = client.get(INTAKES)
    assert response.status_code == 401


def test_intakes_forged_admin_session_refused(admin_env):
    client, _db = admin_env
    with client.session_transaction() as sess:
        sess["is_admin"] = True
        sess["admin_username"] = "not-the-admin"
    response = client.get(INTAKES)
    assert response.status_code == 401


def test_intakes_admin_session_lists_stale_only(admin_env):
    client, _db = admin_env
    _admin_session(client)
    response = client.get(INTAKES)
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["count"] == 2
    assert {row["intake_id"] for row in payload["intakes"]} == EXPECTED_IDS
    for row in payload["intakes"]:
        assert row["status"] == "intake_complete"
        assert row["age_hours"] >= 24
        assert row["customer_email"]  # follow-up needs the buyer email
        assert row["pack_sku"]
        assert row["created_at"]


def test_intakes_admin_key_lists_stale_only(admin_env):
    client, _db = admin_env
    response = client.get(
        INTAKES, headers={"X-Admin-Key": os.environ["ADMIN_PASSWORD"]})
    assert response.status_code == 200
    assert response.get_json()["count"] == 2


# ---------------------------------------------------------------------------
# Production app (sincor2.mvp_app): mounted + gated there too
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mvp_app_obj():
    os.environ.setdefault("FLASK_ENV", "test")
    os.environ.setdefault("ENVIRONMENT", "test")
    from sincor2.mvp_app import app as mvp_app

    mvp_app.config["TESTING"] = True
    return mvp_app


def test_production_app_registers_intakes_route(mvp_app_obj):
    rules = {
        (rule.rule, method)
        for rule in mvp_app_obj.url_map.iter_rules()
        for method in (rule.methods or ())
    }
    assert ("/api/admin/intakes", "GET") in rules


def test_production_intakes_anonymous_refused(mvp_app_obj):
    response = mvp_app_obj.test_client().get(INTAKES)
    assert response.status_code == 401


def test_production_intakes_admin_lists_stale_only(mvp_app_obj, tmp_path,
                                                    monkeypatch):
    db_path = tmp_path / "prod_revenue.db"
    _seed_intakes(db_path, SEED_ROWS)
    monkeypatch.setenv("SINCOR_REVENUE_DB", str(db_path))
    client = mvp_app_obj.test_client()
    _admin_session(client)
    response = client.get(INTAKES)
    assert response.status_code == 200
    payload = response.get_json()
    assert {row["intake_id"] for row in payload["intakes"]} == EXPECTED_IDS
