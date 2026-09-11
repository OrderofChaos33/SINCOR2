"""Resolve Flask/JWT secrets without ever committing a shared fallback string.

Production / Railway: missing, weak, or known-dummy key is a hard failure.
Everywhere else: generate an in-memory random key for this process only.
"""

from __future__ import annotations

import logging
import os
import secrets

logger = logging.getLogger("sincor2.runtime_secrets")

_PROD_MARKERS = {"production", "prod"}
_MIN_PRODUCTION_LENGTH = 16

# Strings that have appeared in this repo as constructor defaults or examples.
# Treated as unset. Never accepted as a live secret.
BANNED_SECRETS = frozenset(
    {
        "chroma-demo-secret-change-me",
        "development-key-change-in-production",
        "sincor-secret-key-change-in-production",
        "dev-secret-key-CHANGE-IN-PRODUCTION-min-32-chars",
        "dev-secret-key-change-in-production",
        "dev-jwt-secret-key-change-in-production",
        "your-super-secret-key-change-in-production",
        "your-jwt-secret-key-change-in-production",
        "your-super-secret-key-min-32-chars",
        "your-jwt-secret-key-min-32-chars",
        "changeme",
        "changeme123",
        "secret",
        "password",
    }
)


def is_production_runtime() -> bool:
    if os.environ.get("RAILWAY_ENVIRONMENT"):
        return True
    env = (
        os.environ.get("FLASK_ENV")
        or os.environ.get("ENVIRONMENT")
        or ""
    ).strip().lower()
    return env in _PROD_MARKERS


def is_banned_secret(value: str | None) -> bool:
    if not value:
        return True
    lowered = value.strip().strip('"').strip("'")
    if not lowered:
        return True
    if lowered in BANNED_SECRETS:
        return True
    if lowered.lower() in {item.lower() for item in BANNED_SECRETS}:
        return True
    if "change-me" in lowered.lower() or "change_in_production" in lowered.lower():
        return True
    if "change-in-production" in lowered.lower():
        return True
    return False


def _clean(value: str | None) -> str:
    return (value or "").strip().strip('"').strip("'")


def _first_usable_env(*names: str) -> str:
    for name in names:
        value = _clean(os.environ.get(name))
        if value and not is_banned_secret(value):
            return value
        if value and is_banned_secret(value):
            logger.error("%s is set to a banned placeholder; treating as unset", name)
    return ""


def resolve_secret(*, names: tuple[str, ...], purpose: str) -> str:
    """Return a process secret. Never a repo-hardcoded string."""
    found = _first_usable_env(*names)
    if found:
        if is_production_runtime() and len(found) < _MIN_PRODUCTION_LENGTH:
            raise RuntimeError(
                f"{purpose} is too short in production "
                f"(minimum {_MIN_PRODUCTION_LENGTH} characters)."
            )
        return found
    if is_production_runtime():
        raise RuntimeError(
            f"{purpose} must be set in production "
            f"(checked {', '.join(names)}). Refusing to boot with a baked-in key."
        )
    generated = secrets.token_hex(32)
    logger.warning(
        "%s unset outside production; using an in-memory random key for this process only",
        names[0],
    )
    return generated


def resolve_flask_secret() -> str:
    return resolve_secret(
        names=("SECRET_KEY", "FLASK_SECRET_KEY"),
        purpose="SECRET_KEY",
    )


def resolve_jwt_secret() -> str:
    found = _first_usable_env("JWT_SECRET_KEY", "JWT_SECRET")
    if found:
        if is_production_runtime() and len(found) < _MIN_PRODUCTION_LENGTH:
            raise RuntimeError(
                "JWT_SECRET_KEY is too short in production "
                f"(minimum {_MIN_PRODUCTION_LENGTH} characters)."
            )
        return found
    # Same process may already have a Flask secret — reuse it, do not invent a second source.
    flask = _first_usable_env("SECRET_KEY", "FLASK_SECRET_KEY")
    if flask:
        if is_production_runtime() and len(flask) < _MIN_PRODUCTION_LENGTH:
            raise RuntimeError(
                "JWT_SECRET_KEY missing and SECRET_KEY is too short to reuse in production."
            )
        return flask
    return resolve_secret(
        names=("JWT_SECRET_KEY", "JWT_SECRET"),
        purpose="JWT_SECRET_KEY",
    )
