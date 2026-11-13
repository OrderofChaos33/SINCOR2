"""Auto-heal resilience: classification, circuit breaker, retry, persistence, health."""

import json
import threading

import pytest

from sincor2.resilience.auto_heal import (
    AutoHealCoordinator,
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    ComponentHealth,
    ComponentStatus,
    ErrorClassification,
    ErrorRecord,
    HealthCheck,
    PersistentErrorTracker,
    RetryPolicy,
    classify_error,
)


class FakeClock:
    def __init__(self, start: float = 1_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class HttpError(Exception):
    def __init__(self, status_code: int, msg: str = ""):
        super().__init__(msg or f"HTTP {status_code}")
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_classify_5xx_is_transient():
    assert classify_error(status_code=500) == ErrorClassification.TRANSIENT
    assert classify_error(status_code=503) == ErrorClassification.TRANSIENT


def test_classify_429_is_transient():
    assert classify_error(status_code=429) == ErrorClassification.TRANSIENT


def test_classify_4xx_is_permanent():
    assert classify_error(status_code=400) == ErrorClassification.PERMANENT
    assert classify_error(status_code=403) == ErrorClassification.PERMANENT
    assert classify_error(status_code=404) == ErrorClassification.PERMANENT


def test_classify_network_exception_is_transient():
    assert classify_error(exc=ConnectionError("down")) == ErrorClassification.TRANSIENT
    assert classify_error(exc=TimeoutError("slow")) == ErrorClassification.TRANSIENT


def test_classify_reads_status_off_exception():
    assert classify_error(exc=HttpError(503)) == ErrorClassification.TRANSIENT
    assert classify_error(exc=HttpError(404)) == ErrorClassification.PERMANENT


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


def test_breaker_starts_closed():
    b = CircuitBreaker("x")
    assert b.state == CircuitState.CLOSED
    assert b.call(lambda: 42) == 42


def test_breaker_opens_after_threshold():
    clock = FakeClock()
    b = CircuitBreaker("x", failure_threshold=3, clock=clock)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            b.call(_boom)
    assert b.state == CircuitState.OPEN
    # fail fast: fn is never invoked while open
    with pytest.raises(CircuitOpenError):
        b.call(_boom)


def test_breaker_half_opens_after_cooldown_and_closes_on_probe():
    clock = FakeClock()
    b = CircuitBreaker("x", failure_threshold=1, cooldown_seconds=60, clock=clock)
    with pytest.raises(RuntimeError):
        b.call(_boom)
    assert b.state == CircuitState.OPEN
    clock.advance(61)
    assert b.state == CircuitState.HALF_OPEN
    assert b.call(lambda: "ok") == "ok"  # probe succeeds
    assert b.state == CircuitState.CLOSED


def test_breaker_reopens_when_probe_fails():
    clock = FakeClock()
    b = CircuitBreaker("x", failure_threshold=1, cooldown_seconds=60, clock=clock)
    with pytest.raises(RuntimeError):
        b.call(_boom)
    clock.advance(61)
    with pytest.raises(RuntimeError):
        b.call(_boom)  # probe fails
    assert b.state == CircuitState.OPEN


def _boom():
    raise RuntimeError("boom")


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------


def test_retry_backoff_delays_increase():
    sleeps: list[float] = []
    policy = RetryPolicy(max_retries=3, base_delay=1.0, jitter=0.0, sleep=sleeps.append)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise HttpError(503)
        return "recovered"

    assert policy.execute(flaky) == "recovered"
    assert sleeps == [1.0, 2.0]  # base * 2^attempt, no jitter


def test_retry_gives_up_after_max_retries():
    policy = RetryPolicy(max_retries=2, base_delay=0.0, sleep=lambda s: None)
    with pytest.raises(HttpError):
        policy.execute(_always_503)


def _always_503():
    raise HttpError(503)


def test_retry_does_not_retry_permanent_errors():
    sleeps: list[float] = []
    policy = RetryPolicy(max_retries=5, sleep=sleeps.append)
    calls = {"n": 0}

    def forbidden():
        calls["n"] += 1
        raise HttpError(403)

    with pytest.raises(HttpError):
        policy.execute(forbidden)
    assert calls["n"] == 1  # single attempt, no retries
    assert sleeps == []


def test_retry_honors_retry_after_header():
    sleeps: list[float] = []

    class RateLimited(Exception):
        def __init__(self):
            super().__init__("slow down")
            self.retry_after = 30

    policy = RetryPolicy(max_retries=1, base_delay=1.0, jitter=0.0, sleep=sleeps.append)
    calls = {"n": 0}

    def limited():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RateLimited()
        return "ok"

    assert policy.execute(limited) == "ok"
    assert sleeps == [30.0]


# ---------------------------------------------------------------------------
# Persistent tracker
# ---------------------------------------------------------------------------


def test_tracker_counts_and_persists_across_reload(tmp_path):
    path = str(tmp_path / "errors.json")
    t1 = PersistentErrorTracker(path)
    t1.record("HttpError", "/v1/a2a/heartbeat", ErrorClassification.TRANSIENT, "503")
    t1.record("HttpError", "/v1/a2a/heartbeat", ErrorClassification.TRANSIENT, "503")
    t2 = PersistentErrorTracker(path)  # reload from disk
    rec = t2.get("HttpError", "/v1/a2a/heartbeat")
    assert rec is not None
    assert rec.count == 2
    assert rec.classification == ErrorClassification.TRANSIENT


def test_tracker_stats_split_by_classification(tmp_path):
    t = PersistentErrorTracker(str(tmp_path / "e.json"))
    t.record("A", "/x", ErrorClassification.TRANSIENT)
    t.record("A", "/x", ErrorClassification.TRANSIENT)
    t.record("B", "/y", ErrorClassification.PERMANENT)
    stats = t.stats()
    assert stats["total_errors"] == 3
    assert stats["by_classification"] == {"transient": 2, "permanent": 1}


def test_tracker_prunes_stale_records(tmp_path):
    clock = FakeClock()
    t = PersistentErrorTracker(str(tmp_path / "e.json"), clock=clock)
    t.record("Old", "/x", ErrorClassification.TRANSIENT)
    clock.advance(10_000)
    t.record("New", "/y", ErrorClassification.TRANSIENT)
    pruned = t.prune(older_than_seconds=5_000)
    assert pruned == 1
    assert t.get("Old", "/x") is None
    assert t.get("New", "/y") is not None


def test_tracker_survives_corrupt_store(tmp_path):
    path = tmp_path / "e.json"
    path.write_text("{not valid json")
    t = PersistentErrorTracker(str(path))  # must not raise
    assert t.stats()["total_errors"] == 0


# ---------------------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------------------


def test_health_aggregate_worst_wins():
    clock = FakeClock()
    hc = HealthCheck(clock=clock)
    hc.register("a", lambda: ComponentStatus("a", ComponentHealth.HEALTHY, clock()))
    hc.register("b", lambda: ComponentStatus("b", ComponentHealth.DEGRADED, clock()))
    hc.register("c", lambda: ComponentStatus("c", ComponentHealth.DOWN, clock()))
    assert hc.aggregate() == ComponentHealth.DOWN


def test_health_raising_check_reports_down():
    hc = HealthCheck()
    hc.register("dead", _raise_check)
    statuses = hc.check_all()
    assert statuses["dead"].health == ComponentHealth.DOWN
    assert "check raised" in statuses["dead"].detail


def _raise_check():
    raise RuntimeError("check exploded")


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


def _coordinator(tmp_path, **kwargs):
    tracker = PersistentErrorTracker(str(tmp_path / "errors.json"))
    policy = RetryPolicy(max_retries=2, base_delay=0.0, sleep=lambda s: None)
    return AutoHealCoordinator(tracker, retry_policy=policy, **kwargs), tracker


def test_coordinator_recovers_after_transient_failures(tmp_path):
    coord, tracker = _coordinator(tmp_path)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise HttpError(503)
        return "healed"

    assert coord.run(flaky, endpoint="/hb") == "healed"
    rec = tracker.get("HttpError", "/hb")
    assert rec is not None and rec.count == 2  # both failures tracked


def test_coordinator_runs_recovery_action_on_exhaustion(tmp_path):
    coord, _ = _coordinator(tmp_path)
    recovered: list = []

    def recovery(exc, record):
        recovered.append((type(exc).__name__, record.count))

    with pytest.raises(HttpError):
        coord.run(_always_503, endpoint="/hb", recovery=recovery)
    assert recovered == [("HttpError", 3)]  # 1 initial + 2 retries, then recovery


def test_coordinator_permanent_error_skips_retry_and_still_recovers(tmp_path):
    coord, tracker = _coordinator(tmp_path)
    calls = {"n": 0}
    recovered: list = []

    def forbidden():
        calls["n"] += 1
        raise HttpError(403)

    with pytest.raises(HttpError):
        coord.run(forbidden, endpoint="/hb", recovery=lambda e, r: recovered.append(r))
    assert calls["n"] == 1  # no retries for permanent errors
    rec = tracker.get("HttpError", "/hb")
    assert rec.classification == ErrorClassification.PERMANENT
    assert len(recovered) == 1


def test_coordinator_circuit_opens_and_endpoint_stats(tmp_path):
    clock = FakeClock()
    tracker = PersistentErrorTracker(str(tmp_path / "e.json"), clock=clock)
    policy = RetryPolicy(max_retries=0, sleep=lambda s: None)
    coord = AutoHealCoordinator(tracker, retry_policy=policy, clock=clock)
    breaker = coord.breaker_for("/hb", failure_threshold=2, cooldown_seconds=60)
    for _ in range(2):
        with pytest.raises(HttpError):
            coord.run(_always_503, endpoint="/hb")
    assert breaker.state == CircuitState.OPEN
    stats = coord.endpoint_stats("/hb")
    assert stats["circuit_state"] == "open"
    assert stats["total_errors"] == 2
    # fail fast while open
    with pytest.raises(CircuitOpenError):
        coord.run(lambda: "x", endpoint="/hb")


def test_error_record_round_trip():
    rec = ErrorRecord(
        error_type="HttpError",
        endpoint="/hb",
        count=5,
        first_seen=100.0,
        last_seen=200.0,
        classification=ErrorClassification.PERMANENT,
        last_message="403",
    )
    restored = ErrorRecord.from_dict(json.loads(json.dumps(rec.to_dict())))
    assert restored == rec


def test_tracker_thread_safe_under_concurrency(tmp_path):
    t = PersistentErrorTracker(str(tmp_path / "e.json"))

    def hammer():
        for _ in range(50):
            t.record("Race", "/x", ErrorClassification.TRANSIENT)

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert t.get("Race", "/x").count == 400
