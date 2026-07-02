#!/usr/bin/env python3
"""
Production-grade Circuit Breaker for SINCOR agents and external calls.

Best practice implementation inspired by industry standards (Netflix Hystrix style, adapted for Python async/sync).
Features:
- Three states: CLOSED, OPEN, HALF_OPEN
- Configurable failure threshold, timeout, reset timeout
- Fallback function support
- Metrics / observability hooks (integrates with existing production_logger and monitoring_dashboard)
- Thread-safe
- No external dependencies beyond stdlib + existing SINCOR logging

Usage example:
    from resilience.circuit_breaker import CircuitBreaker

    breaker = CircuitBreaker(
        name="renegade_api",
        failure_threshold=5,
        reset_timeout=60,
        fallback=lambda *a, **kw: {"error": "circuit open, using fallback"}
    )

    @breaker
    def call_renegade(...):
        ...

This prevents cascading failures in Polyclaw, TOA, Renegade, RPC calls, etc.
Part of the highest-caliber resilience layer for the self-perpetuating earning machine.
"""

import time
import threading
import functools
from typing import Callable, Any, Optional, Dict
from datetime import datetime, timedelta

# Integrate with existing SINCOR observability
try:
    from src.sincor2.production_logger import get_logger
    logger = get_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


class CircuitBreakerState:
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        reset_timeout: int = 60,
        half_open_max_calls: int = 1,
        fallback: Optional[Callable] = None,
        on_state_change: Optional[Callable[[str, str], None]] = None,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.half_open_max_calls = half_open_max_calls
        self.fallback = fallback
        self.on_state_change = on_state_change

        self._state = CircuitBreakerState.CLOSED
        self._failure_count = 0
        self._last_failure_time: Optional[datetime] = None
        self._half_open_calls = 0
        self._lock = threading.RLock()

        logger.info(f"CircuitBreaker '{name}' initialized in {self._state} state")

    @property
    def state(self) -> str:
        with self._lock:
            if self._state == CircuitBreakerState.OPEN:
                if self._last_failure_time and datetime.now() - self._last_failure_time > timedelta(seconds=self.reset_timeout):
                    self._transition_to(CircuitBreakerState.HALF_OPEN)
            return self._state

    def _transition_to(self, new_state: str):
        old_state = self._state
        self._state = new_state
        if new_state == CircuitBreakerState.CLOSED:
            self._failure_count = 0
            self._half_open_calls = 0
        elif new_state == CircuitBreakerState.HALF_OPEN:
            self._half_open_calls = 0

        logger.warning(f"CircuitBreaker '{self.name}' state changed: {old_state} -> {new_state}")
        if self.on_state_change:
            try:
                self.on_state_change(old_state, new_state)
            except Exception as e:
                logger.error(f"Error in on_state_change callback: {e}")

    def _record_success(self):
        with self._lock:
            if self._state == CircuitBreakerState.HALF_OPEN:
                self._transition_to(CircuitBreakerState.CLOSED)
            else:
                self._failure_count = 0

    def _record_failure(self):
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = datetime.now()
            if self._failure_count >= self.failure_threshold:
                if self._state != CircuitBreakerState.OPEN:
                    self._transition_to(CircuitBreakerState.OPEN)

    def __call__(self, func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            current_state = self.state

            if current_state == CircuitBreakerState.OPEN:
                logger.warning(f"CircuitBreaker '{self.name}' OPEN - using fallback")
                if self.fallback:
                    return self.fallback(*args, **kwargs)
                raise Exception(f"Circuit breaker {self.name} is OPEN")

            if current_state == CircuitBreakerState.HALF_OPEN:
                with self._lock:
                    if self._half_open_calls >= self.half_open_max_calls:
                        if self.fallback:
                            return self.fallback(*args, **kwargs)
                        raise Exception(f"Circuit breaker {self.name} HALF_OPEN limit reached")
                    self._half_open_calls += 1

            try:
                result = func(*args, **kwargs)
                self._record_success()
                return result
            except Exception as e:
                self._record_failure()
                logger.error(f"CircuitBreaker '{self.name}' call failed: {e}")
                if self.fallback:
                    return self.fallback(*args, **kwargs)
                raise

        return wrapper

    def call(self, func: Callable, *args, **kwargs) -> Any:
        """Direct call method for non-decorator usage."""
        return self.__call__(func)(*args, **kwargs)


# Example production usage for Renegade / RPC / external services
if __name__ == "__main__":
    def example_renegade_call():
        # Simulate external call
        import random
        if random.random() < 0.3:
            raise Exception("Simulated Renegade failure")
        return {"status": "success", "price": 123.45}

    def renegade_fallback(*args, **kwargs):
        logger.info("Using Renegade fallback (conservative mode)")
        return {"status": "fallback", "price": None}

    breaker = CircuitBreaker(
        name="renegade_dark_pool",
        failure_threshold=3,
        reset_timeout=30,
        fallback=renegade_fallback
    )

    for i in range(10):
        try:
            result = breaker.call(example_renegade_call)
            print(f"Attempt {i}: {result}")
        except Exception as e:
            print(f"Attempt {i} failed: {e}")
        time.sleep(1)
