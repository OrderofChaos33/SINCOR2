"""Persistent auto-heal and error-handling foundation for SINCOR2.

Replaces ad-hoc try/except with a coherent resilience layer:

* :class:`PersistentErrorTracker` — thread-safe, disk-backed error records
  with transient/permanent classification, so error history survives restarts.
* :class:`CircuitBreaker` — fail fast when an endpoint is down; half-open
  probes let it recover without a thundering herd.
* :class:`RetryPolicy` — exponential backoff with jitter, honoring
  ``Retry-After`` hints carried by exceptions.
* :class:`HealthCheck` — aggregate component health (healthy/degraded/down).
* :class:`AutoHealCoordinator` — orchestrates the full loop:
  classify -> track -> circuit-check -> retry with backoff -> recovery action.

Standard library only. Shared state is guarded by re-entrant locks.
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("sincor.resilience.auto_heal")


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


class ErrorClassification(str, Enum):
    """Whether an error is worth retrying."""

    TRANSIENT = "transient"  # 5xx, 429, network failures — retry with backoff
    PERMANENT = "permanent"  # 4xx (except 429) — retrying cannot help


# Exception type names that always indicate a transient transport failure,
# regardless of any status code attached.
_TRANSIENT_EXCEPTION_NAMES = frozenset(
    {
        "ConnectionError",
        "TimeoutError",
        "ConnectTimeout",
        "ReadTimeout",
        "NewConnectionError",
        "MaxRetryError",
        "RemoteDisconnected",
        "ChunkedEncodingError",
        "SSLError",
        "TemporaryFailure",
    }
)


def classify_error(
    status_code: Optional[int] = None,
    exc: Optional[BaseException] = None,
) -> ErrorClassification:
    """Classify an error as transient (retryable) or permanent.

    Rules, in precedence order:
    1. HTTP 429 and 5xx -> transient.
    2. Other 4xx -> permanent.
    3. Known network exception types -> transient.
    4. Anything else -> transient (auto-heal prefers attempting recovery
       over silently giving up; the retry budget bounds the cost).
    """
    if status_code is None and exc is not None:
        # Exceptions that carry their own HTTP status (e.g. HttpError).
        status_code = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status_code is not None:
        try:
            code = int(status_code)
        except (TypeError, ValueError):
            code = None
        if code is not None:
            if code == 429 or 500 <= code < 600:
                return ErrorClassification.TRANSIENT
            if 400 <= code < 500:
                return ErrorClassification.PERMANENT
    if exc is not None and type(exc).__name__ in _TRANSIENT_EXCEPTION_NAMES:
        return ErrorClassification.TRANSIENT
    return ErrorClassification.TRANSIENT


def _status_of(exc: BaseException) -> Optional[int]:
    """Pull an HTTP status off an exception, if it carries one."""
    return getattr(exc, "status_code", None) or getattr(exc, "status", None)


# ---------------------------------------------------------------------------
# Error records + persistent tracker
# ---------------------------------------------------------------------------


@dataclass
class ErrorRecord:
    """One tracked error signature, aggregated over time."""

    error_type: str
    endpoint: str
    count: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    classification: ErrorClassification = ErrorClassification.TRANSIENT
    last_message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["classification"] = self.classification.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ErrorRecord":
        return cls(
            error_type=d["error_type"],
            endpoint=d["endpoint"],
            count=int(d.get("count", 0)),
            first_seen=float(d.get("first_seen", 0.0)),
            last_seen=float(d.get("last_seen", 0.0)),
            classification=ErrorClassification(d.get("classification", "transient")),
            last_message=str(d.get("last_message", "")),
        )


class PersistentErrorTracker:
    """Thread-safe, disk-backed store of aggregated error records.

    Records are keyed by ``(error_type, endpoint)`` and written to JSON
    atomically (tmp file + rename), so a crash mid-write never corrupts the
    store. A corrupt store logs a warning and starts empty.
    """

    def __init__(
        self,
        path: str,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path = path
        self._clock = clock
        self._lock = threading.RLock()
        self._records: Dict[str, ErrorRecord] = {}
        self._load()

    @staticmethod
    def _key(error_type: str, endpoint: str) -> str:
        return f"{error_type}@{endpoint}"

    def record(
        self,
        error_type: str,
        endpoint: str,
        classification: ErrorClassification,
        message: str = "",
    ) -> ErrorRecord:
        """Increment the record for this error signature and persist."""
        now = self._clock()
        with self._lock:
            key = self._key(error_type, endpoint)
            rec = self._records.get(key)
            if rec is None:
                rec = ErrorRecord(
                    error_type=error_type,
                    endpoint=endpoint,
                    first_seen=now,
                    classification=classification,
                )
                self._records[key] = rec
            rec.count += 1
            rec.last_seen = now
            rec.classification = classification
            if message:
                rec.last_message = message[:500]
            self._save()
            return rec

    def get(self, error_type: str, endpoint: str) -> Optional[ErrorRecord]:
        with self._lock:
            return self._records.get(self._key(error_type, endpoint))

    def all_records(self) -> List[ErrorRecord]:
        with self._lock:
            return list(self._records.values())

    def stats(self) -> Dict[str, Any]:
        """Aggregate totals, split by classification."""
        with self._lock:
            total = sum(r.count for r in self._records.values())
            by_class: Dict[str, int] = {}
            for rec in self._records.values():
                by_class[rec.classification.value] = (
                    by_class.get(rec.classification.value, 0) + rec.count
                )
            return {
                "signatures": len(self._records),
                "total_errors": total,
                "by_classification": by_class,
            }

    def prune(self, older_than_seconds: float) -> int:
        """Drop records whose last sighting is older than the cutoff."""
        cutoff = self._clock() - older_than_seconds
        with self._lock:
            stale = [k for k, r in self._records.items() if r.last_seen < cutoff]
            for k in stale:
                del self._records[k]
            if stale:
                self._save()
            return len(stale)

    def _save(self) -> None:
        tmp = self._path + ".tmp"
        try:
            directory = os.path.dirname(self._path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(
                    {k: r.to_dict() for k, r in self._records.items()},
                    fh,
                    indent=1,
                    sort_keys=True,
                )
            os.replace(tmp, self._path)
        except OSError as exc:
            logger.warning("error tracker: failed to persist to %s: %s", self._path, exc)

    def _load(self) -> None:
        try:
            with open(self._path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            logger.warning(
                "error tracker: corrupt store at %s (%s); starting empty",
                self._path,
                exc,
            )
            return
        with self._lock:
            for key, d in raw.items():
                try:
                    self._records[key] = ErrorRecord.from_dict(d)
                except (KeyError, ValueError, TypeError) as exc:
                    logger.warning("error tracker: skipping bad record %s: %s", key, exc)


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


class CircuitState(str, Enum):
    CLOSED = "closed"  # traffic flows; failures counted
    OPEN = "open"  # failing fast; no calls pass through
    HALF_OPEN = "half_open"  # one probe call allowed to test recovery


class CircuitOpenError(Exception):
    """Raised when a call is rejected because the circuit is open."""


class CircuitBreaker:
    """Fail-fast guard for a single endpoint.

    Opens after ``failure_threshold`` consecutive failures. After
    ``cooldown_seconds`` it half-opens: a single probe call decides whether
    the circuit closes (probe succeeded) or re-opens (probe failed).
    Thread-safe.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        cooldown_seconds: float = 60.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._lock = threading.RLock()
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = 0.0

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._maybe_half_open()
            return self._state

    @property
    def consecutive_failures(self) -> int:
        with self._lock:
            return self._consecutive_failures

    def _maybe_half_open(self) -> None:
        if (
            self._state == CircuitState.OPEN
            and self._clock() - self._opened_at >= self.cooldown_seconds
        ):
            self._state = CircuitState.HALF_OPEN
            logger.info("circuit %s: open -> half_open (probe allowed)", self.name)

    def _on_success(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                logger.info("circuit %s: half_open -> closed (recovered)", self.name)
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0

    def _on_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if self._state == CircuitState.HALF_OPEN:
                self._open()
            elif (
                self._state == CircuitState.CLOSED
                and self._consecutive_failures >= self.failure_threshold
            ):
                self._open()

    def _open(self) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = self._clock()
        logger.warning(
            "circuit %s: OPEN after %d consecutive failures",
            self.name,
            self._consecutive_failures,
        )

    def call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run ``fn`` through the breaker; raises :class:`CircuitOpenError`
        immediately when the circuit is open (fail fast, no call attempted)."""
        with self._lock:
            self._maybe_half_open()
            if self._state == CircuitState.OPEN:
                raise CircuitOpenError(f"circuit {self.name} is open")
        try:
            result = fn(*args, **kwargs)
        except Exception:
            self._on_failure()
            raise
        self._on_success()
        return result

    def reset(self) -> None:
        """Force the breaker closed (operator override)."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            logger.info("circuit %s: manually reset to closed", self.name)


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------


@dataclass
class RetryPolicy:
    """Exponential backoff with jitter.

    ``execute`` retries a callable while failures classify as transient.
    Exceptions carrying a ``retry_after`` attribute (seconds) are honored
    over the computed backoff. ``CircuitOpenError`` is never retried —
    the breaker already decided.
    """

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    jitter: float = 0.25  # +/- fraction applied to each delay
    sleep: Callable[[float], None] = field(default=time.sleep, repr=False)

    def delay_for(self, attempt: int) -> float:
        """Backoff delay before retry ``attempt`` (0-based)."""
        raw = min(self.base_delay * (2**attempt), self.max_delay)
        return raw * (1.0 + random.uniform(-self.jitter, self.jitter))

    def execute(
        self,
        fn: Callable[[], Any],
        is_retryable: Optional[Callable[[BaseException], bool]] = None,
        on_retry: Optional[Callable[[BaseException, int, float], None]] = None,
    ) -> Any:
        """Call ``fn``; retry transient failures up to ``max_retries`` times.

        ``is_retryable`` overrides classification; ``on_retry`` is invoked
        with (exception, attempt, delay) before each sleep.
        """
        attempt = 0
        while True:
            try:
                return fn()
            except CircuitOpenError:
                raise  # breaker decided; never spin against it
            except Exception as exc:
                classification = classify_error(_status_of(exc), exc)
                retryable = (
                    is_retryable(exc)
                    if is_retryable is not None
                    else classification == ErrorClassification.TRANSIENT
                )
                if not retryable or attempt >= self.max_retries:
                    raise
                retry_after = getattr(exc, "retry_after", None)
                delay = (
                    float(retry_after)
                    if retry_after is not None
                    else self.delay_for(attempt)
                )
                if on_retry is not None:
                    on_retry(exc, attempt, delay)
                logger.info(
                    "retry %d/%d after %.2fs (%s: %s)",
                    attempt + 1,
                    self.max_retries,
                    delay,
                    type(exc).__name__,
                    exc,
                )
                self.sleep(delay)
                attempt += 1


# ---------------------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------------------


class ComponentHealth(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass
class ComponentStatus:
    name: str
    health: ComponentHealth
    last_check: float
    detail: str = ""


class HealthCheck:
    """Aggregate health across named components. Worst status wins."""

    _SEVERITY = {
        ComponentHealth.HEALTHY: 0,
        ComponentHealth.DEGRADED: 1,
        ComponentHealth.DOWN: 2,
    }

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._checks: Dict[str, Callable[[], ComponentStatus]] = {}
        self._lock = threading.RLock()

    def register(self, name: str, check_fn: Callable[[], ComponentStatus]) -> None:
        """Register a zero-arg callable returning a :class:`ComponentStatus`."""
        with self._lock:
            self._checks[name] = check_fn

    def check_all(self) -> Dict[str, ComponentStatus]:
        """Run every registered check; a raising check reports DOWN."""
        results: Dict[str, ComponentStatus] = {}
        with self._lock:
            items = list(self._checks.items())
        for name, check_fn in items:
            try:
                status = check_fn()
            except Exception as exc:  # noqa: BLE001 — a dead check is DOWN
                status = ComponentStatus(
                    name=name,
                    health=ComponentHealth.DOWN,
                    last_check=self._clock(),
                    detail=f"check raised {type(exc).__name__}: {exc}",
                )
            results[name] = status
        return results

    def aggregate(self) -> ComponentHealth:
        """Overall health: the worst of all components (DOWN > DEGRADED)."""
        worst = ComponentHealth.HEALTHY
        for status in self.check_all().values():
            if self._SEVERITY[status.health] > self._SEVERITY[worst]:
                worst = status.health
        return worst


# ---------------------------------------------------------------------------
# Auto-heal coordinator
# ---------------------------------------------------------------------------


class AutoHealCoordinator:
    """Orchestrates the full auto-heal loop for fallible operations.

    ``run`` executes ``fn`` for ``endpoint`` through this pipeline:

    1. Circuit-breaker gate (fail fast when the endpoint is down).
    2. Retry with exponential backoff for transient failures.
    3. Every failure is classified and recorded in the persistent tracker.
    4. When all retries are exhausted, an optional ``recovery`` callable is
       invoked with (exception, ErrorRecord) for domain-specific healing
       (re-register an agent, rotate a key, flush a queue, ...).
    """

    def __init__(
        self,
        tracker: PersistentErrorTracker,
        retry_policy: Optional[RetryPolicy] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._tracker = tracker
        self._retry = retry_policy or RetryPolicy()
        self._clock = clock
        self._lock = threading.RLock()
        self._breakers: Dict[str, CircuitBreaker] = {}
        self.health = HealthCheck(clock=clock)

    def breaker_for(self, endpoint: str, **kwargs: Any) -> CircuitBreaker:
        """Get (creating if needed) the breaker guarding ``endpoint``."""
        with self._lock:
            breaker = self._breakers.get(endpoint)
            if breaker is None:
                breaker = CircuitBreaker(endpoint, clock=self._clock, **kwargs)
                self._breakers[endpoint] = breaker
            return breaker

    def _track(
        self, exc: BaseException, endpoint: str
    ) -> tuple[ErrorClassification, ErrorRecord]:
        classification = classify_error(_status_of(exc), exc)
        record = self._tracker.record(
            type(exc).__name__, endpoint, classification, str(exc)
        )
        return classification, record

    def run(
        self,
        fn: Callable[[], Any],
        *,
        endpoint: str,
        recovery: Optional[Callable[[BaseException, ErrorRecord], None]] = None,
        is_retryable: Optional[Callable[[BaseException], bool]] = None,
    ) -> Any:
        """Run ``fn`` with the full auto-heal pipeline. Re-raises the last
        exception after retries are exhausted and recovery has run."""
        breaker = self.breaker_for(endpoint)

        def guarded() -> Any:
            return breaker.call(fn)

        def on_retry(exc: BaseException, attempt: int, delay: float) -> None:
            classification, _ = self._track(exc, endpoint)
            logger.warning(
                "auto-heal %s: %s (%s), retry %d in %.2fs",
                endpoint,
                type(exc).__name__,
                classification.value,
                attempt + 1,
                delay,
            )

        try:
            return self._retry.execute(guarded, is_retryable=is_retryable, on_retry=on_retry)
        except CircuitOpenError:
            self._tracker.record(
                "CircuitOpenError", endpoint, ErrorClassification.TRANSIENT,
                f"circuit open for {endpoint}",
            )
            raise
        except Exception as exc:  # noqa: BLE001 — final failure path
            _, record = self._track(exc, endpoint)
            logger.error(
                "auto-heal %s: exhausted retries for %s (seen %d times)",
                endpoint,
                type(exc).__name__,
                record.count,
            )
            if recovery is not None:
                try:
                    recovery(exc, record)
                except Exception as rec_exc:
                    logger.error(
                        "auto-heal %s: recovery action failed: %s: %s",
                        endpoint,
                        type(rec_exc).__name__,
                        rec_exc,
                    )
            raise

    def endpoint_stats(self, endpoint: str) -> Dict[str, Any]:
        """Breaker state + tracked error totals for one endpoint."""
        breaker = self.breaker_for(endpoint)
        records = [r for r in self._tracker.all_records() if r.endpoint == endpoint]
        return {
            "endpoint": endpoint,
            "circuit_state": breaker.state.value,
            "consecutive_failures": breaker.consecutive_failures,
            "signatures": len(records),
            "total_errors": sum(r.count for r in records),
        }
