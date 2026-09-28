"""Dashboard overhaul smoke tests.

The unified console (base.html shell + /dashboard, /agents, /tasks, /pool)
must render 200 with its live-data markers on BOTH Flask apps (sincor2.app
and sincor2.mvp_app -- the latter is what production actually serves);
the retired /dashboards menu must 302 to /command-center; legacy
dashboards must stay reachable.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def isolated_pool(tmp_path, monkeypatch):
    """Point the bounty pool at a throwaway ledger so the smoke test never
    touches the repo's data dir."""
    from sincor2.a2a_bounty_pool import reset_bounty_pool

    pool_path = tmp_path / "pool.json"
    monkeypatch.setenv("SINCOR_BOUNTY_POOL_PATH", str(pool_path))
    monkeypatch.setenv("SINCOR_LAUNCH_BOUNTY_AXM", "100")
    reset_bounty_pool(path=str(pool_path))
    yield
    reset_bounty_pool(path=str(pool_path))


@pytest.fixture
def mvp_client():
    import os

    os.environ.setdefault("FLASK_ENV", "test")
    os.environ.setdefault("ENVIRONMENT", "test")
    from sincor2.mvp_app import app as mvp_app

    mvp_app.config["TESTING"] = True
    return mvp_app.test_client()


# ---------------------------------------------------------------------------
# Main app (sincor2.app)
# ---------------------------------------------------------------------------

def test_overview_renders_with_console_nav(client, isolated_pool):
    r = client.get("/dashboard")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Operations overview" in html
    # unified sidebar nav present
    for href in ("/dashboard", "/command-center", "/agents", "/tasks", "/pool", "/docs"):
        assert f'href="{href}"' in html, href
    # live data sources wired, no "coming soon" placeholders
    assert "/v1/a2a/pool" in html
    assert "Live telemetry coming soon" not in html


def test_agents_page_renders(client):
    r = client.get("/agents")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Agent fleet" in html
    assert "/api/a2a/agents" in html
    assert 'data-nav="agents"' in html


def test_tasks_page_renders(client):
    r = client.get("/tasks")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Task board" in html
    assert "/v1/a2a/tasks" in html


def test_pool_page_renders(client):
    r = client.get("/pool")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Bounty pool" in html
    assert "/v1/a2a/pool" in html


def test_dashboards_menu_redirects_to_command_center(client):
    r = client.get("/dashboards", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/command-center")


def test_command_center_still_renders(client):
    r = client.get("/command-center")
    assert r.status_code == 200


def test_legacy_dashboards_still_reachable(client):
    # NOTE: /operator lives on the unregistered pages blueprint on this app
    # (pre-existing 404); it is reachable on mvp_app instead.
    for path in (
        "/executive-dashboard",
        "/professional-dashboard",
        "/discovery-dashboard",
        "/enterprise-dashboard",
    ):
        r = client.get(path)
        assert r.status_code == 200, path


def test_live_data_endpoints_behind_pages(client, isolated_pool):
    """The JSON feeds the new pages depend on must answer 200 in test env."""
    for path in ("/api/a2a/agents", "/v1/a2a/pool", "/v1/a2a/tasks?per_page=5"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.get_json() is not None, path


# ---------------------------------------------------------------------------
# Production app (sincor2.mvp_app) — this is what gunicorn actually serves
# ---------------------------------------------------------------------------

def test_mvp_console_pages_render(mvp_client, isolated_pool):
    markers = {
        "/agents": "Agent fleet",      # console page wins over wardrobe's orphan index
        "/tasks": "Task board",
        "/pool": "Bounty pool",
    }
    for path, marker in markers.items():
        r = mvp_client.get(path)
        assert r.status_code == 200, path
        assert marker in r.get_data(as_text=True), path


def test_mvp_command_center_gated(mvp_client):
    # Operator surface: anonymous users bounce to login (mvp_app convention),
    # admins get the page.
    r = mvp_client.get("/command-center", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].startswith("/login")
    with mvp_client.session_transaction() as sess:
        sess["is_admin"] = True
        sess["admin_username"] = "admin"
    r = mvp_client.get("/command-center")
    assert r.status_code == 200
    assert "Command Center" in r.get_data(as_text=True)


def test_mvp_dashboards_redirects(mvp_client):
    r = mvp_client.get("/dashboards", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/command-center")


def test_mvp_legacy_pages_still_reachable(mvp_client):
    # Gated pages 302 to /login for anonymous users (their designed auth
    # behavior); discovery/enterprise have no route on mvp_app (main app only).
    for path in (
        "/operator",
        "/executive-dashboard",
        "/professional-dashboard",
        "/admin-dashboard",
        "/consciousness-dashboard",
    ):
        r = mvp_client.get(path, follow_redirects=False)
        assert r.status_code in (200, 302), path
        if r.status_code == 302:
            assert r.headers["Location"].startswith("/login"), path


def test_mvp_live_data_endpoints(mvp_client, isolated_pool):
    for path in ("/api/a2a/agents", "/v1/a2a/pool", "/v1/a2a/tasks?per_page=5"):
        r = mvp_client.get(path)
        assert r.status_code == 200, path
        assert r.get_json() is not None, path
