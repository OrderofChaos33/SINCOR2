"""Base44-review site-surface fixes (Builder C, 2026-11-05).

Pins four Tier-1 findings from the external pre-launch review:
1. The genesis claim form collected a password that customer password
   login no longer accepts — a dead end. The claim flow must not ask
   for, require, or store a password.
2. Machine-readable manifests referenced doc URLs that 404
   (/docs/TRUST_STACK.md from trust-stack.json, /docs/api from the
   agent-card registry, /docs/a2a/bidder-wallet-flow from bidder-kit).
   Every internal URL those manifests hand out must resolve.
3. robots.txt must advertise the real XML sitemap (/sitemap.xml), and
   the XML sitemap must be valid XML listing only live routes.
4. The social share image must be a true 1200x630 asset and the base
   pages must carry twitter:card=summary_large_image plus accurate
   og:image dimensions.
"""
from __future__ import annotations

import os
import re
import sqlite3
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import pytest
from werkzeug.exceptions import NotFound
from werkzeug.routing import RequestRedirect


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


@pytest.fixture
def mvp_app_obj(mvp_client):
    from sincor2.mvp_app import app

    return app


def _resolves(app, path: str) -> bool:
    adapter = app.url_map.bind("localhost")
    for method in ("GET", "POST"):
        try:
            adapter.match(path, method=method, return_rule=True)
            return True
        except RequestRedirect:
            return True
        except NotFound:
            continue
        except Exception:
            continue
    return False


# ---------------------------------------------------------------------------
# 1. Genesis claim: no password anywhere in the flow
# ---------------------------------------------------------------------------

def test_gate_claim_form_has_no_password_field(mvp_client, monkeypatch):
    monkeypatch.delenv("SINCOR_LAUNCH_DATE", raising=False)
    r = mvp_client.get("/", follow_redirects=False)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'id="gate"' in html  # the claim form itself is still there
    assert 'name="password"' not in html
    assert 'type="password"' not in html
    assert "form.password" not in html


class _FakeGenesisSender:
    """W-136: captures genesis confirmation mail for the confirmed flow."""

    mode = "test"

    def __init__(self):
        self.sent = []

    def send_email(self, to_email, to_name="", subject="",
                   html_content="", text_content=None, metadata=None):
        self.sent.append({"to": to_email, "html": html_content,
                          "text": text_content or ""})
        return {"status": "sent", "message_id": "test-genesis"}

    def last_token(self):
        m = re.search(r"token=([A-Za-z0-9_\-]+)",
                      self.sent[-1]["html"] + " " + self.sent[-1]["text"])
        assert m
        return m.group(1)


def _inject_genesis_sender(monkeypatch):
    from sincor2 import genesis_cohort

    sender = _FakeGenesisSender()
    monkeypatch.setattr(genesis_cohort, "email_sender", sender)
    return sender


def test_genesis_claim_api_works_without_password(mvp_client, monkeypatch, tmp_path):
    # W-136 STALE update: this test pinned the vulnerable instant-bind
    # (claim -> ok + gate cookie with no email proof). The claim now
    # returns a neutral pending receipt and sets no cookie; the cookie
    # arrives only after the emailed token is confirmed — which is the
    # flow driven here. Original intent preserved: no password anywhere.
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    sender = _inject_genesis_sender(monkeypatch)
    r = mvp_client.post(
        "/api/genesis/claim",
        json={"email": "nopass@example.com", "wallet": ""},
    )
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
    assert "allocation" not in r.get_json()
    assert "sincor_genesis" not in (r.headers.get("Set-Cookie") or "")
    r = mvp_client.get(f"/api/genesis/confirm?token={sender.last_token()}")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
    assert r.get_json()["allocation"]
    assert "sincor_genesis" in (r.headers.get("Set-Cookie") or "")


def test_genesis_claim_api_still_rejects_bad_email(mvp_client, monkeypatch, tmp_path):
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    r = mvp_client.post("/api/genesis/claim", json={"email": "not-an-email"})
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_genesis_cohort_claim_needs_no_password_and_stores_none(
        monkeypatch, tmp_path):
    # W-136 STALE update: claim() used to bind instantly; it now starts
    # a pending claim. The confirmed flow is driven to preserve the
    # original intent: a passwordless claim binds the wallet and stores
    # no password anywhere.
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    from sincor2 import genesis_cohort

    sender = _inject_genesis_sender(monkeypatch)
    result = genesis_cohort.claim("plain@example.com", wallet="0x" + "11" * 20)
    assert result["ok"], result
    assert "allocation" not in result
    assert genesis_cohort.get_member("plain@example.com") is None
    confirmed = genesis_cohort.confirm_claim(sender.last_token())
    assert confirmed["ok"], confirmed
    member = genesis_cohort.get_member("plain@example.com")
    assert member is not None
    assert member["wallet"] == "0x" + "11" * 20
    db = sqlite3.connect(str(tmp_path / "genesis_cohort.db"))
    cols = [row[1] for row in db.execute("PRAGMA table_info(genesis_cohort)")]
    db.close()
    assert "password_hash" not in cols


def test_genesis_cohort_legacy_password_db_migrates(monkeypatch, tmp_path):
    """DBs created by the pre-fix code carry a password_hash column; the
    fix must keep those members readable and drop the column."""
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    db = sqlite3.connect(str(tmp_path / "genesis_cohort.db"))
    db.execute(
        "CREATE TABLE genesis_cohort ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL UNIQUE,"
        " password_hash TEXT NOT NULL, wallet TEXT, created_at TEXT NOT NULL)"
    )
    db.execute(
        "INSERT INTO genesis_cohort (email, password_hash, wallet, created_at)"
        " VALUES (?, ?, ?, ?)",
        ("legacy@example.com", "deadbeef$cafe", "", "2026-11-01T00:00:00+00:00"),
    )
    db.commit()
    db.close()

    from sincor2 import genesis_cohort

    assert genesis_cohort.get_member("legacy@example.com") is not None
    # W-136 STALE update: the fresh claim completes through email
    # confirmation before it counts as a member (was: instant bind).
    sender = _inject_genesis_sender(monkeypatch)
    result = genesis_cohort.claim("fresh@example.com")
    assert result["ok"], result
    assert genesis_cohort.genesis_count() == 1  # still pending
    confirmed = genesis_cohort.confirm_claim(sender.last_token())
    assert confirmed["ok"], confirmed
    assert genesis_cohort.genesis_count() == 2
    db = sqlite3.connect(str(tmp_path / "genesis_cohort.db"))
    cols = [row[1] for row in db.execute("PRAGMA table_info(genesis_cohort)")]
    db.close()
    assert "password_hash" not in cols


# ---------------------------------------------------------------------------
# 2. Manifest-referenced URLs resolve
# ---------------------------------------------------------------------------

_MANIFEST_SOURCES = (
    "/llms.txt",
    "/ai.txt",
    "/.well-known/agent-card.json",
    "/.well-known/agent.json",
    "/.well-known/sincor-marketplace.json",
    "/.well-known/trust-stack.json",
    "/v1/a2a/cards",
)


def _manifest_internal_paths(mvp_client):
    paths = set()
    for source in _MANIFEST_SOURCES:
        r = mvp_client.get(source)
        assert r.status_code == 200, source
        body = r.get_data(as_text=True)
        for m in re.findall(r"https://getsincor\.com([^\s\"'\)\]]*)", body):
            p = urlparse(m.rstrip(".,;")).path
            if p:
                paths.add(p)
        for m in re.findall(r'"(?:doc|docs|documentationUrl)":\s*"(/[^"]+)"', body):
            paths.add(urlparse(m).path)
    return paths


def test_every_manifest_referenced_internal_url_resolves(mvp_client, mvp_app_obj):
    paths = _manifest_internal_paths(mvp_client)
    assert paths, "manifests exposed no internal URLs at all"
    broken = []
    for path in sorted(paths):
        concrete = re.sub(r"\{[^}]+\}|<[^>]+>", "x", path)
        if not _resolves(mvp_app_obj, concrete):
            broken.append(path)
            continue
        if "{" not in path and "<" not in path:
            status = mvp_client.get(path).status_code
            # POST-only machine endpoints answer 405 to GET; that still
            # proves the route exists. A 404 is the failure mode here.
            if status == 404:
                broken.append(f"{path} (GET 404)")
    assert broken == [], f"manifest-referenced URLs that 404: {broken}"


def test_no_manifest_points_at_known_dead_doc_paths(mvp_client):
    for source in _MANIFEST_SOURCES:
        body = mvp_client.get(source).get_data(as_text=True)
        assert "getsincor.com/docs/api" not in body, source
        assert "/docs/a2a/bidder-wallet-flow" not in body, source


def test_trust_stack_doc_route_serves_the_repo_doc(mvp_client):
    r = mvp_client.get("/docs/TRUST_STACK.md")
    assert r.status_code == 200
    assert "Trust Stack" in r.get_data(as_text=True)


# ---------------------------------------------------------------------------
# 3. robots.txt -> real XML sitemap; sitemap lists only live routes
# ---------------------------------------------------------------------------

def test_robots_advertises_xml_sitemap(mvp_client):
    body = mvp_client.get("/robots.txt").get_data(as_text=True)
    assert "Sitemap: https://getsincor.com/sitemap.xml" in body
    assert "Sitemap: https://getsincor.com/sitemap\n" not in body


def test_sitemap_xml_is_valid_xml_with_only_live_routes(mvp_client, mvp_app_obj):
    r = mvp_client.get("/sitemap.xml")
    assert r.status_code == 200
    root = ET.fromstring(r.get_data(as_text=True))  # raises if not valid XML
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locs = [el.text for el in root.findall("sm:url/sm:loc", ns)]
    assert len(locs) >= 10
    unresolved = [
        loc for loc in locs
        if not _resolves(mvp_app_obj, urlparse(loc).path or "/")
    ]
    assert unresolved == [], f"sitemap.xml entries with no route: {unresolved}"


# ---------------------------------------------------------------------------
# 4. Share card: true 1200x630 asset + twitter/og meta
# ---------------------------------------------------------------------------

def test_share_image_asset_is_1200x630(mvp_client):
    from PIL import Image
    import io

    r = mvp_client.get("/static/sincor_og_1200x630.jpg")
    assert r.status_code == 200
    img = Image.open(io.BytesIO(r.data))
    assert img.size == (1200, 630)


def test_homepage_and_gate_carry_twitter_card_and_og_dimensions(
        mvp_client, monkeypatch):
    monkeypatch.delenv("SINCOR_LAUNCH_DATE", raising=False)
    gate = mvp_client.get("/", follow_redirects=False).get_data(as_text=True)
    for html in (gate,):
        assert 'name="twitter:card" content="summary_large_image"' in html
        assert "sincor_og_1200x630.jpg" in html
        assert 'property="og:image:width" content="1200"' in html
        assert 'property="og:image:height" content="630"' in html
    monkeypatch.setenv("SINCOR_LAUNCH_DATE", "2026-01-01")
    home = mvp_client.get("/", follow_redirects=False).get_data(as_text=True)
    assert 'name="twitter:card" content="summary_large_image"' in home
    assert "sincor_og_1200x630.jpg" in home
    assert 'property="og:image:width" content="1200"' in home
    assert 'property="og:image:height" content="630"' in home


def test_base_layout_carries_twitter_card_and_og_dimensions():
    here = os.path.dirname(os.path.abspath(__file__))
    base = os.path.join(here, "..", "..", "templates", "layouts", "base.html")
    with open(base, encoding="utf-8") as fh:
        html = fh.read()
    assert 'name="twitter:card" content="summary_large_image"' in html
    assert "sincor_og_1200x630.jpg" in html
    assert 'property="og:image:width" content="1200"' in html
    assert 'property="og:image:height" content="630"' in html
