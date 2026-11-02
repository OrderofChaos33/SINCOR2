"""Observability builds #1 + #5 (site audit 2026-10-28, §4/§5.1).

#1 — the homepage stats strip must work for anonymous visitors:
- ``GET /api/metrics/treasury/public`` is unauthenticated and ledger-only:
  24h inflow aggregates + the bounty-pool figures that ``/v1/a2a/pool``
  already publishes. No on-chain snapshot call, no balances, no treasury
  address, no per-source breakdown, no allocation rows.
- The authenticated ``/api/metrics/treasury`` stays JWT-gated (covered by
  test_auditp2_a2_treasury_auth.py; one guard re-asserted here).
- home.html fetches the public variant, reads the reference price when
  ``spot_usd`` is null, and never renders a bare "—" / "unavailable".

#5 — build stamping: ``RAILWAY_GIT_COMMIT_SHA`` (fallback
``SINCOR_BUILD_SHA``, then "unknown") + build time are exposed in the MVP
/health payload, the health aliases, railway_start's /boot status, and
the shared footer when a SHA is known. Absent env degrades to "unknown"
and never crashes.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

FAKE_SHA = "deadbeefcafe1234567890abcdef1234567890ab"
REPO_ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------
# Isolation: the legacy app factory (conftest ``client``) runs
# ``platform_bootstrap.bootstrap_platform()``, whose
# ``_extend_a2a_skills_from_registry()`` APPENDS the ~20 vertical/SKU
# registry skills to the process-global ``sincor2.a2a_integration``
# ``SINCOR_SKILLS`` list — append-only, never reverted. The production
# mvp app never bootstraps, so its agent card serves the canonical 17;
# but ``build_agent_card()`` reads that same global list, so once any
# test in this module builds the legacy app, every later card read in
# the process (e.g. test_machine_surfaces.py) sees 37 skills. Imports
# alone do not pollute (verified: imports leave 17; a legacy
# ``create_app()`` call makes it 37). Snapshot/restore around every test
# in this module so the global catalogue is left exactly as found.
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _restore_a2a_skill_catalogue():
    from sincor2 import a2a_integration

    snapshot = list(a2a_integration.SINCOR_SKILLS)
    yield
    if list(a2a_integration.SINCOR_SKILLS) != snapshot:
        # In-place restore: build_agent_card() embeds the list object
        # itself, so preserve its identity — do not rebind the name.
        a2a_integration.SINCOR_SKILLS[:] = snapshot
        # The bootstrap also recompiled the derived schema cache from
        # the extended list; rebuild it from the restored catalogue.
        a2a_integration.refresh_skill_schemas()


# --------------------------------------------------------------------------
# build_info unit behavior
# --------------------------------------------------------------------------

def test_build_info_prefers_railway_sha(monkeypatch):
    from sincor2.build_info import build_info

    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", FAKE_SHA)
    monkeypatch.setenv("SINCOR_BUILD_SHA", "f" * 40)
    info = build_info()
    assert info["sha"] == FAKE_SHA
    assert info["short"] == FAKE_SHA[:12]
    assert info["time"]


def test_build_info_falls_back_to_sincor_env(monkeypatch):
    from sincor2.build_info import build_info

    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    monkeypatch.setenv("SINCOR_BUILD_SHA", "a" * 40)
    assert build_info()["sha"] == "a" * 40


def test_build_info_unknown_without_env_never_crashes(monkeypatch):
    from sincor2.build_info import build_info

    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    monkeypatch.delenv("SINCOR_BUILD_SHA", raising=False)
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "   ")  # whitespace = unset
    info = build_info()
    assert info["sha"] == "unknown"
    assert info["short"] == "unknown"
    assert info["time"]  # process-start fallback is always present


def test_build_info_honors_explicit_build_time(monkeypatch):
    from sincor2.build_info import build_info

    monkeypatch.setenv("SINCOR_BUILD_TIME", "2026-10-28T22:00:00Z")
    assert build_info()["time"] == "2026-10-28T22:00:00Z"


# --------------------------------------------------------------------------
# #1 — public treasury DTO (ledger-only)
# --------------------------------------------------------------------------

@pytest.fixture
def canned_public(monkeypatch):
    """Ledger summary with internal-only fields + a snapshot that must
    never be consulted by the public route."""
    from sincor2 import a2a_bounty_pool, treasury_inflow

    summary = {
        "events_24h": 3,
        "by_asset": {"USD": 100.0},
        "by_source": {"internal_scheduler": 100.0},
        "usd_total_24h": 100.0,
        "usd_realized_24h": 60.0,
        "usd_projected_24h": 40.0,
    }
    monkeypatch.setattr(treasury_inflow, "ledger_summary_24h", lambda: dict(summary))

    def _snapshot_must_not_be_called(include_onchain=True):
        raise AssertionError("public route must not build an on-chain snapshot")

    monkeypatch.setattr(
        treasury_inflow, "get_treasury_snapshot", _snapshot_must_not_be_called
    )

    class _Pool:
        def status(self):
            return {
                "reserve_axm": 0.0,
                "funded_axm": 20000.0,
                "spent_axm": 277.75,
                "allocated_axm": 26.5,
                "available_axm": 19695.75,
                "allocation_count": 12,
                "allocations": [{"task_id": "secret-task-id"}],
                "ledger": "offchain-axm",
            }

    monkeypatch.setattr(a2a_bounty_pool, "bounty_pool", lambda *a, **k: _Pool())
    return summary


def _assert_public_body(body):
    from sincor2.treasury_inflow import (
        PUBLIC_POOL_FIELDS,
        PUBLIC_TREASURY_LEDGER_FIELDS,
    )

    assert body["status"] == "ok"
    assert body["scope"] == "ledger_only"
    assert set(body["ledger_24h"].keys()) == set(PUBLIC_TREASURY_LEDGER_FIELDS)
    assert set(body["pool"].keys()) == set(PUBLIC_POOL_FIELDS)
    assert body["ledger_24h"]["usd_total_24h"] == 100.0
    assert body["pool"]["funded_axm"] == 20000.0
    assert body["pool"]["available_axm"] == 19695.75
    flat = str(body)
    # nothing beyond the already-public pool figures + ledger aggregates
    assert "internal_scheduler" not in flat
    assert "by_source" not in flat
    assert "by_asset" not in flat
    assert "secret-task-id" not in flat
    assert "allocations" not in flat
    assert "treasury_address" not in flat
    assert "eth_balance" not in flat
    assert "usdc_balance" not in flat
    assert "rpc" not in flat


def test_public_treasury_anonymous_ok_legacy_app(client, canned_public):
    resp = client.get("/api/metrics/treasury/public")
    assert resp.status_code == 200
    _assert_public_body(resp.get_json())


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


def test_public_treasury_anonymous_ok_mvp_app(mvp_client, canned_public):
    resp = mvp_client.get("/api/metrics/treasury/public")
    assert resp.status_code == 200
    _assert_public_body(resp.get_json())


def test_public_treasury_backend_failure_generic(client, monkeypatch):
    from sincor2 import treasury_inflow

    def _boom():
        raise RuntimeError("super secret ledger path /etc/passwd")

    monkeypatch.setattr(treasury_inflow, "ledger_summary_24h", _boom)
    resp = client.get("/api/metrics/treasury/public")
    assert resp.status_code == 500
    assert "super secret" not in resp.get_data(as_text=True)
    assert "/etc/passwd" not in resp.get_data(as_text=True)


def test_authenticated_treasury_still_gated_on_mvp(mvp_client, canned_public):
    resp = mvp_client.get("/api/metrics/treasury")
    assert resp.status_code in (401, 403)


# --------------------------------------------------------------------------
# #1 — homepage strip wiring (template source)
# --------------------------------------------------------------------------

def test_home_fetches_public_treasury_not_gated_route():
    src = (REPO_ROOT / "templates" / "home.html").read_text(encoding="utf-8")
    assert "fetchJson('/api/metrics/treasury/public')" in src
    assert "fetchJson('/api/metrics/treasury')" not in src


def test_home_reads_reference_price_and_never_renders_unavailable():
    src = (REPO_ROOT / "templates" / "home.html").read_text(encoding="utf-8")
    assert "reference_price_usd" in src
    assert "public snapshot unavailable" not in src
    assert "'unavailable'" not in src
    # static honest defaults are server-rendered, so the strip never
    # shows a bare dash even before (or without) the JS fetch completing.
    # The SINC value itself always comes from the pricing payload — the
    # claim scanner bans a hardcoded price figure in template copy.
    assert 'id="live-spot-val">Reference<' in src
    assert 'id="live-treasury-val">20,000 AXM<' in src


# --------------------------------------------------------------------------
# #5 — health / boot build stamps
# --------------------------------------------------------------------------

def test_mvp_health_carries_build_sha(mvp_client, monkeypatch):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", FAKE_SHA)
    resp = mvp_client.get("/health")
    assert resp.status_code == 200
    build = resp.get_json()["build"]
    assert build["sha"] == FAKE_SHA
    assert build["short"] == FAKE_SHA[:12]
    assert build["time"]


def test_mvp_health_alias_carries_build_sha(mvp_client, monkeypatch):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", FAKE_SHA)
    resp = mvp_client.get("/api/health")
    assert resp.status_code == 200
    assert resp.get_json()["build"]["sha"] == FAKE_SHA


def test_mvp_health_degrades_to_unknown(mvp_client, monkeypatch):
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    monkeypatch.delenv("SINCOR_BUILD_SHA", raising=False)
    resp = mvp_client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["build"]["sha"] == "unknown"


def test_railway_boot_status_carries_build(monkeypatch):
    import railway_start

    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", FAKE_SHA)
    status = railway_start.boot_status()
    assert status["build"]["sha"] == FAKE_SHA
    assert status["build"]["short"] == FAKE_SHA[:12]
    assert status["build"]["time"]


def test_railway_boot_status_unknown_without_env(monkeypatch):
    import railway_start

    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    monkeypatch.delenv("SINCOR_BUILD_SHA", raising=False)
    status = railway_start.boot_status()
    assert status["build"]["sha"] == "unknown"


def test_footer_renders_short_sha_when_known(mvp_client, monkeypatch):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", FAKE_SHA)
    resp = mvp_client.get("/?inner=1")
    assert resp.status_code == 200
    assert ("Build " + FAKE_SHA[:12]) in resp.get_data(as_text=True)


def test_footer_omits_build_when_unknown(mvp_client, monkeypatch):
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    monkeypatch.delenv("SINCOR_BUILD_SHA", raising=False)
    resp = mvp_client.get("/?inner=1")
    assert resp.status_code == 200
    assert "Build unknown" not in resp.get_data(as_text=True)
