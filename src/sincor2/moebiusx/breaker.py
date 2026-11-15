"""Circuit-breaker state machines for MoebiusX.

Two honest pieces:

1. :class:`ConsecutiveFailureBreaker` — a pure, deterministic
   reference of the MoebiusX ops-lineage rule: the breaker trips after
   N consecutive failed validations (v3 lineage: 3) and NEVER clears
   itself; only an explicit operator ``reset()`` closes it. No clock,
   no I/O — fully deterministic for tests.

2. ``adapters.ResilienceBreakerAdapter`` wraps the in-repo
   ``sincor2.resilience.auto_heal.CircuitBreaker`` (threshold +
   cooldown + half-open probe) — that one IS a live in-tree component.
"""
from __future__ import annotations

import threading
from enum import Enum
from typing import Any, Callable


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"


class BreakerOpenError(RuntimeError):
    """Raised when a call is attempted while the breaker is open."""


class ConsecutiveFailureBreaker:
    """Trips after ``failure_threshold`` consecutive failures.

    Success resets the consecutive count. Once open, ``call`` fails
    fast without invoking the function, and the breaker stays open
    until ``reset()`` (operator action) — matching the MoebiusX rule
    that the loop never clears its own breaker.
    """

    def __init__(self, name: str = "moebiusx",
                 failure_threshold: int = 3) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        self.name = name
        self.failure_threshold = failure_threshold
        self._lock = threading.RLock()
        self._state = BreakerState.CLOSED
        self._consecutive_failures = 0
        self._tripped_reason = ""

    @property
    def state(self) -> BreakerState:
        with self._lock:
            return self._state

    @property
    def tripped(self) -> bool:
        return self.state is BreakerState.OPEN

    @property
    def consecutive_failures(self) -> int:
        with self._lock:
            return self._consecutive_failures

    @property
    def tripped_reason(self) -> str:
        with self._lock:
            return self._tripped_reason

    def record_success(self) -> None:
        with self._lock:
            if self._state is BreakerState.CLOSED:
                self._consecutive_failures = 0

    def record_failure(self, reason: str = "") -> None:
        with self._lock:
            if self._state is BreakerState.OPEN:
                return
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.failure_threshold:
                self._state = BreakerState.OPEN
                self._tripped_reason = reason

    def call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            if self._state is BreakerState.OPEN:
                raise BreakerOpenError(
                    f"circuit {self.name} is open: {self._tripped_reason}")
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            self.record_failure(str(exc))
            raise
        self.record_success()
        return result

    def reset(self) -> None:
        """Operator override — the ONLY way an open breaker closes."""
        with self._lock:
            self._state = BreakerState.CLOSED
            self._consecutive_failures = 0
            self._tripped_reason = ""
