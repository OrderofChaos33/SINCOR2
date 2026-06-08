
from sincor2.settings import Settings


def test_settings_non_prod_allows_dev_defaults(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    settings = Settings.from_env()
    assert settings.environment == "development"


def test_settings_prod_auto_recovers_weak_values(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "short")
    monkeypatch.setenv("JWT_SECRET_KEY", "short")
    monkeypatch.setenv("ADMIN_PASSWORD", "changeme123")
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("PAYPAL_REST_API_ID", raising=False)
    monkeypatch.delenv("PAYPAL_REST_API_SECRET", raising=False)

    settings = Settings.from_env()

    assert settings.is_production
    assert settings.secret_key != "short"
    assert len(settings.secret_key) >= 16
    assert settings.jwt_secret_key != "short"
    assert len(settings.jwt_secret_key) >= 16
    assert settings.admin_password != "changeme123"


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


def test_settings_prod_auto_recovers_missing_security_values(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)

    settings = Settings.from_env()

    assert settings.is_production
    assert len(settings.secret_key) >= 16
    assert len(settings.jwt_secret_key) >= 16
    assert settings.admin_password
