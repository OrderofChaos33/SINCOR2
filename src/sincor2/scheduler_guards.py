"""Per-job guards for the central scheduler (one-scheduler, STREAM D, 2026-10-12).

Two guardrails every central-registry job runs under:

1. **Per-job hard timeouts** — ``run_with_timeout(fn, seconds)`` runs the job
   body on a daemon worker thread (modeled on the polyclaw 80s pattern:
   ``Future`` + ``future.result(timeout=...)``) and raises
   ``SchedulerJobTimeoutError`` when the ceiling is hit. The timeout kills only
   the job *run* — the APScheduler thread itself is never blocked waiting for
   a hung job, and a hung worker can never block interpreter shutdown.

2. **Per-job circuit breakers** — ``JobCircuitBreaker`` tracks consecutive
   failures per job_id (failure = exception OR timeout). After N consecutive
   failures (default 5, env ``SCHEDULER_CB_THRESHOLD`` overrides) the breaker
   OPENS: runs are skipped with a clear log for a cooldown (default 30 min,
   env ``SCHEDULER_CB_COOLDOWN_SEC`` overrides), then the breaker half-opens
   for one trial run. Any success resets the counter.

Composition with the existing ``_safe_run`` pattern: the guards are the
INNER wrapper, ``_safe_run`` stays the OUTER wrapper —

    sched.add_job(lambda: _safe_run(name, lambda: guarded_run(job_id, name, func)))

so no job exception (including a timeout) can ever propagate to APScheduler
and kill the scheduler thread. ``guarded_run`` records the failure with the
breaker and re-raises, letting ``_safe_run`` do its normal logging.

Breaker state is in-memory by design. If Stream B's durable ledger later
persists breaker state across restarts, this module should accept an
optional state store — not built here.
"""
from __future__ import annotations

import concurrent.futures
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("sincor2.scheduler")


class SchedulerJobTimeoutError(TimeoutError):
    """Raised when a job run exceeds its hard per-job timeout ceiling."""


# ---------------------------------------------------------------------------
# Per-job hard timeout ceilings (seconds).
#
# Chosen from the cadence of each job: ceilings are 5-10x the cadence period
# (or the job's own documented ceiling, e.g. polyclaw 80s from
# polyclaw_scheduler.run_polyclaw_cycle). Do not change a value here without
# a founder decision — these gate live outbound, trading, and audits.
# ---------------------------------------------------------------------------
PER_JOB_TIMEOUTS: Dict[str, int] = {
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
    # 2-minute cadence -> 600s ceiling (5x), same band as other fast loops.
    "activity_engine_cycle": 600,
}

#: Fallback ceiling for job ids not in PER_JOB_TIMEOUTS.
DEFAULT_JOB_TIMEOUT_SEC = 600

#: Env names for circuit-breaker tuning.
CB_THRESHOLD_ENV = "SCHEDULER_CB_THRESHOLD"
CB_COOLDOWN_ENV = "SCHEDULER_CB_COOLDOWN_SEC"

_CB_THRESHOLD_DEFAULT = 5
_CB_COOLDOWN_SEC_DEFAULT = 30 * 60


def run_with_timeout(fn: Callable[[], Any], seconds: float) -> Any:
    """Run ``fn`` on a worker thread; raise SchedulerJobTimeoutError on timeout.

    Modeled on the polyclaw 80s pattern (``Future`` + ``future.result(timeout=...)``):
    the caller waits at most ``seconds`` for the run. The worker thread is a
    daemon, so a hung job can never block interpreter shutdown; the run is
    orphaned and the caller is never blocked. On timeout the run is logged
    loudly and treated as an error state (exception raised); the caller —
    and the scheduler thread — are never at risk.
    """
    future: "concurrent.futures.Future[Any]" = concurrent.futures.Future()

    def _target() -> None:
        if not future.set_running_or_notify_cancel():
            return
        try:
            future.set_result(fn())
        except BaseException as exc:  # never let the worker die silently
            future.set_exception(exc)

    worker = threading.Thread(
        target=_target, daemon=True, name="sched-guard-worker"
    )
    worker.start()
    try:
        return future.result(timeout=seconds)
    except concurrent.futures.TimeoutError:
        logger.error(
            "[SCHEDULER][GUARD] job TIMED OUT after %.0fs — run recorded as "
            "error state; scheduler unaffected",
            seconds,
        )
        raise SchedulerJobTimeoutError(
            f"job exceeded hard timeout of {seconds}s"
        ) from None


def _cb_threshold() -> int:
    try:
        return max(1, int(os.environ.get(CB_THRESHOLD_ENV, str(_CB_THRESHOLD_DEFAULT))))
    except ValueError:
        return _CB_THRESHOLD_DEFAULT


def _cb_cooldown_sec() -> float:
    try:
        return max(0.0, float(os.environ.get(CB_COOLDOWN_ENV, str(_CB_COOLDOWN_SEC_DEFAULT))))
    except ValueError:
        return _CB_COOLDOWN_SEC_DEFAULT


class JobCircuitBreaker:
    """In-memory per-job circuit breaker: closed -> open -> half-open.

    ``allow(job_id)`` returns False while the breaker is open (cooldown).
    After the cooldown elapses, the next call half-opens: exactly one trial
    run is admitted. ``record_success`` closes the breaker and resets the
    consecutive-failure counter; ``record_failure`` increments it and opens
    the breaker once the threshold is reached.

    State is in-memory: a restart resets every breaker to closed. (Stream B's
    durable ledger may persist this later; this module stays self-contained.)
    """

    _CLOSED = "closed"
    _OPEN = "open"
    _HALF_OPEN = "half-open"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # job_id -> {"failures": int, "state": str, "opened_at": float,
        #            "trial_in_flight": bool}
        self._state: Dict[str, Dict[str, Any]] = {}

    def _entry(self, job_id: str) -> Dict[str, Any]:
        entry = self._state.get(job_id)
        if entry is None:
            entry = {
                "failures": 0,
                "state": self._CLOSED,
                "opened_at": 0.0,
                "trial_in_flight": False,
            }
            self._state[job_id] = entry
        return entry

    def allow(self, job_id: str) -> bool:
        """True if a run may proceed; False if the breaker is open."""
        with self._lock:
            entry = self._entry(job_id)
            state = entry["state"]
            if state == self._CLOSED:
                return True
            if state == self._HALF_OPEN:
                # Only one trial run at a time.
                if entry["trial_in_flight"]:
                    return False
                entry["trial_in_flight"] = True
                return True
            # OPEN: check cooldown expiry -> half-open.
            if time.monotonic() - entry["opened_at"] >= _cb_cooldown_sec():
                entry["state"] = self._HALF_OPEN
                entry["trial_in_flight"] = True
                logger.warning(
                    "[SCHEDULER][GUARD] circuit breaker for %s HALF-OPEN: "
                    "one trial run admitted",
                    job_id,
                )
                return True
            return False

    def record_success(self, job_id: str) -> None:
        with self._lock:
            entry = self._entry(job_id)
            if entry["state"] != self._CLOSED:
                logger.info(
                    "[SCHEDULER][GUARD] circuit breaker for %s CLOSED "
                    "(recovered after %d consecutive failures)",
                    job_id,
                    entry["failures"],
                )
            entry["failures"] = 0
            entry["state"] = self._CLOSED
            entry["trial_in_flight"] = False

    def record_failure(self, job_id: str) -> None:
        with self._lock:
            entry = self._entry(job_id)
            entry["failures"] += 1
            entry["trial_in_flight"] = False
            threshold = _cb_threshold()
            if entry["state"] == self._HALF_OPEN:
                # Trial run failed -> straight back to open.
                entry["state"] = self._OPEN
                entry["opened_at"] = time.monotonic()
                logger.error(
                    "[SCHEDULER][GUARD] circuit breaker for %s trial run FAILED "
                    "(%d consecutive failures): OPEN for %.0fs",
                    job_id,
                    entry["failures"],
                    _cb_cooldown_sec(),
                )
            elif entry["failures"] >= threshold and entry["state"] == self._CLOSED:
                entry["state"] = self._OPEN
                entry["opened_at"] = time.monotonic()
                logger.error(
                    "[SCHEDULER][GUARD] circuit breaker for %s OPEN after %d "
                    "consecutive failures: skipping runs for %.0fs",
                    job_id,
                    entry["failures"],
                    _cb_cooldown_sec(),
                )

    def snapshot(self, job_id: str) -> Dict[str, Any]:
        """Read-only view of one job's breaker state (for status/health)."""
        with self._lock:
            return dict(self._entry(job_id))

    def all_snapshots(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {jid: dict(e) for jid, e in self._state.items()}


#: The module-level breaker used by the central scheduler.
_breaker = JobCircuitBreaker()


def guarded_run(job_id: str, name: str, func: Callable[[], Any]) -> Any:
    """Run one job under timeout + circuit-breaker guards.

    Intended as the INNER wrapper under ``_safe_run`` (timeout inside,
    safe_run outside). On failure the breaker records it and the exception
    re-raises so ``_safe_run`` logs it as an error state — never swallowed,
    never allowed to reach APScheduler.
    """
    if not _breaker.allow(job_id):
        logger.warning(
            "[SCHEDULER][GUARD] %s (%s) SKIPPED: circuit breaker OPEN "
            "(cooldown in effect)",
            name,
            job_id,
        )
        return None
    timeout = PER_JOB_TIMEOUTS.get(job_id, DEFAULT_JOB_TIMEOUT_SEC)
    try:
        result = run_with_timeout(func, timeout)
    except Exception:
        _breaker.record_failure(job_id)
        raise
    _breaker.record_success(job_id)
    return result


def get_breaker() -> JobCircuitBreaker:
    """Access the shared breaker (tests, health endpoints)."""
    return _breaker


def reset_breaker() -> None:
    """Reset the shared breaker (tests only — never call in production code)."""
    global _breaker
    _breaker = JobCircuitBreaker()
