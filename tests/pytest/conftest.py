import os

# Set test env before any sincor2 modules import (app.py calls create_app at import time).
os.environ.setdefault("FLASK_ENV", "test")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SINCOR_TASK_QUEUE", "eager")
os.environ.setdefault("SECRET_KEY", "test-secret-key-with-32-char-minimum-ok")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key-32-char-minimum-ok")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "admin-password-32-char-minimum-ok")
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_123456789012345678901234567890")
# Operator heartbeat credential (G2.3): tests authenticate heartbeats with
# this token via the X-Sincor-Heartbeat header unless a test overrides it.
os.environ.setdefault("AGENT_HEARTBEAT_TOKEN", "test-heartbeat-token")

import pytest  # noqa: E402



class MockStripeCheckout:
    def create_checkout_session(self, **kwargs):
        return {
            "success": True,
            "session_id": "cs_test_123",
            "checkout_url": "https://example.com/checkout/cs_test_123",
            **kwargs,
        }


@pytest.fixture(autouse=True)
def env_defaults(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "test")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("SINCOR_TASK_QUEUE", "eager")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-with-32-char-minimum-ok")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-secret-key-32-char-minimum-ok")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-password-32-char-minimum-ok")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123456789012345678901234567890")
    monkeypatch.setenv("AGENT_HEARTBEAT_TOKEN", "test-heartbeat-token")


def hb_headers() -> dict:
    """Operator heartbeat auth header for tests (G2.3)."""
    return {"X-Sincor-Heartbeat": os.environ.get("AGENT_HEARTBEAT_TOKEN", "")}


def signed_task_auth(skill: str, key: str = "0x" + "dd" * 32) -> dict:
    """Fresh EIP-191 caller-signature fields for a marketplace task create.

    The anti-gaming guards reject bids on anonymous (unsigned) bounty
    tasks, so HTTP tests that post tasks and then bid must bind a poster
    server-side. Merge into the POST /v1/a2a/tasks JSON body; the bound
    poster_id is the signer's wallet (lowercased), never the bidder.
    """
    import time
    import uuid

    from eth_account import Account
    from eth_account.messages import encode_defunct

    from sincor2.a2a_integration import _auth_market_create_message

    skill = str(skill).strip().lower()
    acct = Account.from_key(key)
    ts = int(time.time())
    nonce = uuid.uuid4().hex
    sig = "0x" + acct.sign_message(
        encode_defunct(text=_auth_market_create_message(skill, ts, nonce))
    ).signature.hex()
    return {"authSignature": sig, "authTimestamp": ts, "authNonce": nonce}


@pytest.fixture(autouse=True)
def isolated_waitlist_db(tmp_path, monkeypatch):
    """Give every test its own fresh waitlist SQLite database."""
    from sincor2 import waitlist_system

    db_path = str(tmp_path / "waitlist_test.db")
    fresh_manager = waitlist_system.WaitlistManager(db_path=db_path)
    monkeypatch.setattr(waitlist_system, "waitlist_manager", fresh_manager)


@pytest.fixture(autouse=True)
def _reset_a2a_rate_limits():
    """Isolate the in-memory A2A rate limiter between tests.

    The enforcer (sincor2.a2a_rate_limits) keys per-IP tiers on the test
    client's address, so without a reset one test's traffic would bleed
    into the next. Harmless for tests that never touch A2A routes.
    """
    from sincor2.a2a_rate_limits import reset_a2a_limits
    reset_a2a_limits()
    yield
    reset_a2a_limits()


@pytest.fixture(autouse=True)
def _isolated_kya_registry(tmp_path, monkeypatch):
    """Hermetic KYA registry per test.

    The KYA registry persists to the real store and never resets between
    tests; the anti-sybil gates (tombstone + wallet-identity cap) make
    that accumulation load-bearing. This fixture gives every test a
    fresh registry so tests cannot pollute each other through shared
    hardcoded wallets.
    """
    from sincor2 import kya_registry as kya

    monkeypatch.setenv("KYA_STORE_PATH", str(tmp_path / "kya_test.json"))
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "kya_data"))
    kya.reset()
    yield
    kya.reset()


@pytest.fixture(autouse=True)
def _isolated_antigaming_store(tmp_path):
    """Hermetic anti-gaming store per test.

    The guard store persists to the real data dir and never resets between
    tests; the rotation caps (consecutive AND the per-pair wins-per-window
    cap) make that accumulation load-bearing — repeated runs with the same
    poster wallet would trip caps and flake unrelated market tests. Every
    test gets a fresh store; production restart-persistence is unaffected.
    """
    from sincor2 import axm_antigaming as ag

    ag.reset_antigaming_store(path=str(tmp_path / "antigaming_test.json"))
    yield
    ag.reset_antigaming_store(path=str(tmp_path / "antigaming_test.json"))


@pytest.fixture
def app(monkeypatch, isolated_waitlist_db):
    from sincor2 import app as app_module
    from sincor2 import waitlist_system

    monkeypatch.setattr(app_module, "StripeCheckout", lambda api_key=None: MockStripeCheckout())
    flask_app = app_module.create_app()
    flask_app.extensions["waitlist_manager"] = waitlist_system.waitlist_manager
    flask_app.config.update(TESTING=True)
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_headers(client):
    response = client.post(
        "/api/auth/login",
        json={"username": os.environ["ADMIN_USERNAME"], "password": os.environ["ADMIN_PASSWORD"]},
    )
    payload = response.get_json()
    return {"Authorization": "Bearer " + payload["access_token"]}
