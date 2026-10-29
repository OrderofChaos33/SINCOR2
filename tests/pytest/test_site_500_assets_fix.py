"""Site defect fixes from the 2026-10-28 route audit (codebase-routes.md).

Pins:
- GET /sinc/recover-hook renders (was 500: hook_recover.html missing);
- GET /favicon.ico serves the hornet brand asset (was 404);
- executive_dashboard.html no longer references the missing
  /static/sincor_business_logo.png;
- admin_training_vault.html no longer links to the 9 nonexistent
  /videos/* and /onboarding/next-step URLs;
- docs.html no longer links to the nonexistent /billing route;
- operator_dashboard.html no longer fetches the unmounted
  /api/marketplace/settlement/records endpoint and degrades honestly;
- global guard: every internal href/src/fetch target in the rendered
  responses of the pages above resolves against the production route
  table (and /static/ targets exist on disk).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = REPO_ROOT / "templates"
STATIC = REPO_ROOT / "static"


@pytest.fixture(scope="module")
def mvp_client():
    os.environ.setdefault("FLASK_ENV", "test")
    from sincor2.mvp_app import app

    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture(scope="module")
def admin_client(mvp_client):
    with mvp_client.session_transaction() as sess:
        sess["is_admin"] = True
        # tests/pytest/conftest.py forces ADMIN_USERNAME=admin per test;
        # _is_admin_session compares the session name against it (the same
        # convention test_launch_gate_dashboards.py uses).
        sess["admin_username"] = "admin"
    return mvp_client


# ---------------------------------------------------------------------------
# 1. /sinc/recover-hook
# ---------------------------------------------------------------------------

def test_recover_hook_renders_200(mvp_client):
    r = mvp_client.get("/sinc/recover-hook")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "MetaMask" in html
    assert "private key" in html.lower()


# ---------------------------------------------------------------------------
# 2. /favicon.ico
# ---------------------------------------------------------------------------

def test_favicon_ico_serves_brand_asset(mvp_client):
    r = mvp_client.get("/favicon.ico")
    assert r.status_code == 200
    assert r.content_type.startswith("image/")
    assert len(r.data) > 0
    assert r.data == (STATIC / "sincor_hornet_logo.jpg").read_bytes()


# ---------------------------------------------------------------------------
# 3. executive_dashboard.html missing logo asset
# ---------------------------------------------------------------------------

def test_executive_dashboard_logo_asset_exists():
    source = (TEMPLATES / "executive_dashboard.html").read_text(encoding="utf-8")
    assert "sincor_business_logo.png" not in source
    for ref in re.findall(r'src="(/static/[^"]+)"', source):
        assert (REPO_ROOT / ref.lstrip("/")).is_file(), ref


# ---------------------------------------------------------------------------
# 4. admin_training_vault.html dead video / onboarding links
# ---------------------------------------------------------------------------

def test_training_vault_has_no_dead_video_or_onboarding_links():
    source = (TEMPLATES / "admin_training_vault.html").read_text(encoding="utf-8")
    assert "/videos/" not in source
    assert "/onboarding/next-step" not in source


# ---------------------------------------------------------------------------
# 5. docs.html dead /billing link
# ---------------------------------------------------------------------------

def test_docs_has_no_dead_billing_link():
    source = (TEMPLATES / "docs.html").read_text(encoding="utf-8")
    assert 'href="/billing"' not in source


# ---------------------------------------------------------------------------
# 6. operator_dashboard.html dead settlement fetch degrades honestly
# ---------------------------------------------------------------------------

def test_operator_dashboard_no_dead_settlement_fetch():
    source = (TEMPLATES / "operator_dashboard.html").read_text(encoding="utf-8")
    assert "settlement/records" not in source
    # The panel keeps an honest empty state instead of a perpetual load.
    assert "No settlement events recorded yet." in source


# ---------------------------------------------------------------------------
# Global guard: rendered pages carry no internal 404 targets
# ---------------------------------------------------------------------------

_ANON_PAGES = ["/sinc/recover-hook", "/docs"]
_ADMIN_PAGES = ["/executive-dashboard", "/operator", "/admin/training-vault"]


def _internal_targets(html: str) -> set[str]:
    targets: set[str] = set()
    for m in re.findall(r'(?:href|src|action)\s*=\s*"([^"]+)"', html):
        targets.add(m)
    for m in re.findall(r"fetch\(\s*[`'\"]([^`'\"]+)[`'\"]", html):
        targets.add(m)
    out = set()
    for t in targets:
        if t.startswith(("http://", "https://", "mailto:", "#", "data:",
                         "javascript:")):
            continue
        if "${" in t or "{{" in t:  # client-side templated URL, not literal
            continue
        path = t.split("?")[0].split("#")[0]
        if path:
            out.add(path)
    return out


def _assert_targets_resolve(app, client, page: str):
    r = client.get(page)
    assert r.status_code == 200, page
    html = r.get_data(as_text=True)
    adapter = app.url_map.bind("localhost")
    for target in sorted(_internal_targets(html)):
        if target.startswith("/static/"):
            assert (REPO_ROOT / target.lstrip("/")).is_file(), (
                f"{page}: missing static asset {target}")
            continue
        try:
            adapter.match(target)
        except Exception:
            pytest.fail(f"{page}: internal target {target} has no route")


def test_rendered_pages_have_no_dead_internal_targets(mvp_client, admin_client):
    from sincor2.mvp_app import app

    for page in _ANON_PAGES:
        _assert_targets_resolve(app, mvp_client, page)
    for page in _ADMIN_PAGES:
        _assert_targets_resolve(app, admin_client, page)
