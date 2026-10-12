"""Shared credential-verifier throttling (audit P2-A3 remediation).

Every password-check path (legacy JSON login, mvp JSON login, mvp form
login) gets the SAME two layers, both built on the existing
flask-limiter infrastructure — no parallel system:

1. Rate limiting — stacked flask-limiter rules:
     * per client IP: "5 per minute;20 per hour;50 per day"
       (matches the canonical AUTH_LIMITS in sincor2.rate_limiter);
     * per account: "30 per 10 minutes;100 per day", keyed on
       sha256(identifier). This contains distributed guessing against one
       account across many IPs without letting one IP's abuse lock out
       other users.
2. Progressive backoff — a consecutive-failure counter kept in the SAME
   storage backend the limiter uses (SINCOR_LIMITER_STORAGE_URI,
   memory:// default), so it is shared across gunicorn workers. Delay
   before a failed response = min(2 ** (failures - 1), cap) seconds;
   the cap comes from SINCOR_LOGIN_BACKOFF_MAX_SECONDS (default 8).
   A successful login clears the counter.

Usage at each credential verifier::

    from sincor2.login_throttle import (
        credential_rate_limits, note_login_failure, note_login_success,
    )

    @app.route("/api/auth/login", methods=["POST"])
    @credential_rate_limits(limiter)          # stacked limits
    def login():
        ...
        if not _credentials_match(user, pw):
            note_login_failure(user)         # backoff, then caller 401s
            return jsonify({"error": "Invalid credentials"}), 401
        note_login_success(user)
        ...
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Callable, Optional

from flask import request

logger = logging.getLogger("sincor.login_throttle")

#: Per-IP credential limits — identical to AUTH_LIMITS in sincor2.rate_limiter.
IP_CREDENTIAL_LIMITS = "5 per minute;20 per hour;50 per day"
#: Per-account credential limits — bounds distributed guessing against one
#: identifier regardless of source IP.
ACCOUNT_CREDENTIAL_LIMITS = "30 per 10 minutes;100 per day"

_FAILURE_KEY_PREFIX = "sincor:loginfail:"
_FAILURE_TTL_SECONDS = 3600  # counters decay after an hour without attempts
_DEFAULT_BACKOFF_CAP_SECONDS = 8.0

_sleep: Callable[[float], None] = time.sleep  # indirection: tests capture delays

_storages: dict = {}


def _backoff_storage():
    """Failure-counter storage on the limiter's own backend (shared workers)."""
    from limits.storage import storage_from_string

    uri = (os.environ.get("SINCOR_LIMITER_STORAGE_URI") or "memory://").strip() or "memory://"
    storage = _storages.get(uri)
    if storage is None:
        storage = storage_from_string(uri)
        _storages[uri] = storage
    return storage


def login_attempt_identifier() -> str:
    """Best-effort login identifier from the current request (JSON or form)."""
    ident = ""
    try:
        data = request.get_json(silent=True) or {}
    except Exception:
        data = {}
    if isinstance(data, dict):
        ident = (
            data.get("username")
            or data.get("email")
            or data.get("identifier")
            or data.get("user")
            or ""
        )
    if not ident:
        try:
            vals = request.values
            ident = (
                vals.get("identifier") or vals.get("email") or vals.get("username") or ""
            )
        except Exception:
            ident = ""
    return str(ident or "").strip().lower()[:128]


def _login_account_key() -> str:
    """flask-limiter key_func: per-account bucket (hashed, no PII in keys)."""
    ident = login_attempt_identifier() or "<unknown>"
    digest = hashlib.sha256(ident.encode("utf-8")).hexdigest()[:32]
    return f"login-account:{digest}"


def credential_rate_limits(limiter, *, per_method: bool = False, methods=None):
    """Stacked credential-verifier rate limits on the existing limiter.

    Applies the per-IP rule and the per-account rule. ``per_method=True``
    keeps methods in separate buckets; ``methods=["POST"]`` restricts the
    limits to the listed methods entirely (e.g. the form login page: GET
    page renders are not credential checks and stay unlimited).
    """

    def _decorate(fn):
        if limiter is None:  # limiter unavailable: leave route unadorned
            return fn
        fn = limiter.limit(
            IP_CREDENTIAL_LIMITS, per_method=per_method, methods=methods
        )(fn)
        fn = limiter.limit(
            ACCOUNT_CREDENTIAL_LIMITS,
            key_func=_login_account_key,
            per_method=per_method,
            methods=methods,
        )(fn)
        return fn

    return _decorate


def _failure_key(identifier: str) -> str:
    digest = hashlib.sha256(identifier.strip().lower().encode("utf-8")).hexdigest()
    return f"{_FAILURE_KEY_PREFIX}{digest}"


def login_backoff_delay_seconds(failures: int) -> float:
    """Progressive delay for the Nth consecutive failure (seconds)."""
    try:
        cap = float(os.environ.get("SINCOR_LOGIN_BACKOFF_MAX_SECONDS", ""))
    except (TypeError, ValueError):
        cap = _DEFAULT_BACKOFF_CAP_SECONDS
    if cap != cap:  # NaN guard
        cap = _DEFAULT_BACKOFF_CAP_SECONDS
    if cap <= 0:
        return 0.0
    return min(2.0 ** max(int(failures) - 1, 0), cap)


def note_login_failure(identifier: Optional[str] = None) -> float:
    """Record a failed credential check; sleep the progressive backoff.

    Returns the delay applied (0 when backoff is disabled or the identifier
    is empty). Never raises — throttling must not break the login route.
    """
    ident = (identifier if identifier is not None else login_attempt_identifier())
    ident = (ident or "").strip().lower()
    if not ident:
        return 0.0
    try:
        failures = int(_backoff_storage().incr(_failure_key(ident), _FAILURE_TTL_SECONDS))
    except Exception as exc:
        logger.warning("login backoff storage unavailable: %s", exc)
        failures = 1
    delay = login_backoff_delay_seconds(failures)
    if delay > 0:
        try:
            _sleep(delay)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("login backoff sleep interrupted: %s", exc)
    return delay


def note_login_success(identifier: Optional[str] = None) -> None:
    """Clear the failure counter after a successful login. Never raises."""
    ident = (identifier if identifier is not None else login_attempt_identifier())
    ident = (ident or "").strip().lower()
    if not ident:
        return
    try:
        _backoff_storage().clear(_failure_key(ident))
    except Exception as exc:
        logger.warning("login backoff clear failed: %s", exc)
