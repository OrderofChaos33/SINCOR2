"""Tests for durable A2A shared state (P3 wave 15, backlog item 21 / G2.8).

Proves:
  * mode resolution (explicit config wins; unset -> sqlite in prod,
    memory in dev/test — identical in-memory behavior preserved)
  * shared limiter decision parity with the SlidingWindowLimiter spec
  * multi-process counter correctness (two real processes, SQLite)
  * restart persistence (new driver instance sees prior writes)
  * unreachable configured store -> StateStoreUnavailable -> HTTP 503
    denial (fail closed), never a silent counter reset
  * tenant key isolation (adversarial: separator smuggling, quoting
    collisions, over-long keys)
  * idempotency store claim/replay/complete/release + domain isolation

Run: PYTHONPATH=src:. FLASK_ENV=test <venv>/python -m pytest \\
        tests/pytest/test_a2a_shared_state.py -q
"""

from __future__ import annotations

import multiprocessing as mp
import os
import time

import pytest

from sincor2.a2a_rate_limits import SlidingWindowLimiter
from sincor2.a2a_shared_state import (
    IdempotencyStore,
    MemorySharedState,
    RedisSharedState,
    SharedSlidingWindowLimiter,
    SqliteSharedState,
    StateStoreUnavailable,
    build_namespaced_key,
    build_shared_state,
    get_idempotency_store,
    get_shared_state,
    reset_shared_state,
    resolve_state_mode,
)



class FakeClock:
    def __init__(self, start: float = 1_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# "quote" mirrors A2A_RATE_POLICIES["quote"] exactly (60/min, 2000/hour),
# so the parity test can run both limiters against the same policy name.
POLICIES = {"quote": [(60.0, 60.0), (3600.0, 2000.0)]}
POLICY = "quote"
SMALL_POLICIES = {"s": [(60.0, 3.0), (3600.0, 5.0)]}
SMALL = "s"


# ---------------------------------------------------------------------------
# Mode resolution
# ---------------------------------------------------------------------------

def test_resolve_state_mode_explicit_wins(monkeypatch):
    monkeypatch.setenv("A2A_STATE_STORE", "redis")
    assert resolve_state_mode() == "redis"
    monkeypatch.setenv("A2A_STATE_STORE", "sqlite")
    assert resolve_state_mode() == "sqlite"
    monkeypatch.setenv("A2A_STATE_STORE", "memory")
    assert resolve_state_mode() == "memory"


def test_resolve_state_mode_unset_test_env_is_memory(monkeypatch):
    monkeypatch.delenv("A2A_STATE_STORE", raising=False)
    monkeypatch.setenv("FLASK_ENV", "test")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    assert resolve_state_mode() == "memory"


def test_resolve_state_mode_unset_prod_is_sqlite(monkeypatch):
    monkeypatch.delenv("A2A_STATE_STORE", raising=False)
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    assert resolve_state_mode() == "sqlite"


def test_resolve_state_mode_bogus_raises(monkeypatch):
    monkeypatch.setenv("A2A_STATE_STORE", "etcd")
    with pytest.raises(StateStoreUnavailable):
        resolve_state_mode()


def test_build_shared_state_types(tmp_path, monkeypatch):
    assert isinstance(build_shared_state("memory"), MemorySharedState)
    monkeypatch.setenv("A2A_STATE_SQLITE_PATH", str(tmp_path / "t.db"))
    s = build_shared_state("sqlite")
    assert isinstance(s, SqliteSharedState)
    s.close()
    # explicit redis with no URL and no server -> fail fast, never degrade
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_PRIVATE_URL", raising=False)
    with pytest.raises(StateStoreUnavailable):
        build_shared_state("redis")


def test_singleton_resolve_and_reset(monkeypatch, tmp_path):
    reset_shared_state()
    monkeypatch.setenv("A2A_STATE_STORE", "memory")
    one = get_shared_state()
    assert get_shared_state() is one
    reset_shared_state()
    two = get_shared_state()
    assert two is not one
    reset_shared_state()


# ---------------------------------------------------------------------------
# Memory driver CRUD
# ---------------------------------------------------------------------------

def test_memory_driver_crud():
    s = MemorySharedState()
    assert s.get("k") is None
    s.set("k", "v")
    assert s.get("k") == "v"
    s.set("k2", "v2", ttl_seconds=60)
    assert s.get("k2") == "v2"
    assert s.incr("c") == 1
    assert s.incr("c") == 2
    assert s.add_if_absent("n", "1") is True
    assert s.add_if_absent("n", "2") is False
    assert s.get("n") == "1"
    s.delete("n")
    assert s.get("n") is None
    s.clear()
    assert s.get("k") is None
    assert s.incr("c") == 1


# ---------------------------------------------------------------------------
# Shared limiter: parity with the executable spec
# ---------------------------------------------------------------------------

def _drive_both(events):
    """Run the same event script against the spec limiter and the shared
    limiter (memory backend); return both decision lists."""
    clock = FakeClock()
    spec = SlidingWindowLimiter(clock=clock)
    shared = SharedSlidingWindowLimiter(MemorySharedState(), POLICIES,
                                        clock=clock)
    spec_out, shared_out = [], []
    for action in events:
        if action[0] == "advance":
            clock.advance(action[1])
            continue
        _, key = action
        spec_out.append(spec.check(POLICY, key)[0])
        shared_out.append(shared.check(POLICY, key)[0])
    return spec_out, shared_out


def test_shared_limiter_parity_with_spec():
    events = [("hit", "ip:1") for _ in range(60)]
    events += [("hit", "ip:1")]              # 61st: denied by 60s window
    events += [("advance", 61.0)]
    events += [("hit", "ip:1")]              # window slid: allowed
    events += [("hit", "ip:2")]              # other tenant unaffected
    events += [("hit", "ip:1")]
    spec_out, shared_out = _drive_both(events)
    assert spec_out == shared_out
    assert spec_out == [True] * 60 + [False, True, True, True]


def test_shared_limiter_info_shape_and_retry_after():
    clock = FakeClock()
    lim = SharedSlidingWindowLimiter(MemorySharedState(), SMALL_POLICIES,
                                     clock=clock)
    for _ in range(3):
        allowed, _ = lim.check(SMALL, "k")
        assert allowed
    allowed, info = lim.check(SMALL, "k")
    assert not allowed
    assert set(info) == {"retry_after", "window_seconds", "window_max"}
    assert info["retry_after"] > 0
    assert info["window_seconds"] == 60.0
    assert info["window_max"] == 3.0
    clock.advance(61.0)
    allowed, info = lim.check(SMALL, "k")
    assert allowed
    assert info["retry_after"] == 0.0


def test_shared_limiter_reset():
    lim = SharedSlidingWindowLimiter(MemorySharedState(), SMALL_POLICIES)
    for _ in range(3):
        assert lim.check(SMALL, "k")[0]
    assert not lim.check(SMALL, "k")[0]
    lim.reset()
    assert lim.check(SMALL, "k")[0]


# ---------------------------------------------------------------------------
# Tenant key isolation (adversarial)
# ---------------------------------------------------------------------------

def test_namespace_quoting_is_injective():
    # A literal "%3A" in the input must NOT alias to a ":" in another input.
    k1 = build_namespaced_key("a2a", "rl", "t", "agent:a:b")
    k2 = build_namespaced_key("a2a", "rl", "t", "agent:a%3Ab")
    assert k1 != k2
    # Control characters / newlines cannot break out of the namespace.
    k3 = build_namespaced_key("a2a", "rl", "t", "x\n:rl")
    assert ":rl:" not in k3.split("fabric:")[1].replace("%3A", "")
    # Empty parts and over-long keys fail closed, never truncate.
    with pytest.raises(ValueError):
        build_namespaced_key("a2a", "rl", "t", "")
    with pytest.raises(ValueError):
        build_namespaced_key("a2a", "rl", "t", "x" * 600)


def test_tenant_buckets_are_isolated():
    lim = SharedSlidingWindowLimiter(MemorySharedState(), SMALL_POLICIES)
    for _ in range(3):
        assert lim.check(SMALL, "agent:a:b")[0]
    assert not lim.check(SMALL, "agent:a:b")[0]
    # Near-collision inputs get their own buckets.
    assert lim.check(SMALL, "agent:a%3Ab")[0]
    assert lim.check(SMALL, "agent:a")[0]
    assert lim.check(SMALL, "agent:a:b:c")[0]


# ---------------------------------------------------------------------------
# SQLite driver: multi-process correctness + restart persistence
# ---------------------------------------------------------------------------

def _child_incr(path: str, key: str, n: int):
    s = SqliteSharedState(path=path)
    try:
        for _ in range(n):
            s.incr(key)
    finally:
        s.close()


def test_sqlite_multiprocess_counter(tmp_path):
    path = str(tmp_path / "fabric.db")
    seed = SqliteSharedState(path=path)
    seed.close()
    ctx = mp.get_context("fork")
    procs = [ctx.Process(target=_child_incr, args=(path, "mp-counter", 200))
             for _ in range(2)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
        assert p.exitcode == 0
    check = SqliteSharedState(path=path)
    try:
        assert check.get("mp-counter") == "400"
    finally:
        check.close()


def test_sqlite_restart_persistence(tmp_path):
    path = str(tmp_path / "fabric.db")
    policies = {"w": [(60.0, 2.0)]}
    s1 = SqliteSharedState(path=path)
    idem1 = IdempotencyStore(s1, domain="bids")
    try:
        assert s1.incr("boot-count") == 1
        assert s1.incr("boot-count") == 2
        fresh, _ = idem1.claim("key-1", {"status": "accepted"})
        assert fresh is True
        lim1 = SharedSlidingWindowLimiter(s1, policies)
        assert lim1.check("w", "ip:1")[0]
        assert lim1.check("w", "ip:1")[0]
        assert not lim1.check("w", "ip:1")[0]
    finally:
        s1.close()

    # "Restart": brand-new driver instance against the same file.
    s2 = SqliteSharedState(path=path)
    try:
        assert s2.incr("boot-count") == 3
        idem2 = IdempotencyStore(s2, domain="bids")
        fresh, recorded = idem2.claim("key-1", {"status": "whatever"})
        assert fresh is False
        assert recorded == {"status": "accepted"}
        lim2 = SharedSlidingWindowLimiter(s2, policies)
        # The two pre-restart hits are still counted.
        assert not lim2.check("w", "ip:1")[0]
        assert lim2.check("w", "ip:2")[0]
    finally:
        s2.close()


def test_sqlite_concurrent_window_claim_threads(tmp_path):
    path = str(tmp_path / "fabric.db")
    s = SqliteSharedState(path=path)
    policies = {"w": [(3600.0, 10.0)]}
    lim = SharedSlidingWindowLimiter(s, policies)
    results = []

    def hammer():
        results.append(lim.check("w", "shared")[0])

    threads = [__import__("threading").Thread(target=hammer) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    s.close()
    # Exactly 10 allowed; the 2 over-limit are denied — never over-admitted.
    assert sorted(results) == [False, False] + [True] * 10


# ---------------------------------------------------------------------------
# Idempotency store
# ---------------------------------------------------------------------------

def test_idempotency_claim_replay_complete_release():
    store = IdempotencyStore(MemorySharedState(), domain="bids")
    fresh, recorded = store.claim("k1", {"status": "processing"})
    assert fresh is True
    assert recorded == {"status": "processing"}
    # Replay returns the recorded result verbatim; the write ran once.
    fresh, recorded = store.claim("k1", {"status": "different"})
    assert fresh is False
    assert recorded == {"status": "processing"}
    store.complete("k1", {"status": "settled", "tx": "0xabc"})
    assert store.get("k1") == {"status": "settled", "tx": "0xabc"}
    fresh, _ = store.claim("k1", {"status": "x"})
    assert fresh is False
    store.release("k1")
    assert store.get("k1") is None
    fresh, _ = store.claim("k1", {"status": "retry"})
    assert fresh is True


def test_idempotency_domain_isolation():
    state = MemorySharedState()
    a = IdempotencyStore(state, domain="bids")
    b = IdempotencyStore(state, domain="stake")
    assert a.claim("same-key", {"d": "a"})[0] is True
    assert b.claim("same-key", {"d": "b"})[0] is True
    assert a.get("same-key") == {"d": "a"}
    assert b.get("same-key") == {"d": "b"}


def test_idempotency_key_validation():
    store = IdempotencyStore(MemorySharedState())
    for bad in ("", "x" * 257, "has\nnewline", "has\ttab"):
        with pytest.raises(ValueError):
            store.claim(bad, {})


def test_get_idempotency_store_singleton(monkeypatch):
    reset_shared_state()
    monkeypatch.setenv("A2A_STATE_STORE", "memory")
    try:
        a = get_idempotency_store("bids")
        b = get_idempotency_store("bids")
        assert a is b
        assert get_idempotency_store("stake") is not b
    finally:
        reset_shared_state()


# ---------------------------------------------------------------------------
# Redis driver (fakeredis) + fail-closed behavior
# ---------------------------------------------------------------------------
# fakeredis is a test-only stand-in for a real Redis server (none runs in
# CI). Tests that need it skip individually so the SQLite/memory suites
# always run.


def _fake_redis_state():
    fakeredis = pytest.importorskip("fakeredis")
    client = fakeredis.FakeRedis(decode_responses=True)
    return RedisSharedState(_client=client), client


def test_redis_driver_crud_and_windows():
    s, _ = _fake_redis_state()
    s.set("k", "v", ttl_seconds=60)
    assert s.get("k") == "v"
    assert s.incr("c") == 1
    assert s.incr("c") == 2
    assert s.add_if_absent("n", "1") is True
    assert s.add_if_absent("n", "2") is False
    lim = SharedSlidingWindowLimiter(s, SMALL_POLICIES)
    for _ in range(3):
        assert lim.check(SMALL, "rk")[0]
    allowed, info = lim.check(SMALL, "rk")
    assert not allowed and info["retry_after"] > 0
    s.clear()
    assert s.get("k") is None


def test_redis_driver_idempotency():
    s, _ = _fake_redis_state()
    store = IdempotencyStore(s, domain="pool")
    assert store.claim("k", {"ok": True})[0] is True
    fresh, recorded = store.claim("k", {"ok": False})
    assert fresh is False and recorded == {"ok": True}


def test_redis_unreachable_config_fails_fast():
    # No server on port 9: constructor must raise, never degrade.
    with pytest.raises(StateStoreUnavailable):
        RedisSharedState(redis_url="redis://127.0.0.1:9")


def test_redis_mid_request_failure_denies():
    # Client that dies after construction -> every op raises
    # StateStoreUnavailable (deny), never a silent allow.
    import redis as redis_mod
    s, _ = _fake_redis_state()
    dead = redis_mod.Redis.from_url("redis://127.0.0.1:9",
                                    socket_connect_timeout=1)
    s._r = dead
    lim = SharedSlidingWindowLimiter(s, POLICIES)
    with pytest.raises(StateStoreUnavailable):
        lim.check("quote", "k")
    with pytest.raises(StateStoreUnavailable):
        s.incr("c")
    idem = IdempotencyStore(s)
    with pytest.raises(StateStoreUnavailable):
        idem.claim("k", {})


def test_watcherror_exhaustion_denies():
    # Write contention that never resolves -> deny, not silent allow.
    s, _ = _fake_redis_state()

    class PoisonPipe:
        def watch(self, *a, **k):
            raise type("WatchError", (Exception,), {})("conflict")

    class PoisonClient:
        def pipeline(self, **k):
            return PoisonPipe()

    s._r = PoisonClient()
    lim = SharedSlidingWindowLimiter(s, POLICIES)
    with pytest.raises(StateStoreUnavailable):
        lim.check("quote", "k")


def test_rate_limit_check_returns_503_on_store_outage(monkeypatch):
    """The Flask before_request handler converts an unreachable store
    into an HTTP 503 denial (fail closed), not a 429 and not a pass."""
    import redis as redis_mod
    from flask import Flask

    import sincor2.a2a_rate_limits as rl_mod

    s, _ = _fake_redis_state()
    s._r = redis_mod.Redis.from_url("redis://127.0.0.1:9",
                                    socket_connect_timeout=1)
    dead_limiter = SharedSlidingWindowLimiter(s, rl_mod._policy_tuples())
    monkeypatch.setattr(rl_mod, "_ENFORCER", dead_limiter)

    app = Flask(__name__)

    @app.route("/v1/a2a/register", methods=["POST"])
    def _register():
        return {"ok": True}

    app.before_request(rl_mod.a2a_rate_limit_check)
    client = app.test_client()
    r = client.post("/v1/a2a/register", json={"agent_id": "x"})
    assert r.status_code == 503, r.status_code
    body = r.get_json()
    assert body["error"] == "state_store_unavailable"
    assert r.headers.get("Retry-After") == "5"


def test_redis_url_redaction():
    redacted = RedisSharedState._redact(
        "redis://:s3cret@redis.example.com:6379/0")
    assert "s3cret" not in redacted
    assert "redis.example.com" in redacted
