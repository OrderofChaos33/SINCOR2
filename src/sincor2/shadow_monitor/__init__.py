"""Phase 1 shadow-mode monitoring — an independent safety and evidence system.

This package observes SINCOR agent activity in shadow mode, records what an
agent *would* do, and raises alerts on anomalies. It is deliberately NOT an
autonomous actor: it observes, reports, and (under predefined safe rules) pauses
the affected shadow worker. It never changes permissions, clears halts, retries
risky actions, or modifies production data.

Components:
- boundary: adapter-layer side-effect blocking (would_* verbs only in shadow)
- effect_boundary: founder's EffectIntent/EffectReceipt dispatch pattern
- events: hash-chained append-only decision event log with redaction
- anomaly: six-class anomaly detectors (hard rules vs baselines)
- alerting: severity routing, dedup, dead-man checks, agent-cannot-clear guard
- dashboard: read-only six-section dashboard + synthetic verification harness

Key invariant: actual_side_effects_in_shadow == 0, structurally enforced.
"""

from .alerting import Alert, AlertManager, Severity
from .anomaly import Anomaly, AnomalyClass
from .boundary import (
    ACTUAL_SIDE_EFFECTS,
    ProposedAction,
    ShadowBoundary,
    ShadowBoundaryViolation,
    ShadowCredentials,
)
from .dashboard import ShadowDashboard, SyntheticVerification
from .effect_boundary import (
    EffectIntent,
    EffectReceipt,
    ShadowEffectBoundary,
    build_effect_boundary,
)
from .events import DecisionEvent, EventStore

__all__ = [
    "ACTUAL_SIDE_EFFECTS",
    "Alert",
    "AlertManager",
    "Anomaly",
    "AnomalyClass",
    "DecisionEvent",
    "EffectIntent",
    "EffectReceipt",
    "EventStore",
    "ProposedAction",
    "Severity",
    "ShadowBoundary",
    "ShadowBoundaryViolation",
    "ShadowCredentials",
    "ShadowDashboard",
    "ShadowEffectBoundary",
    "SyntheticVerification",
    "build_effect_boundary",
]
