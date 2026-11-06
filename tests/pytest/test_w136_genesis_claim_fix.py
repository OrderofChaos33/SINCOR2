"""W-136: genesis claim email-ownership fix (hunt-40 PoC regression).

The vulnerable flow bound any email to any wallet on a bare
POST /api/genesis/claim — no ownership proof — and leaked the
allocation number on duplicates (membership oracle):

  attacker claims victim@example.com with attacker's wallet
    -> {ok:true, existing:false, allocation:"00001"} bound to attacker
  victim claims their own email later
    -> {ok:true, existing:true, allocation:"00001"} STILL attacker-bound

The fix: a claim creates a PENDING claim and emails a single-use,
expiring confirmation token to the address; the wallet binds only on
confirmation. Already-confirmed members reclaim (replace) their wallet
through the same confirmed path. The initial response is a neutral
"check your email" receipt — no allocation, no existence flag — and
sets no genesis cookie. Stub-mode email delivery fails closed (no
usable pending claim), mirroring the onboarding challenge in auth.py.
"""
from __future__ import annotations

import os
import re
import sqlite3

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
        return {"status": "sent", "message_id": "msg-w136"}

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
def cohort(tmp_path, monkeypatch, fake_sender):
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    from sincor2 import genesis_cohort

    return genesis_cohort


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


def _confirmed(cohort, sender, email, wallet):
    """Drive the full confirmed flow; return the confirm result."""
    result = cohort.claim(email, wallet=wallet)
    assert result["ok"], result
    confirmed = cohort.confirm_claim(sender.last_token(to_email=email))
    assert confirmed["ok"], confirmed
    return confirmed


# ---------------------------------------------------------------------------
# Hunt-40 PoC, end to end
# ---------------------------------------------------------------------------

def test_attacker_cannot_squat_victim_email(cohort, fake_sender):
    # Attacker claims the victim's email with the attacker's wallet.
    receipt = cohort.claim(VICTIM, wallet=ATTACKER_WALLET)
    # (b) neutral receipt: no allocation, no existence flag.
    assert receipt == {
        "ok": True,
        "status": "pending_confirmation",
        "message": "Check your email to confirm your genesis claim.",
    }
    # (a) nothing is bound to the attacker — or to anyone.
    assert cohort.get_member(VICTIM) is None
    assert cohort.get_member_by_wallet(ATTACKER_WALLET) is None
    assert cohort.is_genesis_member(VICTIM) is False
    assert cohort.genesis_count() == 0

    # The confirmation token went to the VICTIM's inbox; the victim's
    # own claim replaces the pending row (invalidating the attacker's
    # token), and confirming it binds the VICTIM's wallet.
    attacker_token = fake_sender.last_token(to_email=VICTIM)
    confirmed = _confirmed(cohort, fake_sender, VICTIM, VICTIM_WALLET)
    member = cohort.get_member(VICTIM)
    assert member is not None
    assert member["wallet"] == VICTIM_WALLET
    assert member["allocation"] == confirmed["allocation"]
    assert cohort.get_member_by_wallet(ATTACKER_WALLET) is None
    # The token from the attacker's request no longer works.
    replay = cohort.confirm_claim(attacker_token)
    assert replay["ok"] is False
    assert cohort.get_member(VICTIM)["wallet"] == VICTIM_WALLET


def test_api_poc_attacker_binds_nothing_and_gets_no_cookie(
        mvp_client, monkeypatch, tmp_path, fake_sender):
    from sincor2 import genesis_cohort

    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    r = mvp_client.post(
        "/api/genesis/claim",
        json={"email": VICTIM, "wallet": ATTACKER_WALLET},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert "allocation" not in body
    assert "existing" not in body
    assert "sincor_genesis" not in (r.headers.get("Set-Cookie") or "")
    assert genesis_cohort.get_member(VICTIM) is None
    assert genesis_cohort.get_member_by_wallet(ATTACKER_WALLET) is None

    # Victim confirms via the emailed link (GET, as a mail client does).
    token = fake_sender.last_token(to_email=VICTIM)
    # NOTE: this token is the one from the attacker's request — it was
    # emailed to the victim, so the victim confirming it with the
    # pending (attacker) wallet is NOT what happens here; the victim
    # makes their own claim first, which rotates the pending token.
    r = mvp_client.post(
        "/api/genesis/claim",
        json={"email": VICTIM, "wallet": VICTIM_WALLET},
    )
    assert r.status_code == 200
    token = fake_sender.last_token(to_email=VICTIM)
    r = mvp_client.get(f"/api/genesis/confirm?token={token}")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["allocation"]
    assert "sincor_genesis" in (r.headers.get("Set-Cookie") or "")
    member = genesis_cohort.get_member(VICTIM)
    assert member["wallet"] == VICTIM_WALLET
    assert member["allocation"] == body["allocation"]


# ---------------------------------------------------------------------------
# Neutral receipt / no oracle
# ---------------------------------------------------------------------------

def test_receipt_identical_for_new_pending_and_existing(cohort, fake_sender):
    new_receipt = cohort.claim("new@example.com", wallet=VICTIM_WALLET)
    pending_receipt = cohort.claim("new@example.com", wallet=VICTIM_WALLET)
    _confirmed(cohort, fake_sender, "member@example.com", VICTIM_WALLET)
    existing_receipt = cohort.claim("member@example.com", wallet=NEW_WALLET)
    assert new_receipt == pending_receipt == existing_receipt
    for receipt in (new_receipt, pending_receipt, existing_receipt):
        assert "allocation" not in receipt
        assert "existing" not in receipt
        assert "count" not in receipt
    # The unconfirmed reclaim attempt changed nothing.
    assert cohort.get_member("member@example.com")["wallet"] == VICTIM_WALLET


# ---------------------------------------------------------------------------
# Reclaim path
# ---------------------------------------------------------------------------

def test_reclaim_replaces_wallet_only_after_confirmation(cohort, fake_sender):
    first = _confirmed(cohort, fake_sender, VICTIM, VICTIM_WALLET)
    # Unconfirmed reclaim: nothing changes.
    receipt = cohort.claim(VICTIM, wallet=NEW_WALLET)
    assert receipt["ok"] is True
    assert cohort.get_member(VICTIM)["wallet"] == VICTIM_WALLET
    # Confirmed reclaim: wallet replaced, allocation (identity) kept.
    confirmed = cohort.confirm_claim(fake_sender.last_token(to_email=VICTIM))
    assert confirmed["ok"] is True
    assert confirmed["existing"] is True
    assert confirmed["allocation"] == first["allocation"]
    member = cohort.get_member(VICTIM)
    assert member["wallet"] == NEW_WALLET
    assert cohort.get_member_by_wallet(VICTIM_WALLET) is None
    assert cohort.get_member_by_wallet(NEW_WALLET)["email"] == VICTIM
    assert cohort.genesis_count() == 1


# ---------------------------------------------------------------------------
# Token security: single-use, expiry, tampering
# ---------------------------------------------------------------------------

def test_token_is_single_use(cohort, fake_sender):
    cohort.claim(VICTIM, wallet=VICTIM_WALLET)
    token = fake_sender.last_token(to_email=VICTIM)
    assert cohort.confirm_claim(token)["ok"] is True
    replay = cohort.confirm_claim(token)
    assert replay["ok"] is False
    assert cohort.get_member(VICTIM)["wallet"] == VICTIM_WALLET


def test_expired_token_fails(cohort, fake_sender, tmp_path):
    cohort.claim(VICTIM, wallet=VICTIM_WALLET)
    token = fake_sender.last_token(to_email=VICTIM)
    db = sqlite3.connect(str(tmp_path / "genesis_cohort.db"))
    db.execute(
        "UPDATE genesis_pending_claims SET expires_at = ?",
        ("2000-01-01T00:00:00+00:00",),
    )
    db.commit()
    db.close()
    result = cohort.confirm_claim(token)
    assert result["ok"] is False
    assert cohort.get_member(VICTIM) is None


def test_tampered_and_unknown_tokens_fail(cohort, fake_sender):
    cohort.claim(VICTIM, wallet=VICTIM_WALLET)
    token = fake_sender.last_token(to_email=VICTIM)
    for bad in (token + "x", token[:-1] + ("A" if token[-1] != "A" else "B"),
                "totally-made-up-token", ""):
        assert cohort.confirm_claim(bad)["ok"] is False, bad
    assert cohort.get_member(VICTIM) is None
    # The real token still works afterwards (failed attempts do not
    # consume it).
    assert cohort.confirm_claim(token)["ok"] is True


def test_only_token_hash_is_stored(cohort, fake_sender, tmp_path):
    cohort.claim(VICTIM, wallet=VICTIM_WALLET)
    token = fake_sender.last_token(to_email=VICTIM)
    db = sqlite3.connect(str(tmp_path / "genesis_cohort.db"))
    rows = db.execute("SELECT token_hash FROM genesis_pending_claims").fetchall()
    db.close()
    assert len(rows) == 1
    assert rows[0][0] != token
    assert token not in rows[0][0]
    assert len(rows[0][0]) == 64  # sha256 hex


# ---------------------------------------------------------------------------
# Fail closed when email delivery is unavailable (stub mode)
# ---------------------------------------------------------------------------

def test_stub_mode_fails_closed_module(tmp_path, monkeypatch):
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    from sincor2 import genesis_cohort

    stub = FakeSender(mode="stub")
    monkeypatch.setattr(genesis_cohort, "email_sender", stub)
    result = genesis_cohort.claim(VICTIM, wallet=ATTACKER_WALLET)
    assert result["ok"] is False
    assert result["email_unavailable"] is True
    assert stub.sent == []  # send was never attempted in stub mode
    # No usable pending claim was created and nothing bound.
    assert genesis_cohort.genesis_count() == 0  # also ensures schema exists
    db = sqlite3.connect(str(tmp_path / "genesis_cohort.db"))
    n = db.execute("SELECT COUNT(*) FROM genesis_pending_claims").fetchone()[0]
    db.close()
    assert n == 0
    assert genesis_cohort.get_member(VICTIM) is None


def test_send_failure_fails_closed_and_leaves_no_pending(
        tmp_path, monkeypatch):
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    from sincor2 import genesis_cohort

    class FailingSender(FakeSender):
        def send_email(self, **kw):
            return {"status": "failed", "error": "boom"}

    monkeypatch.setattr(genesis_cohort, "email_sender", FailingSender())
    result = genesis_cohort.claim(VICTIM, wallet=VICTIM_WALLET)
    assert result["ok"] is False
    assert result["email_unavailable"] is True
    db = sqlite3.connect(str(tmp_path / "genesis_cohort.db"))
    n = db.execute("SELECT COUNT(*) FROM genesis_pending_claims").fetchone()[0]
    db.close()
    assert n == 0
    assert genesis_cohort.get_member(VICTIM) is None


def test_stub_mode_api_claim_is_503_without_cookie(
        mvp_client, monkeypatch, tmp_path):
    from sincor2 import genesis_cohort

    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(genesis_cohort, "email_sender", FakeSender(mode="stub"))
    r = mvp_client.post(
        "/api/genesis/claim",
        json={"email": VICTIM, "wallet": ATTACKER_WALLET},
    )
    assert r.status_code == 503
    assert r.get_json()["ok"] is False
    assert "sincor_genesis" not in (r.headers.get("Set-Cookie") or "")
    assert genesis_cohort.get_member(VICTIM) is None


# ---------------------------------------------------------------------------
# API surface details
# ---------------------------------------------------------------------------

def test_api_confirm_via_post_json_and_bad_token(
        mvp_client, monkeypatch, tmp_path, fake_sender):
    from sincor2 import genesis_cohort

    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    bad = mvp_client.post("/api/genesis/confirm", json={"token": "nope"})
    assert bad.status_code == 400
    assert bad.get_json()["ok"] is False
    assert "sincor_genesis" not in (bad.headers.get("Set-Cookie") or "")

    r = mvp_client.post(
        "/api/genesis/claim",
        json={"email": VICTIM, "wallet": VICTIM_WALLET},
    )
    assert r.status_code == 200
    token = fake_sender.last_token(to_email=VICTIM)
    r = mvp_client.post("/api/genesis/confirm", json={"token": token})
    assert r.status_code == 200
    assert r.get_json()["allocation"] == genesis_cohort.get_member(VICTIM)["allocation"]
    assert "sincor_genesis" in (r.headers.get("Set-Cookie") or "")


def test_password_shim_still_ignored_and_never_stored(cohort, fake_sender, tmp_path):
    confirmed = None
    result = cohort.claim("shim@example.com", "supersecret-pass-1",
                          wallet=VICTIM_WALLET)
    assert result["ok"], result
    confirmed = cohort.confirm_claim(fake_sender.last_token(to_email="shim@example.com"))
    assert confirmed["ok"], confirmed
    db = sqlite3.connect(str(tmp_path / "genesis_cohort.db"))
    for table in ("genesis_cohort", "genesis_pending_claims"):
        cols = [row[1] for row in db.execute(f"PRAGMA table_info({table})")]
        assert "password_hash" not in cols
        assert "password" not in cols
    db.close()
