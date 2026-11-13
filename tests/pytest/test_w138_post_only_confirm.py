"""W-138: genesis confirm must bind on POST only, never on GET.

Hunt-41 finding: GET /api/genesis/confirm called confirm_claim()
directly, so anything that merely PREFETCHES the emailed link — a
mail-security scanner, a chat link-preview bot, an AV URL checker —
completed the confirmation. In the W-136 squat scenario that means
the scanner binds the ATTACKER's pending wallet to the victim's
email before the victim ever sees the message.

The fix: GET renders a neutral confirmation page carrying a button
that POSTs the token; only POST binds, sets the genesis cookie, or
consumes the token. A prefetch therefore changes no state at all,
and the real owner's later POST still works (the token is neither
consumed nor rotated by the GET). POST behavior (JSON body, form
field, reclaim-by-confirmation) is unchanged from W-136.
"""
from __future__ import annotations

import os
import re

import pytest

VICTIM = "victim@example.com"
VICTIM_WALLET = "0x" + "aa" * 20
ATTACKER_WALLET = "0x" + "bb" * 20
NEW_WALLET = "0x" + "cc" * 20

TOKEN_RE = re.compile(r"token=([A-Za-z0-9_\-]+)")


class FakeSender:
    """Injectable sender capturing outgoing confirmation mail."""

    def __init__(self, mode="test"):
        self.mode = mode
        self.sent = []

    def send_email(self, to_email, to_name="", subject="",
                   html_content="", text_content=None, metadata=None):
        self.sent.append({
            "to": to_email, "subject": subject,
            "html": html_content, "text": text_content or "",
        })
        return {"status": "sent", "message_id": "msg-w138"}

    def last_token(self, to_email=None):
        for mail in reversed(self.sent):
            if to_email is not None and mail["to"] != to_email:
                continue
            m = TOKEN_RE.search(mail["html"] + " " + mail["text"])
            assert m, f"no token in captured email: {mail}"
            return m.group(1)
        raise AssertionError("no captured email")


@pytest.fixture
def fake_sender(monkeypatch):
    from sincor2 import genesis_cohort

    sender = FakeSender()
    monkeypatch.setattr(genesis_cohort, "email_sender", sender)
    return sender


@pytest.fixture
def mvp_client(monkeypatch):
    os.environ.setdefault("FLASK_ENV", "test")
    os.environ.setdefault("ENVIRONMENT", "test")
    os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key-32-char-minimum-ok")
    os.environ.setdefault("ADMIN_USERNAME", "admin")
    os.environ.setdefault("ADMIN_PASSWORD", "admin-password-32-char-minimum-ok")
    from sincor2.mvp_app import app, limiter

    limiter.reset()
    app.config["TESTING"] = True
    app.config["SERVER_NAME"] = None
    yield app.test_client()
    limiter.reset()


def _claim(mvp_client, email, wallet):
    r = mvp_client.post(
        "/api/genesis/claim", json={"email": email, "wallet": wallet},
    )
    assert r.status_code == 200, r.get_data(as_text=True)
    return r


# ---------------------------------------------------------------------------
# The W-138 PoC: a prefetch-style GET must change nothing
# ---------------------------------------------------------------------------

def test_get_with_valid_token_does_not_bind_or_set_cookie(
        mvp_client, monkeypatch, tmp_path, fake_sender):
    from sincor2 import genesis_cohort

    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    _claim(mvp_client, VICTIM, VICTIM_WALLET)
    token = fake_sender.last_token(to_email=VICTIM)

    # A mail scanner prefetching the emailed link:
    r = mvp_client.get(f"/api/genesis/confirm?token={token}")
    assert r.status_code == 200
    # Nothing bound, nobody counted, no cookie set.
    assert genesis_cohort.get_member(VICTIM) is None
    assert genesis_cohort.get_member_by_wallet(VICTIM_WALLET) is None
    assert genesis_cohort.genesis_count() == 0
    assert "sincor_genesis" not in (r.headers.get("Set-Cookie") or "")


def test_get_renders_confirmation_page_that_posts(
        mvp_client, monkeypatch, tmp_path, fake_sender):
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    _claim(mvp_client, VICTIM, VICTIM_WALLET)
    token = fake_sender.last_token(to_email=VICTIM)

    r = mvp_client.get(f"/api/genesis/confirm?token={token}")
    assert r.status_code == 200
    assert "text/html" in (r.headers.get("Content-Type") or "")
    page = r.get_data(as_text=True)
    # The page carries a form that POSTs the token on a human click.
    assert 'method="post"' in page.lower()
    assert 'action="/api/genesis/confirm"' in page
    assert f'value="{token}"' in page
    # ...and does not itself report a completed confirmation.
    assert '"ok": true' not in page
    assert "allocation" not in page


def test_prefetch_does_not_consume_token_post_still_binds(
        mvp_client, monkeypatch, tmp_path, fake_sender):
    from sincor2 import genesis_cohort

    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    _claim(mvp_client, VICTIM, VICTIM_WALLET)
    token = fake_sender.last_token(to_email=VICTIM)

    # Scanner prefetch first (twice — some scanners fetch repeatedly).
    for _ in range(2):
        r = mvp_client.get(f"/api/genesis/confirm?token={token}")
        assert r.status_code == 200
    assert genesis_cohort.get_member(VICTIM) is None

    # The real owner clicks the button: the browser submits the form
    # (application/x-www-form-urlencoded POST).
    r = mvp_client.post("/api/genesis/confirm", data={"token": token})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["allocation"]
    assert "sincor_genesis" in (r.headers.get("Set-Cookie") or "")
    member = genesis_cohort.get_member(VICTIM)
    assert member["wallet"] == VICTIM_WALLET
    assert member["allocation"] == body["allocation"]


def test_scanner_prefetch_cannot_complete_attacker_squat(
        mvp_client, monkeypatch, tmp_path, fake_sender):
    from sincor2 import genesis_cohort

    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    # Attacker claims the victim's email with the attacker's wallet.
    _claim(mvp_client, VICTIM, ATTACKER_WALLET)
    attacker_token = fake_sender.last_token(to_email=VICTIM)
    # The victim's mail scanner prefetches the link. Pre-fix this GET
    # bound the attacker's wallet; now it must bind nothing.
    r = mvp_client.get(f"/api/genesis/confirm?token={attacker_token}")
    assert r.status_code == 200
    assert genesis_cohort.get_member(VICTIM) is None
    assert genesis_cohort.get_member_by_wallet(ATTACKER_WALLET) is None
    assert "sincor_genesis" not in (r.headers.get("Set-Cookie") or "")


# ---------------------------------------------------------------------------
# POST behavior is unchanged (W-136 contract)
# ---------------------------------------------------------------------------

def test_post_json_still_binds_and_sets_cookie(
        mvp_client, monkeypatch, tmp_path, fake_sender):
    from sincor2 import genesis_cohort

    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    _claim(mvp_client, VICTIM, VICTIM_WALLET)
    token = fake_sender.last_token(to_email=VICTIM)
    r = mvp_client.post("/api/genesis/confirm", json={"token": token})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
    assert "sincor_genesis" in (r.headers.get("Set-Cookie") or "")
    assert genesis_cohort.get_member(VICTIM)["wallet"] == VICTIM_WALLET


def test_post_bogus_token_fails_closed(
        mvp_client, monkeypatch, tmp_path, fake_sender):
    from sincor2 import genesis_cohort

    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    _claim(mvp_client, VICTIM, VICTIM_WALLET)
    r = mvp_client.post("/api/genesis/confirm", json={"token": "nope"})
    assert r.status_code == 400
    assert r.get_json()["ok"] is False
    assert "sincor_genesis" not in (r.headers.get("Set-Cookie") or "")
    assert genesis_cohort.get_member(VICTIM) is None


def test_get_bogus_token_renders_neutral_page_and_binds_nothing(
        mvp_client, monkeypatch, tmp_path, fake_sender):
    from sincor2 import genesis_cohort

    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    _claim(mvp_client, VICTIM, VICTIM_WALLET)
    r = mvp_client.get("/api/genesis/confirm?token=<redacted>")
    assert r.status_code == 200
    assert "text/html" in (r.headers.get("Content-Type") or "")
    assert "sincor_genesis" not in (r.headers.get("Set-Cookie") or "")
    assert genesis_cohort.get_member(VICTIM) is None
    # Failed GET probing did not disturb the real pending claim.
    token = fake_sender.last_token(to_email=VICTIM)
    r = mvp_client.post("/api/genesis/confirm", json={"token": token})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


# ---------------------------------------------------------------------------
# Reclaim path stays intact — and a GET cannot trigger it either
# ---------------------------------------------------------------------------

def test_reclaim_still_works_via_post_and_get_cannot_reclaim(
        mvp_client, monkeypatch, tmp_path, fake_sender):
    from sincor2 import genesis_cohort

    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    # Initial confirmed membership.
    _claim(mvp_client, VICTIM, VICTIM_WALLET)
    token = fake_sender.last_token(to_email=VICTIM)
    r = mvp_client.post("/api/genesis/confirm", json={"token": token})
    assert r.status_code == 200
    first_allocation = r.get_json()["allocation"]

    # Reclaim: a fresh claim rotates the token (W-136 behavior).
    _claim(mvp_client, VICTIM, NEW_WALLET)
    reclaim_token = fake_sender.last_token(to_email=VICTIM)
    assert reclaim_token != token

    # A prefetch of the reclaim link must NOT swap the wallet.
    r = mvp_client.get(f"/api/genesis/confirm?token={reclaim_token}")
    assert r.status_code == 200
    assert genesis_cohort.get_member(VICTIM)["wallet"] == VICTIM_WALLET

    # The owner's POST completes the reclaim; allocation is kept.
    r = mvp_client.post(
        "/api/genesis/confirm", data={"token": reclaim_token},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["existing"] is True
    assert body["allocation"] == first_allocation
    assert genesis_cohort.get_member(VICTIM)["wallet"] == NEW_WALLET
    assert genesis_cohort.genesis_count() == 1
