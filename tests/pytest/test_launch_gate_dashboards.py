"""Launch gate (2026-11-09 recalibration) + restored dashboard routes on mvp_app."""
from __future__ import annotations

import os
import re

import pytest


@pytest.fixture
def mvp_client():
    os.environ.setdefault("FLASK_ENV", "test")
    os.environ.setdefault("ENVIRONMENT", "test")
    os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key-32-char-minimum-ok")
    os.environ.setdefault("ADMIN_USERNAME", "admin")
    os.environ.setdefault("ADMIN_PASSWORD", "admin-password-32-char-minimum-ok")
    from sincor2.mvp_app import app

    app.config["TESTING"] = True
    app.config["SERVER_NAME"] = None
    return app.test_client()


def _as_admin(client):
    with client.session_transaction() as sess:
        sess["is_admin"] = True
        sess["admin_username"] = "admin"


DASHBOARDS = [
    "/admin-dashboard",
    "/consciousness-dashboard",
    "/executive-dashboard",
    "/professional-dashboard",
]

GATE_MARKER = "T-minus genesis"
HOME_MARKER = "TOA-native agent infrastructure"


# --------------------------------------------------------------------------
# Dashboard routes
# --------------------------------------------------------------------------

def test_dashboard_routes_admin_200(mvp_client):
    _as_admin(mvp_client)
    markers = {
        "/admin-dashboard": "SINCOR Admin",
        "/consciousness-dashboard": "Consciousness Transfer Interface",
        "/executive-dashboard": "Executive Command Center",
        "/professional-dashboard": "Business Intelligence Dashboard",
    }
    for path in DASHBOARDS:
        r = mvp_client.get(path, follow_redirects=False)
        assert r.status_code == 200, path
        assert markers[path] in r.get_data(as_text=True), path


def test_dashboard_routes_anonymous_redirect_to_login(mvp_client):
    for path in DASHBOARDS:
        r = mvp_client.get(path, follow_redirects=False)
        assert r.status_code in (301, 302), path
        loc = r.headers.get("Location", "")
        assert "/login" in loc and f"next={path}" in loc, path


def test_dashboards_menu_all_hrefs_resolve(mvp_client):
    _as_admin(mvp_client)
    r = mvp_client.get("/dashboards", follow_redirects=False)
    assert r.status_code == 200
    hrefs = set(re.findall(r'href="(/[^"]*)"', r.get_data(as_text=True)))
    assert hrefs, "expected links in dashboards_menu.html"
    for href in sorted(hrefs):
        rr = mvp_client.get(href, follow_redirects=False)
        assert rr.status_code != 404, href


def test_executive_apis_admin_shape(mvp_client):
    _as_admin(mvp_client)
    r = mvp_client.get("/api/executive-metrics")
    assert r.status_code == 200
    data = r.get_json()
    for key in ("leads", "system", "agents", "database", "performance"):
        assert key in data, key
    r = mvp_client.get("/api/recent-activity")
    assert r.status_code == 200
    assert r.get_json() == []


def test_executive_apis_anonymous_401(mvp_client):
    assert mvp_client.get("/api/executive-metrics").status_code == 401
    assert mvp_client.get("/api/recent-activity").status_code == 401


def test_professional_stubs_honest_failure(mvp_client):
    _as_admin(mvp_client)
    for path in (
        "/generate-leads", "/create-campaign", "/analyze-opportunities",
        "/connect-calendar", "/connect-payments", "/connect-email",
        "/connect-sms", "/test-email",
    ):
        r = mvp_client.post(path, json={})
        assert r.status_code == 501, path
        data = r.get_json()
        assert data["success"] is False and data["error"], path


def test_professional_stubs_anonymous_401(mvp_client):
    assert mvp_client.post("/generate-leads", json={}).status_code == 401


# --------------------------------------------------------------------------
# Launch gate
# --------------------------------------------------------------------------

def test_gate_anonymous_sees_gate_before_launch(mvp_client, monkeypatch):
    monkeypatch.delenv("SINCOR_LAUNCH_DATE", raising=False)
    r = mvp_client.get("/", follow_redirects=False)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert GATE_MARKER in html
    assert "November 9, 2026" in html
    assert "2026-11-09T00:00:00.000Z" in html  # countdown target
    assert HOME_MARKER not in html


def test_gate_admin_bypass(mvp_client, monkeypatch):
    monkeypatch.delenv("SINCOR_LAUNCH_DATE", raising=False)
    _as_admin(mvp_client)
    r = mvp_client.get("/", follow_redirects=False)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert GATE_MARKER not in html
    assert HOME_MARKER in html


def test_gate_past_launch_date_env(mvp_client, monkeypatch):
    monkeypatch.setenv("SINCOR_LAUNCH_DATE", "2026-01-01")
    r = mvp_client.get("/", follow_redirects=False)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert GATE_MARKER not in html
    assert HOME_MARKER in html


def test_gate_bad_env_value_never_500s(mvp_client, monkeypatch):
    monkeypatch.setenv("SINCOR_LAUNCH_DATE", "not-a-date")
    r = mvp_client.get("/", follow_redirects=False)
    assert r.status_code == 200
    assert "November 9, 2026" in r.get_data(as_text=True)  # falls back to default


def test_gate_inner_backdrop_bypass(mvp_client, monkeypatch):
    # The gate embeds /?inner=1 as its blurred backdrop; it must serve the
    # real homepage or the gate would recurse infinitely.
    monkeypatch.delenv("SINCOR_LAUNCH_DATE", raising=False)
    r = mvp_client.get("/?inner=1", follow_redirects=False)
    assert r.status_code == 200
    assert GATE_MARKER not in r.get_data(as_text=True)


def test_genesis_claim_flow_lifts_gate(mvp_client, monkeypatch, tmp_path):
    monkeypatch.delenv("SINCOR_LAUNCH_DATE", raising=False)
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    r = mvp_client.post(
        "/api/genesis/claim",
        json={"email": "gate-test@example.com", "password": "testpass123", "wallet": ""},
    )
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
    assert "sincor_genesis" in (r.headers.get("Set-Cookie") or "")
    # Cookie now lifts the gate for this browser.
    r = mvp_client.get("/", follow_redirects=False)
    assert r.status_code == 200
    assert GATE_MARKER not in r.get_data(as_text=True)


def test_genesis_claim_rejects_bad_email(mvp_client, monkeypatch, tmp_path):
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    r = mvp_client.post(
        "/api/genesis/claim",
        json={"email": "not-an-email", "password": "testpass123"},
    )
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_gate_does_not_gate_other_surfaces(mvp_client, monkeypatch):
    # Spot-check: APIs and auth stay exactly as they are pre-launch.
    monkeypatch.delenv("SINCOR_LAUNCH_DATE", raising=False)
    r = mvp_client.get("/api/a2a/agents")
    assert r.status_code == 200
    r = mvp_client.get("/login", follow_redirects=False)
    assert r.status_code == 200
