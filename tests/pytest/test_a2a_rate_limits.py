"""Tests for A2A rate-limit policies (Stream 4).

Proves the policies in ``src/sincor2/a2a_rate_limits.py`` trigger correctly:
registration spam is stopped, bid/commit spam is bounded per agent, quote
scraping is bounded per IP, windows expire, and clients are isolated.

These are pure unit tests — no Flask, no Redis, no wall-clock sleeps. The
limiter takes an injected clock; tests advance it manually.

Run: PYTHONPATH=src:. <venv>/python -m pytest tests/pytest/test_a2a_rate_limits.py -q
"""

import pytest

from sincor2.a2a_rate_limits import (
    A2A_RATE_POLICIES,
    ENDPOINT_POLICY,
    SlidingWindowLimiter,
    a2a_client_key,
    flask_limit_strings,
    policy_for,
)


class FakeClock:
    def __init__(self, start: float = 1_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def clock():
    return FakeClock()


@pytest.fixture()
def limiter(clock):
    return SlidingWindowLimiter(clock=clock)


def drain(limiter, policy, key, n):
    """Consume n allowed hits; assert each was allowed."""
    for _ in range(n):
        allowed, _ = limiter.check(policy, key)
        assert allowed


# ---------------------------------------------------------------------------
# 1. Registration spam: 5/hour, 20/day per IP — the strictest policy
# ---------------------------------------------------------------------------

def test_register_policy_triggers_at_sixth_per_hour(limiter):
    key = a2a_client_key(ip="203.0.113.7")
    drain(limiter, "register", key, 5)
    allowed, info = limiter.check("register", key)
    assert not allowed
    assert info["retry_after"] > 0
    assert info["window_seconds"] == 3600.0


def test_register_hourly_window_expires(limiter, clock):
    key = a2a_client_key(ip="203.0.113.7")
    drain(limiter, "register", key, 5)
    allowed, _ = limiter.check("register", key)
    assert not allowed
    clock.advance(3601)
    allowed, _ = limiter.check("register", key)
    assert allowed


def test_register_daily_cap_survives_hourly_reset(limiter, clock):
    """20/day: spreading 5/hour across 4 hours then hitting the daily cap."""
    key = a2a_client_key(ip="203.0.113.8")
    for _ in range(4):
        drain(limiter, "register", key, 5)
        clock.advance(3601)
    # 20 hits in the day now; the 21st must be denied by the daily window.
    allowed, info = limiter.check("register", key)
    assert not allowed
    assert info["window_seconds"] == 86400.0


# ---------------------------------------------------------------------------
# 2. Bid/commit spam: 30/min, 300/hour — keyed per AGENT, not per IP
# ---------------------------------------------------------------------------

def test_bid_policy_triggers_at_31st_per_minute(limiter):
    key = a2a_client_key(agent_id="agent-007", ip="203.0.113.9")
    drain(limiter, "bid", key, 30)
    allowed, info = limiter.check("bid", key)
    assert not allowed
    assert info["window_seconds"] == 60.0


def test_bid_minute_window_expires(limiter, clock):
    key = a2a_client_key(agent_id="agent-007", ip="203.0.113.9")
    drain(limiter, "bid", key, 30)
    clock.advance(61)
    allowed, _ = limiter.check("bid", key)
    assert allowed


def test_bid_key_prefers_agent_over_ip():
    """Two agents behind one NAT egress must not share a bucket."""
    assert a2a_client_key(agent_id="a1", ip="10.0.0.1") == "agent:a1"
    assert a2a_client_key(agent_id="a2", ip="10.0.0.1") == "agent:a2"
    assert a2a_client_key(agent_id="a1", ip="10.0.0.1") != a2a_client_key(agent_id="a2", ip="10.0.0.1")


def test_bid_agents_sharing_ip_are_isolated(limiter):
    drain(limiter, "bid", a2a_client_key(agent_id="a1", ip="10.0.0.1"), 30)
    allowed, _ = limiter.check("bid", a2a_client_key(agent_id="a1", ip="10.0.0.1"))
    assert not allowed
    # agent a2 on the SAME ip is unaffected
    allowed, _ = limiter.check("bid", a2a_client_key(agent_id="a2", ip="10.0.0.1"))
    assert allowed


# ---------------------------------------------------------------------------
# 3. Quote scraping: 60/min, 2000/hour per IP
# ---------------------------------------------------------------------------

def test_quote_policy_triggers_at_61st_per_minute(limiter):
    key = a2a_client_key(ip="198.51.100.23")
    drain(limiter, "quote", key, 60)
    allowed, info = limiter.check("quote", key)
    assert not allowed
    assert info["window_seconds"] == 60.0


def test_quote_legitimate_bidder_unaffected(limiter):
    """A real bidder needs ~1 quote per auction; 60/min is generous."""
    key = a2a_client_key(ip="198.51.100.24")
    for _ in range(10):
        allowed, _ = limiter.check("quote", key)
        assert allowed


# ---------------------------------------------------------------------------
# 4. Policy strictness ordering: register << quote
# ---------------------------------------------------------------------------

def test_register_stricter_than_quote(limiter):
    key = a2a_client_key(ip="203.0.113.99")
    # 6 events: register denies, quote allows.
    drain(limiter, "register", key, 5)
    allowed, _ = limiter.check("register", key)
    assert not allowed
    for _ in range(6):
        allowed, _ = limiter.check("quote", key)
        assert allowed


# ---------------------------------------------------------------------------
# 5. Client isolation + retry_after sanity
# ---------------------------------------------------------------------------

def test_exhausted_client_does_not_affect_others(limiter):
    drain(limiter, "register", a2a_client_key(ip="203.0.113.1"), 5)
    allowed, _ = limiter.check("register", a2a_client_key(ip="203.0.113.2"))
    assert allowed


def test_retry_after_bounded_by_window(limiter):
    key = a2a_client_key(ip="203.0.113.5")
    drain(limiter, "register", key, 5)
    _, info = limiter.check("register", key)
    assert 0 < info["retry_after"] <= 3600.0


def test_unknown_ip_falls_back_to_named_bucket():
    assert a2a_client_key() == "ip:unknown"
    assert a2a_client_key(ip=None) == "ip:unknown"


# ---------------------------------------------------------------------------
# 6. Coverage: every A2A mutation route has a policy; wiring strings parse
# ---------------------------------------------------------------------------

def test_every_mutation_route_has_policy():
    mutations = [
        ("POST", "/v1/a2a/register"),
        ("POST", "/v1/a2a/bids"),
        ("POST", "/v1/a2a/bids/commit"),
        ("POST", "/v1/a2a/bids/reveal"),
        ("POST", "/v1/a2a/disputes"),
        ("GET", "/api/a2a/quote"),
        ("POST", "/api/a2a/quote"),
    ]
    for method, path in mutations:
        assert policy_for(method, path) is not None, f"no policy for {method} {path}"


def test_flask_limit_strings_well_formed():
    strings = flask_limit_strings("bid")
    assert strings == ["30 per minute", "300 per hour"]
    strings = flask_limit_strings("register")
    assert strings == ["5 per hour", "20 per day"]
    # every policy converts without error
    for policy in A2A_RATE_POLICIES:
        for s in flask_limit_strings(policy):
            count, _, unit = s.partition(" per ")
            assert count.isdigit()
            assert unit.split()[0].isdigit() or unit.split()[0] in {"minute", "hour", "day", "second", "seconds", "minutes", "hours", "days"}


def test_dispute_policy_is_backstop_not_blocker(limiter):
    """Adjudicator-signed disputes are already auth-bounded; the limiter
    must not interfere with legitimate low-volume use."""
    key = a2a_client_key(ip="203.0.113.77")
    for _ in range(5):
        allowed, _ = limiter.check("dispute", key)
        assert allowed
    allowed, _ = limiter.check("dispute", key)
    assert not allowed
