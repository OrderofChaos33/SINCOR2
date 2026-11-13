"""Canonical cookie-security posture (C3, P2 audit).

The ``Secure`` cookie attribute must follow the *effective* deployment
security posture — "is this app serving HTTPS in production?" — not a
single provider-specific environment label.

Rules
-----
``is_production_https()`` is True when ANY of these hold:

1. Explicit operator override: ``SINCOR_COOKIE_SECURE`` is truthy
   (``1``/``true``/``yes``/``on``). A falsy value (``0``/``false``/...)
   forces False and wins over everything below.
2. TLS terminated at the edge: ``TRUST_PROXY`` is truthy. Flask sits behind
   a proxy that speaks HTTPS to clients (the app already enables ProxyFix
   and ``PREFERRED_URL_SCHEME=https`` on this signal) — provider-agnostic.
3. Production deployment AND a configured ``https://`` public base URL
   (``PUBLIC_BASE_URL`` / ``SITE_URL`` / ``OAUTH_REDIRECT_BASE_URL`` /
   ``APP_URL`` / ``BASE_URL``).

A bare provider label (e.g. ``RAILWAY_ENVIRONMENT`` alone, ``RENDER`` alone)
with no HTTPS signal does NOT imply ``Secure`` — that is exactly the
false-assumption the audit flagged. Operators get ``SINCOR_COOKIE_SECURE``
as the explicit escape hatch.

``is_production()`` is provider-agnostic: ``FLASK_ENV``/``ENVIRONMENT`` set
to ``production``/``prod``, or any known hosting-platform deployment label.
"""
from __future__ import annotations

import os
from typing import Dict

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})

# Hosting-platform deployment labels. Presence of any one means "deployed",
# on any provider — the Secure decision still needs a separate HTTPS signal.
_DEPLOYMENT_LABELS = (
    "RAILWAY_ENVIRONMENT",
    "RENDER", "RENDER_SERVICE_NAME", "RENDER_SERVICE_ID",
    "FLY_APP_NAME",
    "HEROKU_APP_NAME", "DYNO",
    "VERCEL", "VERCEL_ENV",
    "K_SERVICE",  # Cloud Run
    "AWS_EXECUTION_ENV", "ECS_CONTAINER_METADATA_URI_V4", "ECS_CONTAINER_METADATA_URI",
    "WEBSITE_SITE_NAME", "AZURE_FUNCTIONS_ENVIRONMENT",  # Azure
)

_BASE_URL_KEYS = (
    "PUBLIC_BASE_URL", "SITE_URL", "OAUTH_REDIRECT_BASE_URL", "APP_URL", "BASE_URL",
)


def _env_first(*keys: str, default: str = "") -> str:
    for key in keys:
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    return default


def is_production() -> bool:
    """Provider-agnostic 'running in production' determination."""
    env = (os.environ.get("FLASK_ENV") or os.environ.get("ENVIRONMENT") or "").strip().lower()
    if env in ("production", "prod"):
        return True
    for key in _DEPLOYMENT_LABELS:
        if os.environ.get(key):
            return True
    return False


def is_production_https() -> bool:
    """Canonical 'serving HTTPS in production' determination.

    See module docstring for the precedence rules.
    """
    override = (os.environ.get("SINCOR_COOKIE_SECURE") or "").strip().lower()
    if override in _TRUE_VALUES:
        return True
    if override in _FALSE_VALUES:
        return False
    if (os.environ.get("TRUST_PROXY") or "").strip().lower() in _TRUE_VALUES:
        return True
    if is_production():
        base = _env_first(*_BASE_URL_KEYS)
        if base.lower().startswith("https://"):
            return True
    return False


def secure_cookie_kwargs() -> Dict[str, object]:
    """Consistent attributes for auth cookies.

    HttpOnly + SameSite=Lax are unchanged from existing behavior; Secure
    follows the canonical production-HTTPS determination.
    """
    return {"httponly": True, "secure": is_production_https(), "samesite": "Lax"}
