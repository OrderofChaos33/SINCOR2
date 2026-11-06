"""Base44 review fix wave (Builder B): genesis claim abuse controls + CSP.

External review (2026-11-06) verified against production:
  * 12 rapid POSTs to the claim write path -> 400 x 12, no 429 and no
    RateLimit headers of any kind.
  * Responses carried a good security-header set but NO
    Content-Security-Policy.

Pins, against the real production app assembly (sincor2.mvp_app):
  * POST /api/genesis/claim is limited to 5/minute per client address
    (stacked under a 30/hour ceiling): the 6th rapid POST is a 429.
  * Rate-limit headers (X-RateLimit-*) ride on claim responses, and the
    429 carries Retry-After plus the shared {"error": "rate_limited"}
    JSON shape.
  * A normal single claim behaves as before in shape (200 for a valid
    claim; 400 for an invalid one) — W-136 update: the genesis cookie
    is set by the emailed-token confirmation, not by the bare claim.
  * Every response carries a Content-Security-Policy with
    default-src 'self', object-src 'none', base-uri 'self' and
    frame-ancestors 'self', alongside the pre-existing header set.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def mvp_client():
    os.environ.setdefault("FLASK_ENV", "test")
    os.environ.setdefault("ENVIRONMENT", "test")
    os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key-32-char-minimum-ok")
    os.environ.setdefault("ADMIN_USERNAME", "admin")
    os.environ.setdefault("ADMIN_PASSWORD", "admin-password-32-char-minimum-ok")
    from sincor2.mvp_app import app, limiter

    # Hermetic limiter slate: the claim bucket is keyed on the test
    # client's address, shared with every other mvp test in the process.
    limiter.reset()
    app.config["TESTING"] = True
    app.config["SERVER_NAME"] = None
    yield app.test_client()
    limiter.reset()


BAD_CLAIM = {"email": "not-an-email", "password": "testpass123"}


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
        import re

        m = re.search(r"token=([A-Za-z0-9_\-]+)",
                      self.sent[-1]["html"] + " " + self.sent[-1]["text"])
        assert m
        return m.group(1)


def test_single_valid_claim_behaves_as_before(mvp_client, monkeypatch, tmp_path):
    # W-136 STALE update: a bare claim used to return 200 + the genesis
    # cookie with no email proof. It now returns the neutral pending
    # receipt (200, no allocation, no cookie); the cookie is set when
    # the emailed token is confirmed — driven here to preserve the
    # original intent (a normal valid claim flow succeeds end to end).
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    from sincor2 import genesis_cohort

    sender = _FakeGenesisSender()
    monkeypatch.setattr(genesis_cohort, "email_sender", sender)
    r = mvp_client.post(
        "/api/genesis/claim",
        json={"email": "b44-single@example.com", "password": "testpass123", "wallet": ""},
    )
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
    assert "allocation" not in r.get_json()
    assert "sincor_genesis" not in (r.headers.get("Set-Cookie") or "")
    r = mvp_client.get(f"/api/genesis/confirm?token={sender.last_token()}")
    assert r.status_code == 200
    assert "sincor_genesis" in (r.headers.get("Set-Cookie") or "")


def test_single_invalid_claim_behaves_as_before(mvp_client, monkeypatch, tmp_path):
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    r = mvp_client.post("/api/genesis/claim", json=BAD_CLAIM)
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_claim_responses_carry_rate_limit_headers(mvp_client, monkeypatch, tmp_path):
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    r = mvp_client.post("/api/genesis/claim", json=BAD_CLAIM)
    assert r.status_code == 400
    # flask-limiter header names (RATELIMIT_HEADERS_ENABLED): the limit
    # that governed this response and what is left of it must be visible
    # to the caller, not just inferable from a later 429.
    assert r.headers.get("X-RateLimit-Limit") is not None
    assert r.headers.get("X-RateLimit-Remaining") is not None


def test_claim_rapid_posts_get_429(mvp_client, monkeypatch, tmp_path):
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    codes = [
        mvp_client.post("/api/genesis/claim", json=BAD_CLAIM).status_code
        for _ in range(6)
    ]
    # 5 per minute: five ordinary refusals (400 = bad email, unchanged
    # handler behavior), the 6th rapid POST is refused by the limiter.
    assert codes[:5] == [400] * 5
    assert codes[5] == 429


def test_claim_429_shape_and_retry_after(mvp_client, monkeypatch, tmp_path):
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    resp = None
    for _ in range(6):
        resp = mvp_client.post("/api/genesis/claim", json=BAD_CLAIM)
    assert resp is not None and resp.status_code == 429
    assert resp.get_json() == {"error": "rate_limited"}
    assert resp.headers.get("Retry-After") is not None
    assert resp.headers.get("X-RateLimit-Limit") is not None


def test_csp_header_on_public_page(mvp_client):
    r = mvp_client.get("/login", follow_redirects=False)
    assert r.status_code == 200
    csp = r.headers.get("Content-Security-Policy")
    assert csp, "Content-Security-Policy missing"
    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "base-uri 'self'" in csp
    assert "frame-ancestors 'self'" in csp
    # The pre-existing header set must survive next to the new one.
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("Strict-Transport-Security")


def test_csp_header_on_claim_api_response(mvp_client, monkeypatch, tmp_path):
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    r = mvp_client.post("/api/genesis/claim", json=BAD_CLAIM)
    csp = r.headers.get("Content-Security-Policy")
    assert csp, "Content-Security-Policy missing on API response"
    assert "object-src 'none'" in csp
