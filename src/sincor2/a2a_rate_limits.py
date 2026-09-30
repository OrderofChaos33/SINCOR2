"""A2A-specific rate-limit policies (Stream 4 — protocol hardening).

Additive layer. This module does NOT touch ``SINCORRateLimiter`` internals
(``src/sincor2/rate_limiter.py``) and does NOT change any endpoint behavior
beyond returning 429 on policy breach.

What it is:
  * ``A2A_RATE_POLICIES`` — per-abuse-class sliding-window policies, tuned for
    the A2A surface (registration spam, bid/commit spam, quote scraping,
    dispute filing, settlement, task writes, heartbeats, SSE streams,
    admin backstops, bulk reads).
  * ``ENDPOINT_POLICY`` — maps each A2A route to its policy class, so a
    reviewer can see coverage at a glance. Every mutating or high-value
    route has an explicit tier (G2.9).
  * ``SlidingWindowLimiter`` — a dependency-free, clock-injectable limiter
    backed by a pluggable ``RateLimitStore``. The default store is
    in-memory; wave 15 can swap in a durable (SQLite/Redis) store
    implementing the same interface with no other code changes.
  * ``check_stream_auth`` / stream constants — authentication + caller
    identity for the SSE stream (G2.10). Concurrency capping lives in
    ``a2a_inbound_market.v1_stream``; the per-connection *rate* tier lives
    here under the ``stream`` policy.

Wiring (live):
  * ``a2a_inbound_ext.mount`` — ``bp.before_request(a2a_rate_limit_check)``
    covers the inbound blueprint *including* the market routes attached by
    ``attach_market_routes`` (tasks, bids, close, proofs, disputes,
    auctions, stake, pool, stream, sponsored-stake, recovery).
  * ``A2ARouter`` (``a2a_integration.py``) — same before_request covers the
    discovery/RPC blueprint (quote, settle, tasks/send, tasks/cancel,
    leaderboard, pricing).
  * ``mvp_wardrobe`` (``mvp_blueprints/wardrobe.py``) — same before_request
    covers the token-authed heartbeat and agent reads.

Why per-class policies instead of the global default (1000/day, 200/hour):
  * ``POST /v1/a2a/register`` is the Sybil surface — one bot registering
    thousands of agents poisons discovery, velocity metrics, and the KYA
    queue. 5/hour + 20/day per IP makes farming expensive while a human
    onboarding flow never notices.
  * ``POST /v1/a2a/bids/commit`` is cheap to call and every commit locks
    stake-ledger rows. 30/min + 300/hour per *agent|ip* bounds row churn.
  * ``GET/POST /api/a2a/quote`` is unauthenticated and machine-pollable;
    60/min + 2000/hour per IP stops price-feed scraping without hurting
    real bidders (a bidder needs ~1 quote per auction).
  * ``POST /v1/a2a/disputes`` is adjudicator-signed, so abuse is already
    bounded by the signature check; 5/hour + 20/day per IP is a backstop.
  * ``POST /api/a2a/settle`` is the money path (settlement proofs feed
    reputation + fee accounting); 10/min + 100/hour per caller.
  * ``POST /v1/a2a/heartbeat`` is liveness-critical: a spoofer must not be
    able to exhaust *another* agent's bucket and fake it dead, so the key
    is the ``agent|ip`` composite, never the bare agent_id.

Keying model (adversarial notes — read before "improving" it):
  * Identity-keyed tiers use the composite ``agent:<id>|ip:<ip>``. A bare
    ``agent:<id>`` key would let anyone spoofing a victim's agent_id
    exhaust the victim's bucket (targeted liveness/bidding DoS). A bare
    ``ip:`` key would let one NAT egress's agents share a bucket. The
    composite keeps both properties: different agents behind one NAT are
    isolated, and a spoofer's traffic lands in the *spoofer's* bucket.
    One exception: ``POST /api/a2a/heartbeat`` (wardrobe, token-authed) is
    IP-keyed even though it carries an agent_id, so rotating the claimed
    identity cannot evade the token brute-force backstop.
  * The claimed identity is self-asserted on most routes (body/query/header)
    — it is *not* cryptographic proof. An attacker rotating BOTH identity
    and IP can evade the limiter; IP rotation alone defeats the IP-keyed
    tiers too. The limiter is a cost-raiser and abuse backstop, not an
    authentication mechanism. Cryptographic caller identity (heartbeat
    wallet proofs, registration proofs, adjudicator signatures) is what
    makes a key unforgeable; those waves strengthen this layer.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, List, Protocol, Tuple


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
    # Bid/commit/reveal: cheap calls, lock ledger rows. Composite agent|ip
    # keying — see the module docstring for the poisoning analysis.
    "bid": [Window(30, 60), Window(300, 3600)],
    # Unauthenticated price endpoint: scraping surface.
    "quote": [Window(60, 60), Window(2000, 3600)],
    # Adjudicator-signed already; backstop only.
    "dispute": [Window(5, 3600), Window(20, 86400)],
    # Bulk reads: auctions list, bidder-kit, cards, directory, agents,
    # leaderboard, pricing, chain probes.
    "read": [Window(120, 60), Window(5000, 3600)],
    # Settlement: the money path. Strictest write tier.
    "settle": [Window(10, 60), Window(100, 3600)],
    # Task lifecycle writes: create/delete/close/proofs. Cheap,
    # state-mutating calls; composite agent|ip keying.
    "task_write": [Window(20, 60), Window(300, 3600)],
    # Task messaging: tasks/send, tasks/cancel, JSON-RPC dispatch.
    "task_msg": [Window(30, 60), Window(300, 3600)],
    # Heartbeat: liveness-critical. HEARTBEAT_TTL_S is 60s and agents beat
    # at ~TTL/2 cadence, so 20/min is ~10x headroom for jitter and
    # multi-process fleets. Composite key: a spoofer must never exhaust
    # another agent's bucket and fake it dead.
    "heartbeat": [Window(20, 60), Window(300, 3600)],
    # SSE stream *connection establishment* rate. Steady-state concurrency
    # is capped per caller in the view itself (v1_stream); this tier bounds
    # reconnect churn.
    "stream": [Window(6, 60), Window(60, 3600)],
    # Admin routes: already credential-gated (ADMIN_PASSWORD /
    # SINCOR_BOUNTY_POOL_ADMIN_KEY); the limiter is a brute-force backstop.
    "admin": [Window(30, 60), Window(200, 3600)],
}


# ---------------------------------------------------------------------------
# Endpoint -> policy mapping (coverage checklist)
# ---------------------------------------------------------------------------
# Route strings as registered in a2a_inbound_market.py (attach_market_routes),
# a2a_inbound_ext.py (mount), a2a_integration.py (A2ARouter), sponsored_stake.py
# and recovery.py (attached to the market blueprint), and
# mvp_blueprints/wardrobe.py. ``policy_for`` matches on
# ``request.url_rule``, so ``<param>`` placeholders are literal here.

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
    # quotes (A2ARouter, a2a_integration.py)
    "GET /api/a2a/quote": "quote",
    "POST /api/a2a/quote": "quote",
    # reads (attach_market_routes / mount / A2ARouter / wardrobe)
    "GET /v1/a2a/tasks": "read",
    "GET /v1/a2a/auctions": "read",
    "GET /v1/a2a/tasks/<task_id>/bidder-kit": "read",
    "GET /v1/a2a/tasks/<task_id>": "read",
    "GET /v1/a2a/cards": "read",
    "GET /v1/a2a/directory": "read",
    "GET /v1/a2a/agents": "read",
    "GET /v1/a2a/registration-velocity": "read",
    "GET /v1/a2a/chain": "read",
    "GET /api/a2a/agents": "read",
    "GET /api/a2a/tasks/<task_id>": "read",
    "GET /api/a2a/leaderboard": "read",
    "GET /api/a2a/pricing": "read",
    # stake self-service (attach_market_routes): same abuse class as bids —
    # cheap writes that mutate ledger rows.
    "POST /v1/a2a/stake/deposit": "bid",
    "GET /v1/a2a/stake/<agent_id>": "read",
    # launch bounty pool (attach_market_routes): public status read, and
    # admin-gated writes in the same cheap-write abuse class as bids.
    "GET /v1/a2a/pool": "read",
    "POST /v1/a2a/pool/fund": "bid",
    "POST /v1/a2a/pool/allocate": "bid",
    "POST /v1/a2a/pool/release": "bid",
    # --- wave 16: high-value / mutating routes (G2.9) ---
    # JSON-RPC dispatch + legacy REST send/cancel (A2ARouter)
    "POST /api/a2a": "task_msg",
    "POST /api/a2a/tasks/send": "task_msg",
    "POST /api/a2a/tasks/cancel": "task_msg",
    # settlement: money path (A2ARouter)
    "POST /api/a2a/settle": "settle",
    # task lifecycle (attach_market_routes)
    "POST /v1/a2a/tasks": "task_write",
    "DELETE /v1/a2a/tasks/<task_id>": "task_write",
    "POST /v1/a2a/tasks/<task_id>/close": "task_write",
    "POST /v1/a2a/proofs": "task_write",
    "POST /api/v1/proofs": "task_write",
    # heartbeat: liveness-critical (mount + wardrobe)
    "POST /v1/a2a/heartbeat": "heartbeat",
    "POST /api/a2a/heartbeat": "heartbeat",
    # SSE stream connection establishment (attach_market_routes)
    "GET /v1/a2a/stream": "stream",
    "GET /api/v1/stream": "stream",
    # admin backstops (sponsored_stake.py / recovery.py, credential-gated)
    "POST /v1/a2a/admin/sponsored-stake": "admin",
    "GET /v1/a2a/admin/sponsored-stake/<agent_id>": "admin",
    "POST /v1/a2a/admin/recovery/sponsor": "admin",
    "GET /v1/a2a/admin/recovery/status": "admin",
}


def policy_for(method: str, path: str) -> str | None:
    """Return the policy class for a ``METHOD path`` pair, or None."""
    return ENDPOINT_POLICY.get(f"{method.upper()} {path}")


def flask_limit_strings(policy: str) -> List[str]:
    """Convert a policy to flask-limiter limit strings, e.g.
    ``["30 per minute", "300 per hour"]``.

    Kept for operators who wire tiers into flask-limiter directly.
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

    Legacy helper kept for the executable-spec tests. Production keying
    goes through :func:`a2a_caller_key`, which binds the identity to the
    client IP (see module docstring).
    """
    if agent_id:
        return f"agent:{agent_id}"
    return f"ip:{ip or 'unknown'}"


def a2a_caller_key(
    agent_id: str | None = None,
    wallet: str | None = None,
    ip: str | None = None,
) -> str:
    """Production bucket key: strongest available identity bound to the IP.

    ``wallet:<addr>|ip:<ip>`` when a verified wallet is available,
    ``agent:<id>|ip:<ip>`` for a claimed agent identity, else ``ip:<ip>``.
    The composite defeats cross-caller bucket poisoning: traffic claiming
    agent X from attacker IP Y never touches the bucket of the real agent
    X on IP Z. See the module docstring for the evasion analysis.
    """
    ip = ip or "unknown"
    if wallet:
        return f"wallet:{wallet.lower()}|ip:{ip}"
    if agent_id:
        return f"agent:{agent_id}|ip:{ip}"
    return f"ip:{ip}"


# ---------------------------------------------------------------------------
# Backing-store seam (wave 15)
# ---------------------------------------------------------------------------

class RateLimitStore(Protocol):
    """Durable backing-store interface for the limiter.

    Wave 15 replaces the in-memory store with a durable one (SQLite/Redis)
    implementing exactly this interface — no other code changes needed::

        limiter = SlidingWindowLimiter(store=DurableRateLimitStore(...))

    ``check`` must be atomic (check-and-record under one lock/transaction):
    two racing requests must not both slip under the cap. ``clear`` is a
    test-isolation hook; durable implementations may implement it as a
    best-effort flush.
    """

    def check(
        self,
        policy: str,
        client_key: str,
        windows: List[Window],
        now: float,
    ) -> Tuple[bool, Dict[str, float]]:
        """Atomically record one hit; return ``(allowed, info)`` where
        ``info`` carries ``retry_after`` seconds on denial (0 when allowed)
        plus the tightest ``window_seconds`` / ``window_max``."""
        ...  # pragma: no cover

    def clear(self) -> None:
        """Drop all recorded hits (test isolation)."""
        ...  # pragma: no cover


class MemoryRateLimitStore:
    """In-memory store: the wave-16 default.

    Thread-safe (one lock around check-and-record). Per-process by design —
    it does NOT coordinate across workers; that is wave 15's job. Do not
    mistake multi-process deployments for shared state: each worker gets
    its own budget under this store (documented, not hidden).
    """

    def __init__(self) -> None:
        # (policy, client_key) -> deque of event timestamps (ascending)
        self._hits: Dict[Tuple[str, str], Deque[float]] = {}
        self._lock = threading.Lock()

    def check(
        self,
        policy: str,
        client_key: str,
        windows: List[Window],
        now: float,
    ) -> Tuple[bool, Dict[str, float]]:
        key = (policy, client_key)
        with self._lock:
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

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()


class SlidingWindowLimiter:
    """Sliding-window limiter over a pluggable :class:`RateLimitStore`.

    Kept dependency-free and clock-injectable so the *policies* stay
    provable in unit tests without Flask, Redis, or wall-clock sleeps.
    """

    def __init__(
        self,
        clock: Callable[[], float] | None = None,
        store: RateLimitStore | None = None,
    ) -> None:
        self._clock = clock or time.monotonic
        self._store: RateLimitStore = store or MemoryRateLimitStore()

    def check(self, policy: str, client_key: str) -> Tuple[bool, Dict[str, float]]:
        """Record one event and report whether it is allowed."""
        return self._store.check(
            policy, client_key, A2A_RATE_POLICIES[policy], self._clock()
        )

    def reset(self) -> None:
        """Clear all recorded hits (test isolation)."""
        self._store.clear()


# ---------------------------------------------------------------------------
# Stream auth + identity (G2.10)
# ---------------------------------------------------------------------------
# The SSE stream previously accepted unauthenticated connections and held a
# worker up to ~240s each. Two accepted credentials (either is enough):
#   1. the operator token (AGENT_HEARTBEAT_TOKEN) via the X-Sincor-Heartbeat
#      header or an Authorization: Bearer header — same convention as the
#      wardrobe heartbeat; compared with hmac.compare_digest on sha256.
#   2. a registered agent_id (query param or X-Agent-Id header) present in
#      the fabric registry — registered-agent membership, the same
#      explicitly-non-cryptographic convention the issuance route uses.
# Anything else -> 401. Rate-limit keying reuses the same identity so the
# "stream" tier and the concurrency cap agree on who the caller is.

STREAM_MAX_PER_CALLER = 3
STREAM_IDLE_TIMEOUT_S = 240  # pre-existing worker-hold bound, now named
STREAM_KEEPALIVE_S = 2


def _stream_operator_token() -> str:
    return (os.environ.get("AGENT_HEARTBEAT_TOKEN") or "").strip()


def check_stream_auth() -> Tuple[bool, str]:
    """Authenticate an SSE stream connection.

    Returns ``(authorized, caller_key)``. The caller key is stable for both
    the rate-limit tier and the concurrency cap: ``operator|ip:<ip>`` for
    the operator token, ``agent:<id>|ip:<ip>`` for a registered agent, and
    ``ip:<ip>`` when unauthenticated (the view rejects those with 401, but
    the rate limiter still counts the attempt).
    """
    from flask import request

    ip = _client_ip()
    expected = _stream_operator_token()
    got = (
        request.headers.get("X-Sincor-Heartbeat")
        or (request.headers.get("Authorization") or "").removeprefix("Bearer ")
        or ""
    ).strip()
    if expected and got and hmac.compare_digest(
        hashlib.sha256(got.encode()).digest(),
        hashlib.sha256(expected.encode()).digest(),
    ):
        return True, f"operator|ip:{ip}"

    agent_id = (
        str(request.args.get("agent_id") or "").strip()
        or str(request.headers.get("X-Agent-Id") or "").strip()
    )
    if agent_id:
        try:
            from sincor2.a2a_inbound import get_fabric

            fabric = get_fabric()
            with fabric.lock:
                known = agent_id in (fabric.agents or {})
        except Exception:
            known = False
        if known:
            return True, a2a_caller_key(agent_id=agent_id, ip=ip)

    return False, a2a_caller_key(ip=ip)


def stream_caller_key() -> str:
    """Caller key for the ``stream`` rate-limit tier (auth not required to
    compute it — the view 401s unauthenticated callers separately)."""
    _authed, key = check_stream_auth()
    return key


# ---------------------------------------------------------------------------
# Enforcement: before_request wiring for the A2A blueprints
# ---------------------------------------------------------------------------
# ``a2a_rate_limit_check`` runs as ``before_request`` on the a2a_inbound
# blueprint (mount, incl. market routes), the A2ARouter blueprint, and the
# wardrobe blueprint. Only routes present in ENDPOINT_POLICY are limited.
# Keying: identity-keyed tiers (bid/task_write/task_msg/settle/heartbeat)
# use the composite agent|ip key; stream uses the stream caller identity;
# everything else (register/quote/dispute/read/admin) keys on client IP —
# the admin routes are credential-gated, so IP keying is the brute-force
# backstop there.

_IDENTITY_POLICIES = frozenset({"bid", "task_write", "task_msg", "settle", "heartbeat"})

# Routes where the claimed identity is asserted pre-auth on a
# credential-guessing surface: key on IP so rotating the claimed identity
# cannot evade the brute-force backstop. The /v1/a2a heartbeat keeps
# composite keying — it is liveness-critical, so bucket-poisoning
# resistance wins there.
_IP_KEYED_ROUTES = frozenset({"POST /api/a2a/heartbeat"})

_IDENTITY_FIELDS = ("agent_id", "caller_id", "callerId", "caller", "agent", "poster_id")


def _claimed_identity() -> str | None:
    """Best-effort claimed caller identity for keying.

    Searches the JSON body (flat, ``params.*``, and A2A message metadata),
    then query args, then the ``X-Agent-Id`` header. Self-asserted — see
    the module docstring; the composite key is what makes it safe to use.
    """
    from flask import request

    candidates: List[Dict] = []
    body = request.get_json(silent=True)
    if isinstance(body, dict):
        candidates.append(body)
        params = body.get("params")
        if isinstance(params, dict):
            candidates.append(params)
            msg = params.get("message")
            if isinstance(msg, dict):
                candidates.append(msg)
                meta = msg.get("metadata")
                if isinstance(meta, dict):
                    candidates.append(meta)
    for src in candidates:
        for field in _IDENTITY_FIELDS:
            val = str(src.get(field) or "").strip()
            if val and val.lower() != "anonymous":
                return val
    for field in ("agent_id", "caller_id"):
        val = str(request.args.get(field) or "").strip()
        if val:
            return val
    val = str(request.headers.get("X-Agent-Id") or "").strip()
    return val or None


_ENFORCER = SlidingWindowLimiter()


def reset_a2a_limits() -> None:
    """Clear all recorded hits. Test isolation hook — call between tests."""
    _ENFORCER.reset()


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
    route = f"{request.method.upper()} {rule}"
    policy = policy_for(request.method, str(rule))
    if policy is None:
        return None

    if policy in _IDENTITY_POLICIES and route not in _IP_KEYED_ROUTES:
        client_key = a2a_caller_key(agent_id=_claimed_identity(), ip=_client_ip())
    elif policy == "stream":
        client_key = stream_caller_key()
    else:
        # register / quote / dispute / read / admin — plus the token-authed
        # wardrobe heartbeat (brute-force backstop): no usable caller
        # identity at this layer, so key on client IP — same
        # get_remote_address the app limiter uses.
        client_key = a2a_client_key(ip=_client_ip())

    allowed, info = _ENFORCER.check(policy, client_key)
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
