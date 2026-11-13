"""Dedicated admin API key + failed-key rate limiting (Wren PR 4).

Two hardening changes over the historical posture, where the
``X-Admin-Key`` header was compared directly against ``ADMIN_PASSWORD``
(the login password doubling as an API key):

1. A dedicated ``ADMIN_API_KEY`` credential. When it is configured —
   at least :data:`MIN_ADMIN_API_KEY_LENGTH` characters — it is the
   ONLY value accepted in the ``X-Admin-Key`` header; the password
   stops working as a header key. While ``ADMIN_API_KEY`` is unset (or
   too short to be a real key), the legacy ``ADMIN_PASSWORD`` header
   key keeps working so existing operators and tests are not locked
   out mid-migration. The key is header-only and compared in constant
   time; an unconfigured dedicated key simply does not exist as a
   credential (fail-closed).

2. A brute-force brake: failed ``X-Admin-Key`` attempts are counted
   per client address in a sliding window; past
   :data:`ADMIN_KEY_RATE_LIMIT` failures inside
   :data:`ADMIN_KEY_RATE_WINDOW_SECONDS` the caller gets a 429 JSON
   response. Requests that authenticate — by key, session, or token —
   never consume budget, so a legitimate operator cannot be locked
   out by the brake, and the brake cannot be tripped by anonymous
   browsing (no key header, no count).

This module is the ONE shared implementation: the waitlist and
admin-intakes blueprints (which cannot import mvp_app without a
cycle), polyclaw_scheduler, the command-center blueprint, and
mvp_app's own ``_check_admin_key`` / admin guards all call into it.
It imports nothing from sincor2, so it is safe to import anywhere.
"""
from __future__ import annotations

import hmac
import os
import threading
import time
from collections import deque

MIN_ADMIN_API_KEY_LENGTH = 32
ADMIN_KEY_RATE_LIMIT = 10
ADMIN_KEY_RATE_WINDOW_SECONDS = 60.0


# ---------------------------------------------------------------------------
# Key verification
# ---------------------------------------------------------------------------


def configured_admin_api_key() -> str:
    """The dedicated ADMIN_API_KEY, or "" when not validly configured.

    A value shorter than MIN_ADMIN_API_KEY_LENGTH is treated as
    unconfigured: it never authenticates anything.
    """
    key = (os.environ.get("ADMIN_API_KEY") or "").strip()
    if len(key) < MIN_ADMIN_API_KEY_LENGTH:
        return ""
    return key


def _compare(provided, expected) -> bool:
    if not provided or not expected:
        return False
    try:
        return hmac.compare_digest(str(provided), str(expected))
    except (TypeError, ValueError):
        # Non-ASCII header values are un-comparable in constant time;
        # fail closed instead of raising into the request path.
        return False


def check_admin_api_key(provided) -> bool:
    """True iff ``provided`` is exactly the configured ADMIN_API_KEY."""
    return _compare(provided, configured_admin_api_key())


def check_admin_header_key(provided) -> bool:
    """The X-Admin-Key header check used by every admin surface.

    Dedicated key configured -> only it authenticates. Otherwise the
    legacy ADMIN_PASSWORD header key applies (migration posture).
    """
    dedicated = configured_admin_api_key()
    if dedicated:
        return _compare(provided, dedicated)
    legacy = (os.environ.get("ADMIN_PASSWORD") or "").strip()
    return _compare(provided, legacy)


# ---------------------------------------------------------------------------
# Failed-key rate limiting (sliding window, per client address, in-process)
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_attempts: dict[str, deque] = {}


def _prune(dq: deque, now: float) -> None:
    cutoff = now - ADMIN_KEY_RATE_WINDOW_SECONDS
    while dq and dq[0] <= cutoff:
        dq.popleft()


def record_failed_admin_key_attempt(client_addr, *, now: float | None = None) -> bool:
    """Record one failed key attempt for ``client_addr``.

    Returns True when this attempt pushes the address past the limit
    (i.e. the caller should now be refused with 429).
    """
    now = time.monotonic() if now is None else now
    addr = str(client_addr or "unknown")
    with _lock:
        dq = _attempts.setdefault(addr, deque())
        _prune(dq, now)
        dq.append(now)
        return len(dq) > ADMIN_KEY_RATE_LIMIT


def reset_admin_key_rate_limit() -> None:
    """Clear all recorded attempts (test isolation / operator reset)."""
    with _lock:
        _attempts.clear()


def admin_key_rate_limit_response():
    """Flask ``before_request`` glue: the failed-key brake.

    Returns a 429 JSON response when the current request carries an
    ``X-Admin-Key`` header that does not validate AND the client
    address has now exceeded the failed-attempt budget. Returns None
    (request proceeds) in every other case: no header, valid key, or
    budget remaining. Records at most one attempt per request, even
    when several layers (app guard + blueprint guard) both run.
    """
    from flask import g, jsonify, request

    if getattr(g, "_admin_key_attempt_recorded", False):
        return None
    key = request.headers.get("X-Admin-Key", "")
    if not key:
        return None
    if check_admin_header_key(key):
        return None
    g._admin_key_attempt_recorded = True
    if record_failed_admin_key_attempt(request.remote_addr):
        return jsonify({"error": "Too many requests"}), 429
    return None
