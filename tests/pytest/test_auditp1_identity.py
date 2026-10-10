"""P1 audit remediation — Batch A (Identity fixes) regression tests.

Findings covered (src/sincor2/mvp_blueprints/):
  1. pages.py login_page — identifier-only customer login disabled (403 + log).
  2. billing.py get_customer_orders — JWT required, scoped to caller's own email.
  3. billing.py cancel_subscription — JWT required, owner must match identity.
  4. auth.py OAuth callbacks — provider email_verified required before linking.
  5. auth.py onboarding — email-OTP proof required before profile/consent writes.

Each finding has attack tests (vulnerability closed) and happy-path tests
(legitimate flow still works), plus adversarial edge cases.
"""
from __future__ import annotations

import os
import re
import tempfile

# Isolate ALL persistent state (orders/customer DB, platform payments DB,
# genesis cohort DB) BEFORE importing the app: DB_PATH is computed at import.
_TEST_DATA_DIR = tempfile.mkdtemp(prefix="auditp1_identity_")
os.environ["SINCOR_DATA_DIR"] = _TEST_DATA_DIR
os.environ.setdefault("FLASK_ENV", "test")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-with-32-char-minimum-ok")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key-32-char-minimum-ok")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "admin-password-32-char-minimum-ok")
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_123456789012345678901234567890")

import pytest  # noqa: E402

from sincor2.mvp_app import (  # noqa: E402
    app,
    get_db,
    limiter,
    _request_jwt_identity,
)

ALICE = "alice@example.com"
BOB = "bob@example.com"
WALLET_ALICE = "0x" + "a1" * 20
WALLET_BOB = "0x" + "b2" * 20


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["SERVER_NAME"] = None
    limiter.reset()
    with app.test_client() as c:
        yield c
    limiter.reset()


def _jwt_for(identity, **claims):
    from flask_jwt_extended import create_access_token

    with app.app_context():
        return create_access_token(identity=identity, additional_claims=claims)


def _auth_headers(identity, **claims):
    return {"Authorization": f"Bearer {_jwt_for(identity, **claims)}"}


def _seed_order(email, order_id="ord_test_1"):
    with app.app_context():
        db = get_db()
        db.execute(
            """INSERT OR IGNORE INTO orders
               (order_id, customer_email, product_name, amount, currency,
                payment_status, delivery_status, delivery_url, order_type, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (order_id, email, "Starter", 297, "USD", "paid", "delivered",
             "https://example.com/dl", "product", "2026-10-01T00:00:00"),
        )
        db.commit()


def _seed_customer(email):
    with app.app_context():
        db = get_db()
        db.execute(
            """INSERT OR IGNORE INTO customer_profiles
               (profile_id, email, first_name, username, created_at)
               VALUES (?,?,?, ?,?)""",
            (f"prof_{email}", email, "Test", email.split("@")[0], "2026-10-01T00:00:00"),
        )
        db.commit()


def _seed_subscription(wallet, email):
    from sincor2 import platform_payments

    platform_payments.init_platform_payments_db()
    platform_payments.activate_subscription(
        wallet=wallet,
        plan_id="pro",
        product_name="Professional",
        token="AXM",
        tx_hash="0x" + "ab" * 32,
        payment_id="pay_test_1",
        email=email,
    )


def _csrf_token(client):
    client.get("/onboarding")
    with client.session_transaction() as sess:
        return sess.get("csrf_token", "")


class _FakeSender:
    """Captures 'sent' emails; OTP code parsed from the HTML body."""

    def __init__(self):
        self.mode = "test"
        self.sent = []

    def send_email(self, to_email, to_name, subject, html_content,
                   text_content=None, metadata=None):
        self.sent.append({"to": to_email, "html": html_content})
        return {"status": "sent", "message_id": "test-1", "provider": "test"}

    def last_code(self):
        m = re.search(r"(\d{6})", self.sent[-1]["html"])
        return m.group(1)


@pytest.fixture
def fake_sender(monkeypatch):
    sender = _FakeSender()
    import sincor2.mvp_app as mvp
    import sincor2.mvp_blueprints.auth as auth_bp

    monkeypatch.setattr(mvp, "email_sender", sender)
    monkeypatch.setattr(auth_bp, "email_sender", sender)
    return sender


def _onboarding_payload(email, csrf, **extra):
    payload = {
        "csrf_token": csrf,
        "email": email,
        "first_name": "Ada",
        "last_name": "Lovelace",
        "company_name": "Analytical Engines Inc",
        "industry": "SaaS / Software",
        "team_size": "2–10",
        "primary_use_case": "Lead Generation & Outreach",
        "consent_data": True,
    }
    payload.update(extra)
    return payload


# ============================================================================
# Finding 1 — identifier-only customer login disabled
# ============================================================================


class TestCustomerLoginDisabled:
    def test_attack_identifier_only_login_refused(self, client):
        """A known customer email alone must NOT grant a session (was 302)."""
        _seed_customer(ALICE)
        r = client.post("/login", data={"identifier": ALICE, "password": ""})
        assert r.status_code == 403
        with client.session_transaction() as sess:
            assert not sess.get("user_email")
        assert "access_token" not in (r.headers.get("Set-Cookie") or "")

    def test_attack_unknown_identifier_refused(self, client):
        r = client.post("/login", data={"identifier": "ghost@example.com", "password": "x"})
        assert r.status_code == 403
        with client.session_transaction() as sess:
            assert not sess.get("user_email")

    def test_attack_username_identifier_refused(self, client):
        """Username-style identifiers are refused too (no password check)."""
        _seed_customer(ALICE)
        r = client.post("/login", data={"identifier": "alice", "password": "anything"})
        assert r.status_code == 403
        with client.session_transaction() as sess:
            assert not sess.get("user_email")

    def test_happy_admin_login_still_works(self, client):
        r = client.post(
            "/login",
            data={"identifier": "admin", "password": "admin-password-32-char-minimum-ok"},
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert "/admin" in r.headers["Location"]
        with client.session_transaction() as sess:
            assert sess.get("is_admin") is True

    def test_happy_admin_login_wrong_password_rejected(self, client):
        r = client.post("/login", data={"identifier": "admin", "password": "wrong"})
        assert r.status_code == 403  # falls through to disabled customer path
        with client.session_transaction() as sess:
            assert not sess.get("is_admin")

    def test_login_page_still_renders(self, client):
        r = client.get("/login")
        assert r.status_code == 200


# ============================================================================
# Finding 2 — /api/orders/<email> requires JWT scoped to own email
# ============================================================================


class TestOrderDisclosure:
    def test_attack_no_auth(self, client):
        _seed_order(ALICE)
        r = client.get(f"/api/orders/{ALICE}")
        assert r.status_code == 401

    def test_attack_garbage_token(self, client):
        _seed_order(ALICE)
        r = client.get(f"/api/orders/{ALICE}",
                       headers={"Authorization": "Bearer not-a-real-token"})
        assert r.status_code == 401

    def test_attack_other_users_jwt(self, client):
        """Bob's JWT must not read Alice's orders (was 200 with full data)."""
        _seed_order(ALICE)
        _seed_order(BOB, "ord_test_2")
        r = client.get(f"/api/orders/{ALICE}", headers=_auth_headers(BOB))
        assert r.status_code == 403
        assert b"ord_test_1" not in r.data

    def test_attack_case_spoofed_param(self, client):
        """'ALICE@EXAMPLE.COM' param vs bob's JWT must still be refused."""
        _seed_order(ALICE)
        r = client.get("/api/orders/ALICE@EXAMPLE.COM", headers=_auth_headers(BOB))
        assert r.status_code == 403

    def test_happy_own_orders(self, client):
        _seed_order(ALICE)
        _seed_order(BOB, "ord_test_2")
        r = client.get(f"/api/orders/{ALICE}", headers=_auth_headers(ALICE))
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is True
        assert data["count"] == 1
        assert data["orders"][0]["order_id"] == "ord_test_1"
        assert all(o["order_id"] != "ord_test_2" for o in data["orders"])

    def test_happy_own_email_case_insensitive(self, client):
        _seed_order(ALICE)
        r = client.get("/api/orders/ALICE@EXAMPLE.COM", headers=_auth_headers(ALICE))
        assert r.status_code == 200

    def test_happy_admin_can_query_any(self, client):
        _seed_order(ALICE)
        r = client.get(f"/api/orders/{ALICE}", headers=_auth_headers("admin", role="admin"))
        assert r.status_code == 200
        assert r.get_json()["count"] >= 1

    def test_happy_cookie_jwt_works(self, client):
        """The httponly access_token cookie set at login is accepted."""
        _seed_order(ALICE)
        token = _jwt_for(ALICE)
        client.set_cookie("access_token", token)
        r = client.get(f"/api/orders/{ALICE}")
        assert r.status_code == 200

    def test_request_jwt_identity_helper(self, client):
        with app.test_request_context(
            "/x", headers={"Authorization": f"Bearer {_jwt_for(ALICE)}"}
        ):
            assert _request_jwt_identity() == ALICE
        with app.test_request_context("/x"):
            assert _request_jwt_identity() is None


# ============================================================================
# Finding 3 — /api/cancel-subscription requires JWT + ownership
# ============================================================================


class TestCancelSubscription:
    def test_attack_no_auth(self, client):
        _seed_subscription(WALLET_ALICE, ALICE)
        r = client.post("/api/cancel-subscription", json={"wallet": WALLET_ALICE})
        assert r.status_code == 401
        from sincor2 import platform_payments

        subs = platform_payments.list_subscriptions(WALLET_ALICE)
        assert any(s["status"] == "active" for s in subs)

    def test_attack_other_users_wallet(self, client):
        """Bob must not cancel Alice's wallet subscription (was 200)."""
        _seed_subscription(WALLET_ALICE, ALICE)
        r = client.post(
            "/api/cancel-subscription",
            json={"wallet": WALLET_ALICE},
            headers=_auth_headers(BOB),
        )
        assert r.status_code == 403
        from sincor2 import platform_payments

        subs = platform_payments.list_subscriptions(WALLET_ALICE)
        assert any(s["status"] == "active" for s in subs)

    def test_attack_other_users_email(self, client):
        r = client.post(
            "/api/cancel-subscription",
            json={"email": ALICE, "reason": "malicious"},
            headers=_auth_headers(BOB),
        )
        assert r.status_code == 403

    def test_attack_email_case_spoof(self, client):
        """'ALICE@EXAMPLE.COM' in body vs bob's JWT must be refused."""
        r = client.post(
            "/api/cancel-subscription",
            json={"email": "ALICE@EXAMPLE.COM"},
            headers=_auth_headers(BOB),
        )
        assert r.status_code == 403

    def test_happy_owner_cancels_own_wallet_sub(self, client):
        _seed_subscription(WALLET_ALICE, ALICE)
        r = client.post(
            "/api/cancel-subscription",
            json={"wallet": WALLET_ALICE},
            headers=_auth_headers(ALICE),
        )
        assert r.status_code == 200
        assert r.get_json()["cancelled"] == 1
        from sincor2 import platform_payments

        subs = platform_payments.list_subscriptions(WALLET_ALICE)
        assert all(s["status"] != "active" for s in subs)

    def test_happy_owner_email_case_insensitive(self, client):
        r = client.post(
            "/api/cancel-subscription",
            json={"email": "ALICE@EXAMPLE.COM", "reason": "done"},
            headers=_auth_headers(ALICE),
        )
        # Fiat disabled + no wallet -> informational path, but owned -> allowed
        assert r.status_code == 200

    def test_happy_admin_cancels_any(self, client):
        _seed_subscription(WALLET_BOB, BOB)
        r = client.post(
            "/api/cancel-subscription",
            json={"wallet": WALLET_BOB},
            headers=_auth_headers("admin", role="admin"),
        )
        assert r.status_code == 200
        assert r.get_json()["cancelled"] == 1

    def test_wallet_with_no_active_sub_is_404(self, client):
        r = client.post(
            "/api/cancel-subscription",
            json={"wallet": "0x" + "cc" * 20},
            headers=_auth_headers(ALICE),
        )
        assert r.status_code == 404

    def test_missing_email_and_wallet_is_400(self, client):
        r = client.post(
            "/api/cancel-subscription",
            json={},
            headers=_auth_headers(ALICE),
        )
        assert r.status_code == 400


# ============================================================================
# Finding 4 — OAuth requires provider-verified email
# ============================================================================


class _FakeOAuthResp:
    def __init__(self, payload, ok=True, status_code=200):
        self._payload = payload
        self.ok = ok
        self.status_code = status_code

    def json(self):
        return self._payload


class _FakeGoogle:
    def __init__(self, userinfo):
        self._userinfo = userinfo

    def authorize_access_token(self):
        return {"userinfo": self._userinfo}


class _FakeGithub:
    def __init__(self, user, emails):
        self._user = user
        self._emails = emails

    def authorize_access_token(self):
        return {"access_token": "fake"}

    def get(self, path):
        if path == "user":
            return _FakeOAuthResp(self._user)
        if path == "user/emails":
            return _FakeOAuthResp(self._emails)
        return _FakeOAuthResp({}, ok=False, status_code=404)


class _FakeOAuth:
    def __init__(self, google=None, github=None):
        if google is not None:
            self.google = google
        if github is not None:
            self.github = github


def _patch_oauth(monkeypatch, fake):
    import sincor2.mvp_app as mvp
    import sincor2.mvp_blueprints.auth as auth_bp

    monkeypatch.setattr(mvp, "oauth", fake)
    monkeypatch.setattr(auth_bp, "oauth", fake)


class TestOAuthVerifiedEmail:
    def test_google_unverified_email_refused(self, client, monkeypatch):
        """Google claim without email_verified=true must not link (was login)."""
        _patch_oauth(
            monkeypatch,
            _FakeOAuth(google=_FakeGoogle({"email": "mallory@example.com",
                                          "email_verified": False})),
        )
        r = client.get("/auth/google/callback", follow_redirects=False)
        assert r.status_code == 302
        assert "email_unverified" in r.headers["Location"]
        with client.session_transaction() as sess:
            assert not sess.get("user_email")

    def test_google_verified_email_links(self, client, monkeypatch):
        _patch_oauth(
            monkeypatch,
            _FakeOAuth(google=_FakeGoogle({"email": ALICE, "email_verified": True,
                                          "name": "Alice"})),
        )
        r = client.get("/auth/google/callback", follow_redirects=False)
        assert r.status_code == 302
        with client.session_transaction() as sess:
            assert sess.get("user_email") == ALICE

    def test_google_missing_verified_flag_refused(self, client, monkeypatch):
        """Absent email_verified claim = not verified (fail closed)."""
        _patch_oauth(
            monkeypatch,
            _FakeOAuth(google=_FakeGoogle({"email": "mallory@example.com"})),
        )
        r = client.get("/auth/google/callback", follow_redirects=False)
        assert r.status_code == 302
        assert "email_unverified" in r.headers["Location"]

    def test_github_unverified_primary_refused(self, client, monkeypatch):
        """GitHub primary email unverified must not link (was login)."""
        _patch_oauth(
            monkeypatch,
            _FakeOAuth(github=_FakeGithub(
                {"login": "mallory", "name": "Mallory"},
                [{"email": "mallory@evil.example", "primary": True, "verified": False}],
            )),
        )
        r = client.get("/auth/github/callback", follow_redirects=False)
        assert r.status_code == 302
        assert "email_unverified" in r.headers["Location"]
        with client.session_transaction() as sess:
            assert not sess.get("user_email")

    def test_github_no_verified_email_refused(self, client, monkeypatch):
        _patch_oauth(
            monkeypatch,
            _FakeOAuth(github=_FakeGithub(
                {"login": "mallory"},
                [{"email": "a@x.example", "primary": True, "verified": False},
                 {"email": "b@x.example", "primary": False, "verified": False}],
            )),
        )
        r = client.get("/auth/github/callback", follow_redirects=False)
        assert "email_unverified" in r.headers["Location"]

    def test_github_verified_primary_links(self, client, monkeypatch):
        _patch_oauth(
            monkeypatch,
            _FakeOAuth(github=_FakeGithub(
                {"login": "alicegh", "name": "Alice"},
                [{"email": ALICE, "primary": True, "verified": True}],
            )),
        )
        r = client.get("/auth/github/callback", follow_redirects=False)
        assert r.status_code == 302
        with client.session_transaction() as sess:
            assert sess.get("user_email") == ALICE

    def test_github_public_email_not_in_verified_list_refused(self, client, monkeypatch):
        """/user public email must be cross-checked against verified list."""
        _patch_oauth(
            monkeypatch,
            _FakeOAuth(github=_FakeGithub(
                {"login": "mallory", "email": "mallory@evil.example"},
                [{"email": "other@x.example", "primary": True, "verified": True}],
            )),
        )
        r = client.get("/auth/github/callback", follow_redirects=False)
        # Falls back to the verified list -> links other@x.example, never the
        # unverified public claim.
        assert r.status_code == 302
        assert "email_unverified" not in r.headers["Location"]
        with client.session_transaction() as sess:
            assert sess.get("user_email") == "other@x.example"


# ============================================================================
# Finding 5 — onboarding writes require email-OTP proof
# ============================================================================


class TestOnboardingProof:
    def test_attack_no_proof_refused(self, client):
        """Anonymous upsert without OTP must not write (was 200)."""
        email = "noproof@example.com"
        csrf = _csrf_token(client)
        r = client.post("/api/onboarding", json=_onboarding_payload(email, csrf))
        assert r.status_code == 403
        with app.app_context():
            db = get_db()
            row = db.execute(
                "SELECT id FROM customer_profiles WHERE email=?", (email,)
            ).fetchone()
            assert row is None

    def test_attack_missing_csrf_still_refused(self, client):
        r = client.post("/api/onboarding", json=_onboarding_payload(ALICE, "bad"))
        assert r.status_code == 403

    def test_attack_wrong_otp_refused(self, client, fake_sender):
        csrf = _csrf_token(client)
        r = client.post("/api/onboarding/challenge", json={"email": ALICE})
        challenge_id = r.get_json()["challenge_id"]
        r = client.post(
            "/api/onboarding",
            json=_onboarding_payload(ALICE, csrf, challenge_id=challenge_id, otp="000000"),
        )
        assert r.status_code == 403

    def test_attack_otp_email_mismatch_refused(self, client, fake_sender):
        """Code issued for Alice must not authorize Bob's profile write."""
        csrf = _csrf_token(client)
        r = client.post("/api/onboarding/challenge", json={"email": ALICE})
        challenge_id = r.get_json()["challenge_id"]
        code = fake_sender.last_code()
        r = client.post(
            "/api/onboarding",
            json=_onboarding_payload(BOB, csrf, challenge_id=challenge_id, otp=code),
        )
        assert r.status_code == 403
        with app.app_context():
            db = get_db()
            assert db.execute(
                "SELECT id FROM customer_profiles WHERE email=?", (BOB,)
            ).fetchone() is None

    def test_attack_otp_replay_refused(self, client, fake_sender):
        """A consumed challenge cannot be reused."""
        csrf = _csrf_token(client)
        r = client.post("/api/onboarding/challenge", json={"email": ALICE})
        challenge_id = r.get_json()["challenge_id"]
        code = fake_sender.last_code()
        payload = _onboarding_payload(ALICE, csrf, challenge_id=challenge_id, otp=code)
        assert client.post("/api/onboarding", json=payload).status_code == 200
        # Replay with a fresh CSRF token (the first consumed it)
        csrf2 = _csrf_token(client)
        payload2 = _onboarding_payload(ALICE, csrf2, challenge_id=challenge_id, otp=code)
        assert client.post("/api/onboarding", json=payload2).status_code == 403

    def test_attack_expired_otp_refused(self, client, fake_sender):
        csrf = _csrf_token(client)
        r = client.post("/api/onboarding/challenge", json={"email": ALICE})
        challenge_id = r.get_json()["challenge_id"]
        code = fake_sender.last_code()
        with app.app_context():
            db = get_db()
            db.execute(
                "UPDATE email_verification_challenges SET expires_at=? WHERE challenge_id=?",
                ("2000-01-01T00:00:00+00:00", challenge_id),
            )
            db.commit()
        r = client.post(
            "/api/onboarding",
            json=_onboarding_payload(ALICE, csrf, challenge_id=challenge_id, otp=code),
        )
        assert r.status_code == 403

    def test_attack_unknown_challenge_refused(self, client):
        csrf = _csrf_token(client)
        r = client.post(
            "/api/onboarding",
            json=_onboarding_payload(ALICE, csrf, challenge_id="otp_guessed", otp="123456"),
        )
        assert r.status_code == 403

    def test_challenge_fails_closed_without_email_delivery(self, client, monkeypatch):
        """Stub-mode email sender -> 503, no challenge row usable."""
        import sincor2.mvp_app as mvp
        import sincor2.mvp_blueprints.auth as auth_bp

        class _Stub:
            mode = "stub"

            def send_email(self, *a, **k):
                return {"status": "stub"}

        monkeypatch.setattr(mvp, "email_sender", _Stub())
        monkeypatch.setattr(auth_bp, "email_sender", _Stub())
        r = client.post("/api/onboarding/challenge", json={"email": ALICE})
        assert r.status_code == 503

    def test_happy_onboarding_with_otp(self, client, fake_sender):
        csrf = _csrf_token(client)
        r = client.post("/api/onboarding/challenge", json={"email": ALICE})
        assert r.status_code == 200
        challenge_id = r.get_json()["challenge_id"]
        code = fake_sender.last_code()
        assert re.fullmatch(r"\d{6}", code)

        r = client.post(
            "/api/onboarding",
            json=_onboarding_payload(ALICE, csrf, challenge_id=challenge_id, otp=code),
        )
        assert r.status_code == 200
        with app.app_context():
            db = get_db()
            row = db.execute(
                """SELECT profile_id, consent_given, consent_timestamp,
                          consent_proof_ref, consent_actor, consent_purpose
                   FROM customer_profiles WHERE email=?""",
                (ALICE,),
            ).fetchone()
            assert row is not None
            assert row["consent_given"] == 1
            assert row["consent_timestamp"]
            assert row["consent_proof_ref"] == challenge_id
            assert row["consent_actor"] == ALICE
            assert row["consent_purpose"] == "onboarding"

    def test_happy_admin_override_recorded(self, client):
        """Admin console may write; proof is recorded as admin-console."""
        csrf = _csrf_token(client)
        with client.session_transaction() as sess:
            sess["is_admin"] = True
            sess["admin_username"] = "admin"
        r = client.post("/api/onboarding", json=_onboarding_payload(BOB, csrf))
        assert r.status_code == 200
        with app.app_context():
            db = get_db()
            row = db.execute(
                "SELECT consent_proof_ref, consent_actor FROM customer_profiles WHERE email=?",
                (BOB,),
            ).fetchone()
            assert row["consent_proof_ref"] == "admin-console"
            assert row["consent_actor"].startswith("admin:")

    def test_happy_challenge_rate_limited(self, client, fake_sender):
        for _ in range(5):
            assert client.post("/api/onboarding/challenge", json={"email": ALICE}).status_code == 200
        r = client.post("/api/onboarding/challenge", json={"email": ALICE})
        assert r.status_code == 429
