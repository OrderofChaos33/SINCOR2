"""P2-A2: treasury metric endpoints require auth and return allowlisted DTOs.

- unauthenticated -> 401/403
- authenticated -> 200 with ONLY allowlisted fields (no treasury address,
  no RPC diagnostics, no per-source breakdowns, no tx hashes / notes)
- backend failure -> generic error detail (no exception text leak)
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def canned_treasury(monkeypatch):
    from sincor2 import treasury_inflow
    from sincor2.treasury_inflow import TreasurySnapshot

    snap = TreasurySnapshot(
        timestamp="2026-10-09T00:00:00+00:00",
        treasury_address="0xTESTINTERNAL",
        eth_balance=1.5,
        usdc_balance=250.0,
        sinc_balance=10.0,
        axm_balance=5.0,
        rpc_ok=True,
        rpc_detail="internal dial tcp 10.0.0.9:8545 refused",
        ledger_24h={"USD": 100.0},
        ledger_24h_usd=100.0,
        ledger_events_24h=3,
        ledger_total_events=4242,
    )
    summary = {
        "events_24h": 3,
        "by_asset": {"USD": 100.0},
        "by_source": {"internal_scheduler": 100.0},
        "usd_total_24h": 100.0,
        "usd_realized_24h": 60.0,
        "usd_projected_24h": 40.0,
    }
    monkeypatch.setattr(
        treasury_inflow, "get_treasury_snapshot", lambda include_onchain=True: snap
    )
    monkeypatch.setattr(treasury_inflow, "ledger_summary_24h", lambda: dict(summary))
    return snap, summary


def _assert_allowlisted(body):
    from sincor2.treasury_inflow import (
        PUBLIC_TREASURY_LEDGER_FIELDS,
        PUBLIC_TREASURY_SNAPSHOT_FIELDS,
    )

    assert body["status"] == "ok"
    assert set(body["snapshot"].keys()) == set(PUBLIC_TREASURY_SNAPSHOT_FIELDS)
    assert set(body["ledger_24h"].keys()) == set(PUBLIC_TREASURY_LEDGER_FIELDS)
    # raw ledger internals must never appear
    flat = str(body)
    assert "0xTESTINTERNAL" not in flat
    assert "internal dial tcp" not in flat
    assert "internal_scheduler" not in flat
    assert "by_source" not in flat
    assert "tx_hash" not in flat
    # allowlisted values survive
    assert body["snapshot"]["eth_balance"] == 1.5
    assert body["ledger_24h"]["usd_realized_24h"] == 60.0
    assert body["ledger_24h"]["usd_projected_24h"] == 40.0


def _bearer(app, identity: str = "treasury-auditor") -> dict:
    # Mint the JWT directly: avoids consuming the shared /api/auth/login
    # rate-limit budget (127.0.0.1 is shared by the whole pytest process).
    with app.app_context():
        from flask_jwt_extended import create_access_token

        return {
            "Authorization": "Bearer "
            + create_access_token(identity=identity, additional_claims={"role": "admin"})
        }


def test_legacy_treasury_unauthenticated_rejected(client, canned_treasury):
    resp = client.get("/api/metrics/treasury")
    assert resp.status_code in (401, 403)


def test_legacy_treasury_authenticated_allowlist_only(client, app, canned_treasury):
    resp = client.get("/api/metrics/treasury", headers=_bearer(app))
    assert resp.status_code == 200
    _assert_allowlisted(resp.get_json())


def test_legacy_treasury_backend_failure_generic(client, app, monkeypatch):
    from sincor2 import treasury_inflow

    def _boom(include_onchain=True):
        raise RuntimeError("super secret stack trace /etc/passwd")

    monkeypatch.setattr(treasury_inflow, "get_treasury_snapshot", _boom)
    resp = client.get("/api/metrics/treasury", headers=_bearer(app))
    assert resp.status_code == 500
    assert "super secret" not in resp.get_data(as_text=True)
    assert "/etc/passwd" not in resp.get_data(as_text=True)


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


def test_mvp_treasury_unauthenticated_rejected(mvp_client, canned_treasury):
    resp = mvp_client.get("/api/metrics/treasury")
    assert resp.status_code in (401, 403)


def test_mvp_treasury_authenticated_allowlist_only(mvp_client, canned_treasury):
    from sincor2.mvp_app import app as mvp_app

    resp = mvp_client.get("/api/metrics/treasury", headers=_bearer(mvp_app))
    assert resp.status_code == 200
    _assert_allowlisted(resp.get_json())
