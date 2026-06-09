
import pytest

from sincor2.settings import Settings, SettingsError


def test_settings_non_prod_allows_dev_defaults(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    settings = Settings.from_env()
    assert settings.environment == "development"
    assert len(settings.secret_key) >= 32
    assert len(settings.jwt_secret_key) >= 32
    assert settings.admin_password != "changeme123"


def test_settings_prod_requires_secure_values(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "short")
    monkeypatch.setenv("JWT_SECRET_KEY", "short")
    monkeypatch.setenv("ADMIN_PASSWORD", "changeme123")
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("PAYPAL_REST_API_ID", raising=False)
    monkeypatch.delenv("PAYPAL_REST_API_SECRET", raising=False)
    with pytest.raises(SettingsError):
        Settings.from_env()


def test_settings_prod_allows_missing_payment_providers(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "this-is-a-secure-secret-key")
    monkeypatch.setenv("JWT_SECRET_KEY", "this-is-a-secure-jwt-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "secure-admin-password")
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("PAYPAL_REST_API_ID", raising=False)
    monkeypatch.delenv("PAYPAL_REST_API_SECRET", raising=False)

    settings = Settings.from_env()

    assert settings.is_production
    assert settings.stripe_secret_key is None
