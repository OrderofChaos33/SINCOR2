from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 30):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self.state = CircuitState.CLOSED

    def call(self, func: Callable, *args, **kwargs):
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
            else:
                raise Exception("Circuit breaker is OPEN - service degraded")

        try:
            result = func(*args, **kwargs)
            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.CLOSED
                self.failure_count = 0
            return result
        except Exception as e:
            self.failure_count += 1
            self.last_failure_time = time.time()
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
            raise e


class VerticalAgent(ABC):
    """
    Production-grade base agent for SINCOR2 verticals.
    Includes circuit breaker protection and rich A2A-compliant Agent Cards.
    """

    name: str = "base_vertical_agent"
    version: str = "0.2.0"
    description: str = ""
    capabilities: list[str] = []
    tags: list[str] = []
    rate_limit_per_minute: Optional[int] = 60

    def __init__(self):
        self.logger = logging.getLogger(f"vertical.{self.name}")
        self.circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30)

    @abstractmethod
    def execute(self, task: Dict[str, Any]) -> Dict[str, Any]:
        pass

    def run(self, task: Dict[str, Any]) -> Dict[str, Any]:
        if not self.validate_task(task):
            return {"status": "error", "message": "Invalid task payload"}

        try:
            return self.circuit_breaker.call(self.execute, task)
        except Exception as e:
            return self.handle_error(e, task)

    def get_agent_card(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "capabilities": self.capabilities,
            "tags": self.tags,
            "input_schema": "See schemas.py",
            "output_schema": "See schemas.py",
            "rate_limit_per_minute": self.rate_limit_per_minute,
            "circuit_breaker": {
                "state": self.circuit_breaker.state.value,
                "failure_threshold": self.circuit_breaker.failure_threshold,
            },
            "metadata": {
                "framework": "SINCOR2",
                "a2a_compliant": True,
                "production_ready": True,
            },
        }

    def validate_task(self, task: Dict[str, Any]) -> bool:
        return isinstance(task, dict) and "task_type" in task

    def handle_error(self, error: Exception, task: Dict[str, Any]) -> Dict[str, Any]:
        self.logger.error(f"Task failed in {self.name}: {error}", exc_info=True)
        return {
            "status": "error",
            "message": str(error),
            "task_type": task.get("task_type"),
        }
