"""Wave 16: rate-limit coverage (G2.9) + SSE stream hardening (G2.10).

Proves:
  1. Every mutating/high-value A2A route has an explicit tier in
     ENDPOINT_POLICY (settle, tasks/send, tasks/cancel, task create, close,
     proofs, heartbeat, stream, leaderboard, pricing, v1_chain, and the
     four admin sponsored-stake/recovery routes).
  2. The new tiers are actually wired: hammering each route past its window
     yields a 429 JSON body with Retry-After (never a business-logic leak).
  3. Identity-keyed tiers use the composite agent|ip key: a spoofer cannot
     exhaust another agent's bucket (targeted liveness/bidding DoS).
  4. The store seam: SlidingWindowLimiter delegates to a RateLimitStore;
     a custom store can back it (wave 15's seam).
  5. v1_stream: unauthenticated -> 401; operator token or registered
     agent_id -> 200; per-caller concurrency cap -> 503 on the 4th
     concurrent connection; slots are always released (even on 100-way
     races and generator close).

Run: PYTHONPATH=src:. <venv>/python -m pytest tests/pytest/test_wave16_rate_limits_stream.py -q
"""

from __future__ import annotations

import threading

import pytest
from flask import Flask

from sincor2.a2a_inbound import get_fabric, reset_fabric
from sincor2.a2a_inbound import register as register_inbound
from sincor2.a2a_inbound_market import (
    acquire_stream_slot,
    release_stream_slot,
    reset_stream_slots,
    stream_slot_count,
)
from sincor2.a2a_rate_limits import (
    A2A_RATE_POLICIES,
    ENDPOINT_POLICY,
    STREAM_MAX_PER_CALLER,
    MemoryRateLimitStore,
    SlidingWindowLimiter,
    a2a_caller_key,
    check_stream_auth,
    policy_for,
    reset_a2a_limits,
    stream_caller_key,
)
from sincor2.onchain.stake_ledger import reset_stake_ledger


class FakeClock:
    def __init__(self, start: float = 2_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def client(tmp_path):
    reset_fabric()
    reset_a2a_limits()
    reset_stream_slots()
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    app = Flask(__name__)
    register_inbound(app)
    from sincor2.a2a_integration import A2ARouter
    app.register_blueprint(A2ARouter().blueprint)
    app.config["TESTING"] = True
    yield app.test_client()
    reset_stream_slots()
    reset_a2a_limits()


def drain(limiter, policy, key, n):
    for _ in range(n):
        allowed, _ = limiter.check(policy, key)
        assert allowed


# ---------------------------------------------------------------------------
# 1. Coverage: every high-value route has an explicit tier (G2.9)
# ---------------------------------------------------------------------------

def test_every_high_value_route_has_explicit_tier():
    required = {
        # money path + task messaging (A2ARouter)
        ("POST", "/api/a2a/settle"): "settle",
        ("POST", "/api/a2a/tasks/send"): "task_msg",
        ("POST", "/api/a2a/tasks/cancel"): "task_msg",
        ("POST", "/api/a2a"): "task_msg",
        # task lifecycle (market blueprint)
        ("POST", "/v1/a2a/tasks"): "task_write",
        ("DELETE", "/v1/a2a/tasks/<task_id>"): "task_write",
        ("POST", "/v1/a2a/tasks/<task_id>/close"): "task_write",
        ("POST", "/v1/a2a/proofs"): "task_write",
        ("POST", "/api/v1/proofs"): "task_write",
        # heartbeat (mount + wardrobe)
        ("POST", "/v1/a2a/heartbeat"): "heartbeat",
        ("POST", "/api/a2a/heartbeat"): "heartbeat",
        # SSE stream
        ("GET", "/v1/a2a/stream"): "stream",
        ("GET", "/api/v1/stream"): "stream",
        # high-value reads
        ("GET", "/api/a2a/leaderboard"): "read",
        ("GET", "/api/a2a/pricing"): "read",
        ("GET", "/v1/a2a/chain"): "read",
        ("GET", "/api/a2a/agents"): "read",
        ("GET", "/api/a2a/tasks/<task_id>"): "read",
        # admin backstops (credential-gated)
        ("POST", "/v1/a2a/admin/sponsored-stake"): "admin",
        ("GET", "/v1/a2a/admin/sponsored-stake/<agent_id>"): "admin",
        ("POST", "/v1/a2a/admin/recovery/sponsor"): "admin",
        ("GET", "/v1/a2a/admin/recovery/status"): "admin",
    }
    for (method, path), want in required.items():
        got = policy_for(method, path)
        assert got == want, f"{method} {path}: want tier {want!r}, got {got!r}"


def test_new_policies_have_sane_windows():
    # settle is the strictest write tier; heartbeat is generous for liveness.
    assert A2A_RATE_POLICIES["settle"][0].max_hits <= A2A_RATE_POLICIES["task_write"][0].max_hits
    assert A2A_RATE_POLICIES["heartbeat"][0].max_hits >= 10  # TTL/2 cadence headroom
    assert A2A_RATE_POLICIES["stream"][0].max_hits >= 1
    for policy in ("settle", "task_write", "task_msg", "heartbeat", "stream", "admin"):
        assert policy in A2A_RATE_POLICIES
        assert policy in set(ENDPOINT_POLICY.values())


# ---------------------------------------------------------------------------
# 2. New tiers trigger at the documented boundaries (unit, injected clock)
# ---------------------------------------------------------------------------

def test_settle_tier_triggers_at_11th_per_minute():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(clock=clock)
    key = a2a_caller_key(agent_id="poster-1", ip="203.0.113.10")
    drain(limiter, "settle", key, 10)
    allowed, info = limiter.check("settle", key)
    assert not allowed
    assert info["window_seconds"] == 60.0
    clock.advance(61)
    assert limiter.check("settle", key)[0]


def test_task_write_tier_triggers_at_21st_per_minute():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(clock=clock)
    key = a2a_caller_key(agent_id="poster-2", ip="203.0.113.11")
    drain(limiter, "task_write", key, 20)
    allowed, _ = limiter.check("task_write", key)
    assert not allowed


def test_heartbeat_tier_triggers_at_21st_per_minute():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(clock=clock)
    key = a2a_caller_key(agent_id="agent-hb", ip="203.0.113.12")
    drain(limiter, "heartbeat", key, 20)
    allowed, _ = limiter.check("heartbeat", key)
    assert not allowed


def test_stream_tier_triggers_at_7th_per_minute():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(clock=clock)
    key = a2a_caller_key(agent_id="agent-stream", ip="203.0.113.13")
    drain(limiter, "stream", key, 6)
    allowed, _ = limiter.check("stream", key)
    assert not allowed


# ---------------------------------------------------------------------------
# 3. Composite keying: no cross-caller bucket poisoning (adversarial)
# ---------------------------------------------------------------------------

def test_composite_key_format():
    assert a2a_caller_key(agent_id="a1", ip="10.0.0.1") == "agent:a1|ip:10.0.0.1"
    assert a2a_caller_key(ip="10.0.0.1") == "ip:10.0.0.1"
    assert a2a_caller_key() == "ip:unknown"
    assert a2a_caller_key(wallet="0xABC", ip="10.0.0.1") == "wallet:0xabc|ip:10.0.0.1"


def test_spoofer_cannot_exhaust_victim_heartbeat_bucket():
    """Attacker claims victim's agent_id from their own IP: the victim's
    bucket (keyed to the victim's IP) must be untouched."""
    clock = FakeClock()
    limiter = SlidingWindowLimiter(clock=clock)
    victim = a2a_caller_key(agent_id="victim-agent", ip="192.0.2.50")
    spoofer = a2a_caller_key(agent_id="victim-agent", ip="198.51.100.99")
    assert victim != spoofer
    # Attacker burns 20 hits spoofing the victim's id from the attacker's IP.
    drain(limiter, "heartbeat", spoofer, 20)
    allowed, _ = limiter.check("heartbeat", spoofer)
    assert not allowed  # attacker is capped
    # The real victim, on its own IP, is unaffected.
    allowed, _ = limiter.check("heartbeat", victim)
    assert allowed


def test_agents_behind_one_nat_stay_isolated():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(clock=clock)
    drain(limiter, "bid", a2a_caller_key(agent_id="a1", ip="10.0.0.1"), 30)
    allowed, _ = limiter.check("bid", a2a_caller_key(agent_id="a1", ip="10.0.0.1"))
    assert not allowed
    allowed, _ = limiter.check("bid", a2a_caller_key(agent_id="a2", ip="10.0.0.1"))
    assert allowed


# ---------------------------------------------------------------------------
# 4. Store seam: wave 15 can back the limiter durably
# ---------------------------------------------------------------------------

class DictStore:
    """Minimal custom store proving the seam: delegates to a plain dict."""

    def __init__(self):
        self.buckets = {}

    def check(self, policy, client_key, windows, now):
        inner = MemoryRateLimitStore()
        # Reuse the memory logic per bucket to prove delegation works;
        # the point is the limiter calls *this* object, not its default.
        bucket = self.buckets.setdefault((policy, client_key), MemoryRateLimitStore())
        assert bucket is not inner
        return bucket.check(policy, client_key, windows, now)

    def clear(self):
        self.buckets.clear()


def test_limiter_delegates_to_custom_store():
    clock = FakeClock()
    store = DictStore()
    limiter = SlidingWindowLimiter(clock=clock, store=store)
    key = a2a_caller_key(agent_id="s1", ip="203.0.113.20")
    drain(limiter, "settle", key, 10)
    allowed, _ = limiter.check("settle", key)
    assert not allowed
    assert ("settle", key) in store.buckets
    limiter.reset()
    assert store.buckets == {}


def test_memory_store_is_thread_safe():
    store = MemoryRateLimitStore()
    limiter = SlidingWindowLimiter(store=store)
    key = a2a_caller_key(agent_id="t", ip="203.0.113.21")
    results = []

    def hammer():
        for _ in range(50):
            allowed, _ = limiter.check("quote", key)
            results.append(allowed)

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # 200 racing hits against a 60/min window: exactly 60 allowed, no crash,
    # no over-admit.
    assert len(results) == 200
    assert sum(results) == 60


# ---------------------------------------------------------------------------
# 5. Stream auth + concurrency cap (G2.10) — integration
# ---------------------------------------------------------------------------

def _register_agent(agent_id):
    from sincor2.a2a_inbound_ext import register_agent_record

    return register_agent_record({"agent_id": agent_id, "capability_tags": ["t"]})


def _fast_stream(monkeypatch):
    """Make the SSE generator exit immediately (no 240s hold in tests)."""
    import sincor2.a2a_inbound_market as market

    monkeypatch.setattr(market, "STREAM_IDLE_TIMEOUT_S", 0)


def test_stream_unauthenticated_is_401(client):
    r = client.get("/v1/a2a/stream")
    assert r.status_code == 401, r.status_code
    body = r.get_json()
    assert body["status"] == 401


def test_stream_operator_token_is_200_and_releases_slot(client, monkeypatch):
    monkeypatch.setenv("AGENT_HEARTBEAT_TOKEN", "op-token-123")
    _fast_stream(monkeypatch)
    r = client.get("/v1/a2a/stream", headers={"X-Sincor-Heartbeat": "op-token-123"})
    assert r.status_code == 200, r.status_code
    assert "text/event-stream" in (r.mimetype or r.content_type)
    r.get_data()  # consume the stream, as a WSGI server would
    # The generator's finally block freed the slot when the stream ended.
    assert stream_slot_count("operator|ip:127.0.0.1") == 0


def test_stream_bearer_token_variant_is_200(client, monkeypatch):
    monkeypatch.setenv("AGENT_HEARTBEAT_TOKEN", "op-token-abc")
    _fast_stream(monkeypatch)
    r = client.get("/v1/a2a/stream", headers={"Authorization": "Bearer op-token-abc"})
    assert r.status_code == 200, r.status_code


def test_stream_wrong_token_is_401(client, monkeypatch):
    monkeypatch.setenv("AGENT_HEARTBEAT_TOKEN", "op-token-123")
    r = client.get("/v1/a2a/stream", headers={"X-Sincor-Heartbeat": "wrong"})
    assert r.status_code == 401


def test_stream_registered_agent_is_200(client, monkeypatch):
    _register_agent("rl-stream-agent")
    _fast_stream(monkeypatch)
    r = client.get("/v1/a2a/stream", query_string={"agent_id": "rl-stream-agent"})
    assert r.status_code == 200, r.status_code
    r.get_data()  # consume the stream, as a WSGI server would
    assert stream_slot_count("agent:rl-stream-agent|ip:127.0.0.1") == 0


def test_stream_unknown_agent_is_401(client):
    r = client.get("/v1/a2a/stream", query_string={"agent_id": "no-such-agent"})
    assert r.status_code == 401


def test_stream_concurrency_cap_is_503(client, monkeypatch):
    """Pre-fill the caller's slots: the 4th concurrent connection is
    rejected with 503 + Retry-After instead of holding a 4th worker."""
    monkeypatch.setenv("AGENT_HEARTBEAT_TOKEN", "op-token-123")
    key = "operator|ip:127.0.0.1"
    for _ in range(STREAM_MAX_PER_CALLER):
        assert acquire_stream_slot(key)
    try:
        r = client.get("/v1/a2a/stream", headers={"X-Sincor-Heartbeat": "op-token-123"})
        assert r.status_code == 503, r.status_code
        assert r.get_json()["error"] == "stream_busy"
        assert r.headers.get("Retry-After") == "5"
    finally:
        for _ in range(STREAM_MAX_PER_CALLER):
            release_stream_slot(key)
    assert stream_slot_count(key) == 0


def test_stream_cap_does_not_starve_other_callers(client, monkeypatch):
    """Caller A at cap must not affect caller B's slots."""
    monkeypatch.setenv("AGENT_HEARTBEAT_TOKEN", "op-token-123")
    _register_agent("rl-stream-b")
    key_a = "operator|ip:127.0.0.1"
    for _ in range(STREAM_MAX_PER_CALLER):
        assert acquire_stream_slot(key_a)
    try:
        import sincor2.a2a_inbound_market as market

        monkeypatch.setattr(market, "STREAM_IDLE_TIMEOUT_S", 0)
        r = client.get("/v1/a2a/stream", query_string={"agent_id": "rl-stream-b"})
        assert r.status_code == 200, r.status_code
        r.get_data()
    finally:
        for _ in range(STREAM_MAX_PER_CALLER):
            release_stream_slot(key_a)


def test_stream_slot_race_never_over_admits():
    """100 threads race for one caller's slots: at most MAX_PER_CALLER win,
    and every acquired slot is releasable."""
    reset_stream_slots()
    key = "race|ip:127.0.0.1"
    wins = []
    lock = threading.Lock()

    def grab():
        ok = acquire_stream_slot(key)
        with lock:
            wins.append(ok)

    threads = [threading.Thread(target=grab) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(wins) == STREAM_MAX_PER_CALLER
    assert stream_slot_count(key) == STREAM_MAX_PER_CALLER
    for _ in range(STREAM_MAX_PER_CALLER):
        release_stream_slot(key)
    assert stream_slot_count(key) == 0
    # Over-release is safe (no negative slots, no KeyError).
    release_stream_slot(key)
    assert stream_slot_count(key) == 0


def test_stream_connection_rate_is_limited(client, monkeypatch):
    """7th stream connection inside a minute -> 429 with the stream tier."""
    monkeypatch.setenv("AGENT_HEARTBEAT_TOKEN", "op-token-123")
    _fast_stream(monkeypatch)
    headers = {"X-Sincor-Heartbeat": "op-token-123"}
    for _ in range(6):
        r = client.get("/v1/a2a/stream", headers=headers)
        assert r.status_code == 200, r.status_code
        r.get_data()
    r = client.get("/v1/a2a/stream", headers=headers)
    assert r.status_code == 429, r.status_code
    assert r.get_json()["policy"] == "stream"
    assert r.headers.get("Retry-After")


def test_check_stream_auth_key_shapes(client, monkeypatch):
    """stream_caller_key agrees with the view's slot key for each credential."""
    monkeypatch.setenv("AGENT_HEARTBEAT_TOKEN", "op-token-123")
    _register_agent("rl-key-agent")
    app = client.application
    with app.test_request_context("/v1/a2a/stream", headers={"X-Sincor-Heartbeat": "op-token-123"}):
        ok, key = check_stream_auth()
        assert ok and key == "operator|ip:127.0.0.1"
        assert stream_caller_key() == key
    with app.test_request_context("/v1/a2a/stream?agent_id=rl-key-agent"):
        ok, key = check_stream_auth()
        assert ok and key == "agent:rl-key-agent|ip:127.0.0.1"
    with app.test_request_context("/v1/a2a/stream"):
        ok, key = check_stream_auth()
        assert not ok and key == "ip:127.0.0.1"


# ---------------------------------------------------------------------------
# 6. New tiers wired on the real routes
# ---------------------------------------------------------------------------

def test_heartbeat_tier_wired_429(client):
    for _ in range(20):
        r = client.post("/v1/a2a/heartbeat", json={"agent_id": "ghost"})
        assert r.status_code == 404, r.status_code
    r = client.post("/v1/a2a/heartbeat", json={"agent_id": "ghost"})
    assert r.status_code == 429
    assert r.get_json()["policy"] == "heartbeat"
    assert r.headers.get("Retry-After")


def test_proofs_tier_wired_429(client):
    for _ in range(20):
        r = client.post(
            "/v1/a2a/proofs",
            json={"task_id": "t", "agent_id": "a", "receipt_hash": "h"},
        )
        assert r.status_code != 429, r.status_code
    r = client.post(
        "/v1/a2a/proofs",
        json={"task_id": "t", "agent_id": "a", "receipt_hash": "h"},
    )
    assert r.status_code == 429
    assert r.get_json()["policy"] == "task_write"


def test_task_create_tier_wired_429(client):
    for _ in range(20):
        r = client.post(
            "/v1/a2a/tasks",
            json={"skill": "lead-enrichment", "bounty_axm": 1.5,
                  "poster_id": "poster-x"},
        )
        assert r.status_code in (200, 201, 400), r.status_code
    r = client.post(
        "/v1/a2a/tasks",
        json={"skill": "lead-enrichment", "bounty_axm": 1.5,
              "poster_id": "poster-x"},
    )
    assert r.status_code == 429
    assert r.get_json()["policy"] == "task_write"


def test_settle_tier_wired_429(client):
    for _ in range(10):
        r = client.post(
            "/api/a2a/settle",
            json={"task_id": "x", "tx_hash": "0x" + "ab" * 32,
                  "caller_id": "settler-1"},
        )
        assert r.status_code != 429, r.status_code
    r = client.post(
        "/api/a2a/settle",
        json={"task_id": "x", "tx_hash": "0x" + "ab" * 32,
              "caller_id": "settler-1"},
    )
    assert r.status_code == 429
    assert r.get_json()["policy"] == "settle"


def test_tasks_send_tier_wired_429(client):
    for _ in range(30):
        r = client.post("/api/a2a/tasks/send", json={"bogus": True})
        assert r.status_code != 429, r.status_code
    r = client.post("/api/a2a/tasks/send", json={"bogus": True})
    assert r.status_code == 429
    assert r.get_json()["policy"] == "task_msg"


def test_admin_tier_wired_429(client):
    for _ in range(30):
        r = client.post("/v1/a2a/admin/recovery/sponsor", json={})
        assert r.status_code in (401, 403), r.status_code
    r = client.post("/v1/a2a/admin/recovery/sponsor", json={})
    assert r.status_code == 429
    assert r.get_json()["policy"] == "admin"


def test_unmapped_routes_still_not_limited(client):
    # Discovery/docs stay public and unlimited.
    for _ in range(10):
        r = client.get("/.well-known/agent-card.json")
        assert r.status_code != 429, r.status_code


def test_wardrobe_heartbeat_bruteforce_backstop_is_ip_keyed():
    """The token-authed wardrobe heartbeat must not let an attacker evade
    the tier by rotating the claimed agent_id: 20 rapid attempts from one
    IP -> 429 even with a fresh agent_id every time."""
    from sincor2.mvp_blueprints.wardrobe import bp as wardrobe_bp

    reset_a2a_limits()
    app = Flask(__name__)
    app.register_blueprint(wardrobe_bp)
    app.config["TESTING"] = True
    c = app.test_client()
    for i in range(20):
        r = c.post("/api/a2a/heartbeat", json={"agent_id": f"rot-{i}"})
        assert r.status_code == 401, r.status_code  # no token; still counted
    r = c.post("/api/a2a/heartbeat", json={"agent_id": "rot-fresh"})
    assert r.status_code == 429, r.status_code
    assert r.get_json()["policy"] == "heartbeat"
    reset_a2a_limits()
