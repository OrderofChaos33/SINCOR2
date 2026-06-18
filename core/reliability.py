from __future__ import annotations

"""Reliability controls including circuit breaking and retry helpers."""

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple, Type


@dataclass
class CircuitState:
    """Represents the state of a named circuit breaker."""

    failures: int = 0
    threshold: int = 3
    status: str = 'closed'
    opened_at: Optional[float] = None


class ReliabilityControls:
    """Provides circuit breakers, retries, fallbacks, and health snapshots."""

    def __init__(self) -> None:
        self.circuits: Dict[str, CircuitState] = {}
        self.fallbacks: Dict[str, Callable[..., Any]] = {}

    def call_with_breaker(self, name: str, operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run an operation behind a named circuit breaker."""
        circuit = self.circuits.setdefault(name, CircuitState())
        if circuit.status == 'open':
            fallback = self.fallbacks.get(name)
            if fallback is not None:
                return fallback(*args, **kwargs)
            raise RuntimeError(f'Circuit {name} is open.')
        try:
            result = operation(*args, **kwargs)
        except Exception:
            circuit.failures += 1
            if circuit.failures >= circuit.threshold:
                circuit.status = 'open'
                circuit.opened_at = time.time()
            raise
        circuit.failures = 0
        circuit.status = 'closed'
        circuit.opened_at = None
        return result

    def retry_with_backoff(
        self,
        operation: Callable[..., Any],
        *args: Any,
        retries: int = 3,
        base_delay: float = 0.2,
        retry_exceptions: Tuple[Type[BaseException], ...] = (Exception,),
        **kwargs: Any,
    ) -> Any:
        """Retry an operation with exponential backoff."""
        attempt = 0
        while True:
            try:
                return operation(*args, **kwargs)
            except retry_exceptions:
                attempt += 1
                if attempt > retries:
                    raise
                time.sleep(base_delay * (2 ** (attempt - 1)))

    def register_fallback(self, name: str, handler: Callable[..., Any]) -> None:
        """Register a fallback handler for a named circuit."""
        self.fallbacks[name] = handler

    def get_circuit_status(self) -> Dict[str, Dict[str, Any]]:
        """Return the status of all registered circuits."""
        return {
            name: {
                'failures': state.failures,
                'threshold': state.threshold,
                'status': state.status,
                'opened_at': state.opened_at,
            }
            for name, state in self.circuits.items()
        }
