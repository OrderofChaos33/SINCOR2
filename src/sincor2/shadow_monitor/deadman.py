"""WP5 dead-man checks: prove the alerting path is alive, or mark unhealthy.

A monitoring system that silently stops alerting is worse than no
monitoring: operators believe they are covered while blind. This module
provides periodic liveness verification for the alerting path:

- :class:`DeadManCheck` sends a synthetic canary alert through the real
  :class:`~sincor2.shadow_monitor.alerting.AlertManager` delivery path.
- If the canary cannot be delivered (delivery raises, or no acknowledgment
  arrives within the timeout), the system is marked UNHEALTHY.
- An unhealthy system must not silently continue: :meth:`DeadManCheck.run`
  returns the health verdict, and :meth:`DeadManCheck.require_healthy`
  raises :class:`AlertingUnhealthy` so callers fail closed.

The dead-man check never clears halts, retries actions, or modifies
production state -- it only reports health. (Monitoring-must-not-act.)

Stdlib only.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

__all__ = [
    "AlertingUnhealthy",
    "DeadManCheck",
    "HealthVerdict",
    "SYSTEM_HEALTH",
]


class AlertingUnhealthy(RuntimeError):
    """Raised when the alerting path is unhealthy and a caller requires health.

    Fail-closed: code that depends on alerts being deliverable must not
    proceed while the dead-man check reports unhealthy.
    """


@dataclass
class HealthVerdict:
    """Result of one dead-man check run."""

    healthy: bool
    checked_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    canary_delivered: bool = False
    canary_acknowledged: bool = False
    latency_ms: Optional[float] = None
    failure_reason: str = ""
    consecutive_failures: int = 0


#: Module-level last-known system health. Updated by DeadManCheck.run().
#: "healthy" | "unhealthy" | "unknown" (no check has run yet).
SYSTEM_HEALTH: Dict[str, Any] = {"status": "unknown", "since": None, "_lock": threading.Lock()}


def _set_system_health(status: str) -> None:
    with SYSTEM_HEALTH["_lock"]:
        SYSTEM_HEALTH["status"] = status
        SYSTEM_HEALTH["since"] = datetime.now(timezone.utc).isoformat()


def get_system_health() -> Dict[str, str]:
    """Return the last-known system health (copy)."""
    with SYSTEM_HEALTH["_lock"]:
        return {"status": SYSTEM_HEALTH["status"], "since": SYSTEM_HEALTH["since"] or ""}


class DeadManCheck:
    """Periodic liveness verification for the alerting path.

    ``canary_fn`` is a zero-arg callable that attempts to deliver a canary
    alert through the REAL delivery path and returns a delivery receipt dict.
    ``ack_fn`` optionally verifies the delivery was acknowledged
    (e.g. provider ack); if omitted, a non-raising delivery counts as
    acknowledged.

    Health policy:
    - one failed canary -> verdict unhealthy, consecutive_failures += 1
    - ``max_consecutive_failures`` reached -> system stays unhealthy AND
      ``require_healthy`` raises until a canary succeeds again
    - any successful canary resets consecutive_failures to 0
    """

    def __init__(
        self,
        canary_fn: Callable[[], Dict[str, Any]],
        ack_fn: Optional[Callable[[Dict[str, Any]], bool]] = None,
        max_consecutive_failures: int = 1,
    ) -> None:
        self._canary_fn = canary_fn
        self._ack_fn = ack_fn
        self._max_failures = max(1, max_consecutive_failures)
        self._consecutive_failures = 0
        self._lock = threading.Lock()
        self._history: List[HealthVerdict] = []

    @property
    def consecutive_failures(self) -> int:
        with self._lock:
            return self._consecutive_failures

    def run(self) -> HealthVerdict:
        """Run one dead-man check. Updates SYSTEM_HEALTH. Never raises."""
        start = time.monotonic()
        delivered = False
        acknowledged = False
        failure_reason = ""
        try:
            receipt = self._canary_fn()
            delivered = bool(receipt and receipt.get("delivered", True))
            if not delivered:
                failure_reason = f"canary not delivered: {receipt!r}"[:200]
            elif self._ack_fn is not None:
                acknowledged = bool(self._ack_fn(receipt))
                if not acknowledged:
                    failure_reason = "canary delivered but not acknowledged"[:200]
            else:
                acknowledged = True
        except Exception as exc:  # noqa: BLE001 -- health check never raises
            failure_reason = f"canary raised {type(exc).__name__}: {exc}"[:200]

        latency_ms = (time.monotonic() - start) * 1000.0
        healthy = delivered and acknowledged

        with self._lock:
            if healthy:
                self._consecutive_failures = 0
            else:
                self._consecutive_failures += 1
            failures = self._consecutive_failures
            verdict = HealthVerdict(
                healthy=healthy,
                canary_delivered=delivered,
                canary_acknowledged=acknowledged,
                latency_ms=latency_ms,
                failure_reason=failure_reason,
                consecutive_failures=failures,
            )
            self._history.append(verdict)
            # Bound history memory.
            del self._history[:-100]

        _set_system_health("healthy" if healthy else "unhealthy")
        return verdict

    def require_healthy(self) -> None:
        """Raise AlertingUnhealthy unless the last verdict was healthy.

        Callers that depend on alerts being deliverable (e.g. before
        dispatching a consequential action) must call this and fail closed
        when it raises.
        """
        with self._lock:
            failures = self._consecutive_failures
            last = self._history[-1] if self._history else None
        if last is None:
            raise AlertingUnhealthy(
                "no dead-man check has run yet: alerting health unknown "
                "(fail-closed)"
            )
        if not last.healthy or failures >= self._max_failures:
            raise AlertingUnhealthy(
                f"alerting path unhealthy: {last.failure_reason} "
                f"(consecutive failures: {failures})"
            )

    def history(self) -> List[HealthVerdict]:
        with self._lock:
            return list(self._history)
