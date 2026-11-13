"""P2 audit Batch C — C3: cookie Secure tied to production-HTTPS posture.

Secure must be True whenever the app is serving HTTPS in production,
regardless of provider — and must NOT be assumed from a bare
provider-specific environment label.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

MOD = "sincor2.cookie_security"

_ENV_KEYS = [
    "SINCOR_COOKIE_SECURE", "TRUST_PROXY",
    "FLASK_ENV", "ENVIRONMENT",
    "RAILWAY_ENVIRONMENT", "RENDER", "FLY_APP_NAME", "HEROKU_APP_NAME",
    "PUBLIC_BASE_URL", "SITE_URL", "OAUTH_REDIRECT_BASE_URL",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield


def _reload():
    import importlib

    sys.modules.pop(MOD, None)
    return importlib.import_module(MOD)


def test_production_https_via_trust_proxy():
    os.environ["FLASK_ENV"] = "production"
    os.environ["TRUST_PROXY"] = "1"
    cs = _reload()
    assert cs.is_production_https() is True


def test_production_https_via_base_url():
    os.environ["FLASK_ENV"] = "production"
    os.environ["PUBLIC_BASE_URL"] = "https://getsincor.com"
    cs = _reload()
    assert cs.is_production_https() is True


def test_production_https_any_provider_label():
    # Same posture on a non-Railway host: provider-agnostic.
    os.environ["FLY_APP_NAME"] = "sincor-prod"
    os.environ["SITE_URL"] = "https://app.example.com"
    cs = _reload()
    assert cs.is_production_https() is True


def test_explicit_override_wins():
    os.environ["SINCOR_COOKIE_SECURE"] = "1"
    cs = _reload()
    assert cs.is_production_https() is True
    os.environ["SINCOR_COOKIE_SECURE"] = "0"
    os.environ["FLASK_ENV"] = "production"
    os.environ["TRUST_PROXY"] = "1"
    cs = _reload()
    assert cs.is_production_https() is False


def test_local_dev_http_no_secure():
    os.environ["FLASK_ENV"] = "development"
    cs = _reload()
    assert cs.is_production_https() is False
    kwargs = cs.secure_cookie_kwargs()
    assert kwargs["secure"] is False
    # Existing attributes unchanged.
    assert kwargs["httponly"] is True
    assert kwargs["samesite"] == "Lax"


def test_provider_label_only_no_secure():
    # RAILWAY_ENVIRONMENT alone, no HTTPS signal -> Secure NOT assumed.
    os.environ["RAILWAY_ENVIRONMENT"] = "staging"
    cs = _reload()
    assert cs.is_production_https() is False


def test_other_provider_label_only_no_secure():
    os.environ["RENDER"] = "true"
    cs = _reload()
    assert cs.is_production_https() is False


def test_http_base_url_no_secure():
    os.environ["FLASK_ENV"] = "production"
    os.environ["PUBLIC_BASE_URL"] = "http://localhost:5000"
    cs = _reload()
    assert cs.is_production_https() is False


def test_secure_cookie_kwargs_shape():
    os.environ["FLASK_ENV"] = "production"
    os.environ["TRUST_PROXY"] = "true"
    cs = _reload()
    kwargs = cs.secure_cookie_kwargs()
    assert kwargs == {"httponly": True, "secure": True, "samesite": "Lax"}


def _mvp_session_cookie_secure(env: dict) -> str:
    """Import mvp_app in a fresh interpreter and report SESSION_COOKIE_SECURE."""
    base = {
        "PYTHONPATH": "src:.",
        "FLASK_ENV": "test",
        "SECRET_KEY": "0123456789abcdef0123456789abcdef",
        "JWT_SECRET_KEY": "0123456789abcdef0123456789abcdef",
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "testpass123",
        "STRIPE_SECRET_KEY": "stripe-test-placeholder-not-a-key",
        "PATH": os.environ["PATH"],
    }
    base.update(env)
    code = (
        "import os;"
        "from sincor2 import mvp_app;"
        "print(mvp_app.app.config['SESSION_COOKIE_SECURE'])"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        env=base, capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return proc.stdout.strip().splitlines()[-1]


def test_mvp_app_session_cookie_secure_in_production_https():
    out = _mvp_session_cookie_secure(
        {"FLASK_ENV": "production", "TRUST_PROXY": "1",
         "RAILWAY_ENVIRONMENT": ""}
    )
    assert out == "True"


def test_mvp_app_session_cookie_not_secure_dev():
    out = _mvp_session_cookie_secure(
        {"FLASK_ENV": "development", "TRUST_PROXY": "", "RAILWAY_ENVIRONMENT": ""}
    )
    assert out == "False"


def test_mvp_app_session_cookie_not_secure_label_only():
    out = _mvp_session_cookie_secure(
        {"FLASK_ENV": "", "TRUST_PROXY": "", "RAILWAY_ENVIRONMENT": "staging"}
    )
    assert out == "False"
