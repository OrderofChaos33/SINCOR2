
import pytest

from sincor2.settings import Settings, SettingsError


def test_settings_non_prod_allows_dev_defaults(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    settings = Settings.from_env()
    assert settings.environment == "development"


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
