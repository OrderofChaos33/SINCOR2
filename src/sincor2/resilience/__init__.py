"""SINCOR2 resilience primitives: auto-heal and persistent error handling."""

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

__all__ = [
    "AutoHealCoordinator",
    "CircuitBreaker",
    "CircuitOpenError",
    "CircuitState",
    "ComponentHealth",
    "ComponentStatus",
    "ErrorClassification",
    "ErrorRecord",
    "HealthCheck",
    "PersistentErrorTracker",
    "RetryPolicy",
    "classify_error",
]
