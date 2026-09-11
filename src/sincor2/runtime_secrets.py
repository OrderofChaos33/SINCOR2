"""Resolve Flask/JWT secrets without ever committing a shared fallback string.

Production / Railway: missing key is a hard failure.
Everywhere else: generate an in-memory random key for this process only.
"""

from __future__ import annotations

import logging
import os
import secrets

logger = logging.getLogger("sincor2.runtime_secrets")

_PROD_MARKERS = {"production", "prod"}


def is_production_runtime() -> bool:
    if os.environ.get("RAILWAY_ENVIRONMENT"):
        return True
    env = (
        os.environ.get("FLASK_ENV")
        or os.environ.get("ENVIRONMENT")
        or ""
    ).strip().lower()
    return env in _PROD_MARKERS


def _first_env(*names: str) -> str:
    for name in names:
        value = (os.environ.get(name) or "").strip().strip('"').strip("'")
        if value:
            return value
    return ""


def resolve_secret(*, names: tuple[str, ...], purpose: str) -> str:
    """Return a process secret. Never a repo-hardcoded string."""
    found = _first_env(*names)
    if found:
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
    found = _first_env("JWT_SECRET_KEY", "JWT_SECRET")
    if found:
        return found
    # Same process may already have a Flask secret — reuse it, do not invent a second source.
    flask = _first_env("SECRET_KEY", "FLASK_SECRET_KEY")
    if flask:
        return flask
    return resolve_secret(
        names=("JWT_SECRET_KEY", "JWT_SECRET"),
        purpose="JWT_SECRET_KEY",
    )
