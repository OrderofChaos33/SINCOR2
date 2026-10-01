"""A2A-specific rate-limit policies (Stream 4 — protocol hardening).

Additive layer. This module does NOT touch ``SINCORRateLimiter`` internals
(``src/sincor2/rate_limiter.py``) and does NOT change any endpoint behavior.

What it is:
  * ``A2A_RATE_POLICIES`` — per-abuse-class sliding-window policies, tuned for
    the A2A surface (registration spam, bid/commit spam, quote scraping,
    dispute filing, bulk reads).
  * ``ENDPOINT_POLICY`` — maps each A2A route to its policy class, so a
    reviewer can see coverage at a glance.
  * ``SlidingWindowLimiter`` — a dependency-free, clock-injectable limiter
    used to prove the policies behave (see
    ``tests/pytest/test_a2a_rate_limits.py``). Production wiring should use
    the existing flask-limiter via ``flask_limit_strings()`` below; this
    class is the executable specification.

Wiring (when approved — not done here):

    from sincor2.rate_limiter import SINCORRateLimiter  # existing
    from sincor2.a2a_rate_limits import flask_limit_strings, a2a_key_func

    rl = SINCORRateLimiter(app)          # unchanged
    limiter = rl.get_limiter()
    # per-route, e.g. on the quote endpoint:
    #   @limiter.limit(flask_limit_strings("quote"), key_func=a2a_key_func)

Why per-class policies instead of the global default (1000/day, 200/hour):
  * ``POST /v1/a2a/register`` is the Sybil surface — one bot registering
    thousands of agents poisons discovery, velocity metrics, and the KYA
    queue. 5/hour + 20/day per IP makes farming expensive while a human
    onboarding flow never notices.
  * ``POST /v1/a2a/bids/commit`` is cheap to call and every commit locks
    stake-ledger rows. 30/min + 300/hour per *agent* (not per IP — agents
    behind one NAT egress must not share a bucket) bounds row churn.
  * ``GET/POST /api/a2a/quote`` is unauthenticated and machine-pollable;
    60/min + 2000/hour per IP stops price-feed scraping without hurting
    real bidders (a bidder needs ~1 quote per auction).
  * ``POST /v1/a2a/disputes`` is adjudicator-signed, so abuse is already
    bounded by the signature check; 5/hour + 20/day per IP is a backstop.
"""

from __future__ import annotations

import time
import threading
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, List, Tuple


@dataclass(frozen=True)
class Window:
    """One sliding window: at most ``max_hits`` events per ``seconds``."""
    max_hits: int
    seconds: int


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------
# Keyed by abuse class. Every list is ANDed: a request must pass ALL windows.

A2A_RATE_POLICIES: Dict[str, List[Window]] = {
    # Sybil surface: agent registration. Strictest policy in the set.
    "register": [Window(5, 3600), Window(20, 86400)],
    # Bid/commit/reveal: cheap calls, lock ledger rows. Keyed per agent_id.
    "bid": [Window(30, 60), Window(300, 3600)],
    # Unauthenticated price endpoint: scraping surface.
    "quote": [Window(60, 60), Window(2000, 3600)],
    # Adjudicator-signed already; backstop only.
    "dispute": [Window(5, 3600), Window(20, 86400)],
    # P24 creator-token issuance: high-value, spam-prone write. Strictest
    # tier, same abuse class as registration. Keyed per agent_id; the
    # registration tier (5/hour/IP) backstops agent_id rotation.
    "issuance": [Window(5, 3600), Window(20, 86400)],
    # Bulk reads: auctions list, bidder-kit, cards, directory, agents.
    "read": [Window(120, 60), Window(5000, 3600)],
}


# ---------------------------------------------------------------------------
# Endpoint -> policy mapping (coverage checklist)
# ---------------------------------------------------------------------------
# Route strings as registered in a2a_inbound_market.py (attach_market_routes)
# and a2a_inbound_ext.py (mount) / a2a_integration.py (A2ARouter).

ENDPOINT_POLICY: Dict[str, str] = {
    # registration (a2a_inbound_ext.mount)
    "POST /v1/a2a/register": "register",
    "POST /api/v1/a2a/register": "register",
    "POST /api/marketplace/register": "register",
    # bidding (attach_market_routes)
    "POST /v1/a2a/bids": "bid",
    "POST /api/v1/bids": "bid",
    "POST /v1/a2a/bids/commit": "bid",
    "POST /v1/a2a/bids/reveal": "bid",
    # disputes (attach_market_routes)
    "POST /v1/a2a/disputes": "dispute",
    # P24 socialfi issuance (attach_market_routes)
    "POST /v1/a2a/socialfi/issue": "issuance",
    # quotes (A2ARouter, a2a_integration.py)
    "GET /api/a2a/quote": "quote",
    "POST /api/a2a/quote": "quote",
    # reads (attach_market_routes / mount)
    "GET /v1/a2a/tasks": "read",
    "GET /v1/a2a/auctions": "read",
    "GET /v1/a2a/tasks/<task_id>/bidder-kit": "read",
    "GET /v1/a2a/tasks/<task_id>": "read",
    "GET /v1/a2a/cards": "read",
    "GET /v1/a2a/directory": "read",
    "GET /v1/a2a/agents": "read",
    "GET /v1/a2a/registration-velocity": "read",
    # stake self-service (attach_market_routes; added when the route shipped —
    # same abuse class as bids: cheap writes that mutate ledger rows).
    "POST /v1/a2a/stake/deposit": "bid",
    "GET /v1/a2a/stake/<agent_id>": "read",
    # launch bounty pool (attach_market_routes): public status read, and
    # admin-gated writes in the same cheap-write abuse class as bids.
    "GET /v1/a2a/pool": "read",
    "POST /v1/a2a/pool/fund": "bid",
    "POST /v1/a2a/pool/allocate": "bid",
    "POST /v1/a2a/pool/release": "bid",
}


def policy_for(method: str, path: str) -> str | None:
    """Return the policy class for a ``METHOD path`` pair, or None."""
    return ENDPOINT_POLICY.get(f"{method.upper()} {path}")


def flask_limit_strings(policy: str) -> List[str]:
    """Convert a policy to flask-limiter limit strings, e.g.
    ``["30 per minute", "300 per hour"]``.

    Use with the existing limiter, e.g.:
        @limiter.limit(flask_limit_strings("quote"))
    """
    out = []
    for w in A2A_RATE_POLICIES[policy]:
        if w.seconds < 60:
            n, unit = w.seconds, "second"
        elif w.seconds < 3600:
            n, unit = w.seconds // 60, "minute"
        elif w.seconds < 86400:
            n, unit = w.seconds // 3600, "hour"
        else:
            n, unit = w.seconds // 86400, "day"
        # flask-limiter convention: "30 per minute", "5 per 2 hours".
        label = unit if n == 1 else f"{n} {unit}s"
        out.append(f"{w.max_hits} per {label}")
    return out


def a2a_client_key(agent_id: str | None = None, ip: str | None = None) -> str:
    """Bucket key: prefer the agent identity, fall back to IP.

    Bid/commit/reveal MUST be keyed per agent_id — agents behind one NAT
    egress (same datacenter, same corporate proxy) must not share a bucket.
    Registration and quote have no agent identity yet, so they key on IP.
    """
    if agent_id:
        return f"agent:{agent_id}"
    return f"ip:{ip or 'unknown'}"


# ---------------------------------------------------------------------------
# Executable specification: sliding-window limiter with injectable clock
# ---------------------------------------------------------------------------

class SlidingWindowLimiter:
    """Minimal correct sliding-window limiter.

    Production traffic should go through flask-limiter (Redis-backed,
    multi-worker). This class exists so the *policies* can be proven in
    unit tests without Flask, Redis, or wall-clock sleeps.
    """

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        # (policy, client_key) -> deque of event timestamps (ascending)
        self._hits: Dict[Tuple[str, str], Deque[float]] = {}

    def check(self, policy: str, client_key: str) -> Tuple[bool, Dict[str, float]]:
        """Record one event and report whether it is allowed.

        Returns ``(allowed, info)`` where ``info`` carries ``retry_after``
        seconds when denied (0 when allowed) and the tightest window that
        triggered the denial.
        """
        now = self._clock()
        windows = A2A_RATE_POLICIES[policy]
        key = (policy, client_key)
        events = self._hits.setdefault(key, deque())

        # Evict anything older than the longest window.
        longest = max(w.seconds for w in windows)
        while events and events[0] <= now - longest:
            events.popleft()

        for w in windows:
            count = sum(1 for t in events if t > now - w.seconds)
            if count >= w.max_hits:
                oldest_in_window = min(t for t in events if t > now - w.seconds)
                retry_after = (oldest_in_window + w.seconds) - now
                return False, {
                    "retry_after": max(0.0, retry_after),
                    "window_seconds": float(w.seconds),
                    "window_max": float(w.max_hits),
                }

        events.append(now)
        return True, {"retry_after": 0.0}


# ---------------------------------------------------------------------------
# Enforcement: before_request wiring for the A2A blueprints
# ---------------------------------------------------------------------------
# The policies above are enforced with the SlidingWindowLimiter spec class
# itself (not flask-limiter strings), because the task contract requires an
# exact 429 JSON body *including* Retry-After — which the spec's check()
# returns deterministically. This keeps one code path for production
# (mvp_app) and bare-Flask test apps, with no extension-init dance.
#
# Wire once per blueprint:
#     from sincor2.a2a_rate_limits import a2a_rate_limit_check
#     bp.before_request(a2a_rate_limit_check)
#
# Only routes present in ENDPOINT_POLICY are limited. Everything else —
# admin/operator routes, health checks, heartbeats, proof submission, task
# create/close, the SSE stream, chain probes — is deliberately untouched.

import math

from sincor2.a2a_shared_state import (
    SharedSlidingWindowLimiter,
    StateStoreUnavailable,
    get_shared_state,
)

# Process-wide enforcer, built lazily so tests can set A2A_STATE_STORE /
# FLASK_ENV before first use. Storage moved to the shared backend
# (P3 wave 15, G2.8): N workers now agree on counters and restarts
# preserve them. SlidingWindowLimiter above remains the executable spec.
_ENFORCER: SharedSlidingWindowLimiter | None = None
_ENFORCER_LOCK = threading.Lock()


def _policy_tuples() -> Dict[str, List[Tuple[float, float]]]:
    return {
        name: [(float(w.seconds), float(w.max_hits)) for w in wins]
        for name, wins in A2A_RATE_POLICIES.items()
    }


def _get_enforcer() -> SharedSlidingWindowLimiter:
    global _ENFORCER
    if _ENFORCER is None:
        with _ENFORCER_LOCK:
            if _ENFORCER is None:
                _ENFORCER = SharedSlidingWindowLimiter(
                    get_shared_state(), _policy_tuples())
    return _ENFORCER


def reset_a2a_limits() -> None:
    """Clear all recorded hits. Test isolation hook — call between tests."""
    _get_enforcer().reset()


def _client_ip() -> str:
    try:
        from flask_limiter.util import get_remote_address
        return get_remote_address() or "unknown"
    except Exception:
        try:
            from flask import request
            return request.remote_addr or "unknown"
        except Exception:
            return "unknown"


def a2a_rate_limit_check():
    """Flask before_request handler. Returns a 429 response on breach,
    None when the request may proceed."""
    from flask import jsonify, request

    rule = request.url_rule
    if rule is None:
        return None
    policy = policy_for(request.method, str(rule))
    if policy is None:
        return None

    if policy in ("bid", "issuance"):
        # Per-agent keying: agents behind one NAT egress must not share a
        # bucket. Falls back to IP when the body carries no agent_id.
        body = request.get_json(silent=True) or {}
        agent_id = str(body.get("agent_id") or "").strip()
        client_key = a2a_client_key(agent_id=agent_id or None, ip=_client_ip())
    else:
        # register / quote / dispute / read: no stable agent identity at
        # this layer (or the agent_id names someone else, as in disputes),
        # so key on client IP — same get_remote_address the app limiter uses.
        client_key = a2a_client_key(ip=_client_ip())

    try:
        allowed, info = _get_enforcer().check(policy, client_key)
    except StateStoreUnavailable:
        # Fail closed (G2.8): the configured shared store is unreachable.
        # Deny the request rather than silently resetting counters.
        resp = jsonify({
            "error": "state_store_unavailable",
            "status": 503,
            "policy": policy,
            "detail": "shared rate-limit store unreachable; request denied",
        })
        resp.status_code = 503
        resp.headers["Retry-After"] = "5"
        return resp
    if allowed:
        return None

    retry_after = int(math.ceil(info.get("retry_after") or 0.0))
    resp = jsonify({
        "error": "rate_limited",
        "status": 429,
        "policy": policy,
        "retry_after": retry_after,
    })
    resp.status_code = 429
    resp.headers["Retry-After"] = str(retry_after)
    return resp
