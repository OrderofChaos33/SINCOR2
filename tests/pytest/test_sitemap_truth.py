"""Sitemap truth tests (observability build #10, 2026-10-28).

The /sitemap page linked to routes that do not exist on the production
app (/enterprise-dashboard, /discovery-dashboard, /affiliate-program,
/franchise-empire, /media-packs — templates on disk, never routed), to
/sitemap.xml (no route existed), to dead "#" rows, and to the
authenticated /api/orders endpoint. These tests crawl the sitemap page's
internal links against the production app's route table and assert every
one resolves (route existence, not live HTTP), and that /sitemap.xml is
a real route whose own entries also all resolve.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

import pytest
from werkzeug.exceptions import NotFound
from werkzeug.routing import RequestRedirect

DEAD_PATHS = (
    "/enterprise-dashboard",
    "/discovery-dashboard",
    "/affiliate-program",
    "/franchise-empire",
    "/media-packs",
    "/api/orders",  # authenticated API endpoint, not a public page
)


@pytest.fixture(scope="module")
def app():
    from sincor2.mvp_app import app as flask_app

    flask_app.config.update(TESTING=True)
    return flask_app


@pytest.fixture(scope="module")
def client(app):
    return app.test_client()


def _resolves(app, path: str) -> bool:
    adapter = app.url_map.bind("localhost")
    try:
        adapter.match(path, method="GET", return_rule=True)
        return True
    except RequestRedirect:
        return True  # a redirect rule still proves the route exists
    except NotFound:
        return False


def _internal_paths(html: str):
    paths = []
    for href in re.findall(r'href="([^"]+)"', html):
        if href.startswith("/") and not href.startswith("//"):
            paths.append(urlparse(href).path or "/")
    return paths


@pytest.fixture(scope="module")
def sitemap_html(client):
    resp = client.get("/sitemap")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_sitemap_has_no_dead_anchor_rows(sitemap_html):
    assert 'href="#"' not in sitemap_html


def test_sitemap_drops_known_dead_paths(sitemap_html):
    linked = set(_internal_paths(sitemap_html))
    for dead in DEAD_PATHS:
        assert dead not in linked, f"sitemap still links to dead path: {dead}"


def test_every_sitemap_link_resolves_on_production_app(app, sitemap_html):
    linked = sorted(set(_internal_paths(sitemap_html)))
    assert linked, "sitemap page exposes no internal links at all"
    unresolved = [p for p in linked if not _resolves(app, p)]
    assert unresolved == [], f"sitemap links with no production route: {unresolved}"


def test_sitemap_xml_route_serves_real_sitemap(app, client):
    resp = client.get("/sitemap.xml")
    assert resp.status_code == 200
    assert "xml" in resp.content_type
    body = resp.get_data(as_text=True)
    locs = re.findall(r"<loc>([^<]+)</loc>", body)
    assert len(locs) >= 10, "XML sitemap should list the public pages"
    paths = [urlparse(loc).path or "/" for loc in locs]
    assert "/" in paths
    assert "/security" in paths
    unresolved = [p for p in paths if not _resolves(app, p)]
    assert unresolved == [], f"sitemap.xml entries with no production route: {unresolved}"
