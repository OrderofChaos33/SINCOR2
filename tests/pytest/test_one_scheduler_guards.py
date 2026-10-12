"""STREAM D tests: per-job hard timeouts + per-job circuit breakers.

Covers:
1. Timeout fires on a hung job (short test-only timeouts, not prod values).
2. A timeout is recorded as an error state and the scheduler survives
   (_safe_run swallows it; breaker counts a failure).
3. The breaker opens after N consecutive failures and skips during cooldown.
4. A success resets the consecutive-failure counter.
5. Half-open trial: one trial run after cooldown, then back to open on failure
   or closed on success.
Also asserts the PER_JOB_TIMEOUTS table matches the founder-decided values.
"""
from __future__ import annotations

import threading
import time

import pytest

import sincor2.scheduler as central
from sincor2.scheduler_guards import (
    DEFAULT_JOB_TIMEOUT_SEC,
    PER_JOB_TIMEOUTS,
    JobCircuitBreaker,
    SchedulerJobTimeoutError,
    guarded_run,
    get_breaker,
    reset_breaker,
    run_with_timeout,
)


@pytest.fixture()
def fresh_breaker(monkeypatch):
    """Isolated shared breaker so tests never pollute each other."""
    reset_breaker()
    yield get_breaker()
    reset_breaker()


@pytest.fixture()
def hung_fns():
    """Factory for truly-hung job functions, with guaranteed teardown release."""
    events = []

    def make():
        ev = threading.Event()
        events.append(ev)

        def hung():
            ev.wait(300)  # blocks far longer than any test timeout
            return "should never get here"

        return hung

    yield make
    for ev in events:
        ev.set()


# ---------------------------------------------------------------------------
# 1. Timeout fires on a hung job (short test-only timeout).
# ---------------------------------------------------------------------------

def test_run_with_timeout_fires_on_hung_job(hung_fns):
    with pytest.raises(SchedulerJobTimeoutError):
        run_with_timeout(hung_fns(), 0.2)


def test_run_with_timeout_returns_result_when_fast():
    assert run_with_timeout(lambda: 42, 5) == 42


def test_run_with_timeout_propagates_job_exception():
    def boom():
        raise ValueError("job blew up")

    with pytest.raises(ValueError, match="job blew up"):
        run_with_timeout(boom, 5)


# ---------------------------------------------------------------------------
# PER_JOB_TIMEOUTS table matches the founder-decided values.
# ---------------------------------------------------------------------------

EXPECTED_TIMEOUTS = {
    "polyclaw_scan": 80,
    "outreach_cycle": 600,
    "content_cycle": 900,
    "launch_cycle": 600,
    "sadas_scan": 300,
    "daily_ops": 900,
    "subscription_reminders": 600,
    "recursive_optimizer": 1800,
    "compliance_checks": 300,
    "launch_review_notify": 300,
    "partner_notify": 300,
}


def test_per_job_timeouts_match_canonical_table():
    assert PER_JOB_TIMEOUTS == EXPECTED_TIMEOUTS
    assert set(PER_JOB_TIMEOUTS) == set(central.CANONICAL_JOB_IDS)


# ---------------------------------------------------------------------------
# 2. Timeout recorded as error state, scheduler survives.
# ---------------------------------------------------------------------------

def test_guarded_run_timeout_is_error_state_and_scheduler_survives(
    fresh_breaker, hung_fns, monkeypatch
):
    """Timeout inside, _safe_run outside: the exception never propagates."""
    monkeypatch.setitem(PER_JOB_TIMEOUTS, "sadas_scan", 0.2)
    calls = []

    def hung():
        calls.append("attempted")
        threading.Event().wait(300)  # daemon worker: never blocks test exit

    # _safe_run must swallow the timeout exception — the scheduler lives.
    central._safe_run("SADAS", lambda: guarded_run("sadas_scan", "SADAS", hung))
    assert calls == ["attempted"]
    snap = fresh_breaker.snapshot("sadas_scan")
    assert snap["failures"] == 1
    assert snap["state"] == "closed"  # threshold not reached yet


def test_guarded_run_exception_counts_as_breaker_failure(fresh_breaker):
    def boom():
        raise RuntimeError("nope")

    with pytest.raises(RuntimeError):
        guarded_run("content_cycle", "Content", boom)
    assert fresh_breaker.snapshot("content_cycle")["failures"] == 1


def test_guarded_run_success_resets_failure_counter(fresh_breaker):
    def boom():
        raise RuntimeError("nope")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            guarded_run("daily_ops", "DailyOps", boom)
    assert fresh_breaker.snapshot("daily_ops")["failures"] == 2
    guarded_run("daily_ops", "DailyOps", lambda: "ok")
    assert fresh_breaker.snapshot("daily_ops")["failures"] == 0


def test_guarded_run_unknown_job_id_uses_default_timeout(monkeypatch):
    seen = {}

    def fake_run_with_timeout(fn, seconds):
        seen["seconds"] = seconds
        return fn()

    monkeypatch.setattr(
        "sincor2.scheduler_guards.run_with_timeout", fake_run_with_timeout
    )
    guarded_run("some_future_job", "Future", lambda: "ok")
    assert seen["seconds"] == DEFAULT_JOB_TIMEOUT_SEC


# ---------------------------------------------------------------------------
# 3. Breaker opens after N failures, skips during cooldown.
# ---------------------------------------------------------------------------

def test_breaker_opens_after_threshold_and_skips_during_cooldown(monkeypatch):
    monkeypatch.setenv("SCHEDULER_CB_THRESHOLD", "3")
    monkeypatch.setenv("SCHEDULER_CB_COOLDOWN_SEC", "60")
    cb = JobCircuitBreaker()
    attempts = []

    def boom():
        attempts.append(1)
        raise RuntimeError("fail")

    for _ in range(3):
        with pytest.raises(RuntimeError):
            if not cb.allow("j"):
                pytest.fail("breaker denied run before threshold reached")
            try:
                boom()
            except RuntimeError:
                cb.record_failure("j")
                raise
    assert cb.snapshot("j")["state"] == "open"
    # During cooldown the job is skipped without running.
    assert cb.allow("j") is False
    assert len(attempts) == 3


def test_env_threshold_override_respected(monkeypatch):
    monkeypatch.setenv("SCHEDULER_CB_THRESHOLD", "2")
    monkeypatch.setenv("SCHEDULER_CB_COOLDOWN_SEC", "60")
    cb = JobCircuitBreaker()
    for _ in range(2):
        cb.record_failure("j")
    assert cb.snapshot("j")["state"] == "open"


# ---------------------------------------------------------------------------
# 5. Half-open trial after cooldown.
# ---------------------------------------------------------------------------

def test_half_open_trial_success_closes_breaker(monkeypatch):
    monkeypatch.setenv("SCHEDULER_CB_THRESHOLD", "1")
    monkeypatch.setenv("SCHEDULER_CB_COOLDOWN_SEC", "0.2")
    cb = JobCircuitBreaker()
    cb.record_failure("j")
    assert cb.snapshot("j")["state"] == "open"
    time.sleep(0.25)
    # One trial run admitted.
    assert cb.allow("j") is True
    # A second concurrent run is NOT admitted while the trial is in flight.
    assert cb.allow("j") is False
    cb.record_success("j")
    snap = cb.snapshot("j")
    assert snap["state"] == "closed"
    assert snap["failures"] == 0
    assert cb.allow("j") is True


def test_half_open_trial_failure_reopens_breaker(monkeypatch):
    monkeypatch.setenv("SCHEDULER_CB_THRESHOLD", "1")
    monkeypatch.setenv("SCHEDULER_CB_COOLDOWN_SEC", "0.2")
    cb = JobCircuitBreaker()
    cb.record_failure("j")
    time.sleep(0.25)
    assert cb.allow("j") is True  # trial admitted
    cb.record_failure("j")  # trial failed
    snap = cb.snapshot("j")
    assert snap["state"] == "open"
    assert cb.allow("j") is False


# ---------------------------------------------------------------------------
# End-to-end via the shared module breaker used by the central scheduler.
# ---------------------------------------------------------------------------

def test_guarded_run_end_to_end_breaker_lifecycle(fresh_breaker, monkeypatch):
    monkeypatch.setenv("SCHEDULER_CB_THRESHOLD", "2")
    monkeypatch.setenv("SCHEDULER_CB_COOLDOWN_SEC", "60")

    def boom():
        raise RuntimeError("fail")

    # Two failures -> breaker opens.
    for _ in range(2):
        with pytest.raises(RuntimeError):
            guarded_run("outreach_cycle", "Outreach", boom)
    assert fresh_breaker.snapshot("outreach_cycle")["state"] == "open"

    # Third run is skipped: func never invoked, returns None, no exception.
    invoked = []
    result = guarded_run("outreach_cycle", "Outreach", lambda: invoked.append(1))
    assert result is None
    assert invoked == []
