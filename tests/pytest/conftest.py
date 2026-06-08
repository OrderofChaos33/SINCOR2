
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
    """Give every test its own fresh waitlist SQLite database."""
    from sincor2 import waitlist_system

    db_path = str(tmp_path / "waitlist_test.db")
    fresh_manager = waitlist_system.WaitlistManager(db_path=db_path)
    monkeypatch.setattr(waitlist_system, "waitlist_manager", fresh_manager)


@pytest.fixture
def app(monkeypatch):
    from sincor2 import app as app_module

    monkeypatch.setattr(app_module, "StripeCheckout", lambda api_key=None: MockStripeCheckout())
    flask_app = app_module.create_app()
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
