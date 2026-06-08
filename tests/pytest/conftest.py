
import os

import pytest


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
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-123456")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-secret-key-123456")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-password")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")


@pytest.fixture(autouse=True)
def isolated_waitlist_db(tmp_path, monkeypatch):
    """Give every test its own fresh waitlist SQLite database.

    Patches both the module-level symbol (used by direct imports) and the
    name bound in app.py (used by the Flask app.extensions dict at create
    time) so that all code paths see the same isolated instance.
    """
    from sincor2 import waitlist_system
    from sincor2 import app as app_module

    db_path = str(tmp_path / "waitlist_test.db")
    fresh_manager = waitlist_system.WaitlistManager(db_path=db_path)
    monkeypatch.setattr(waitlist_system, "waitlist_manager", fresh_manager)
    monkeypatch.setattr(app_module, "waitlist_manager", fresh_manager)


@pytest.fixture
def app(monkeypatch, tmp_path):
    from sincor2 import app as app_module
    from sincor2 import waitlist_system

    # Patch StripeCheckout before creating the app so the app doesn't try to
    # contact Stripe during startup.
    monkeypatch.setattr(app_module, "StripeCheckout", lambda api_key=None: MockStripeCheckout())

    # Ensure each test gets a fresh, isolated waitlist DB regardless of the
    # autouse fixture ordering (app is set up before isolated_waitlist_db).
    db_path = str(tmp_path / "app_waitlist.db")
    fresh_manager = waitlist_system.WaitlistManager(db_path=db_path)
    monkeypatch.setattr(waitlist_system, "waitlist_manager", fresh_manager)
    monkeypatch.setattr(app_module, "waitlist_manager", fresh_manager)

    flask_app = app_module.create_app()
    flask_app.config.update(TESTING=True)
    # Override the extension with the fresh manager in case create_app() bound
    # the original module-level instance before our monkeypatch took effect.
    flask_app.extensions["waitlist_manager"] = fresh_manager
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
