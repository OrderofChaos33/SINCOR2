from __future__ import annotations

"""Structured metrics and event collection helpers."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from statistics import mean
from typing import Any, Dict, List, Optional


@dataclass
class MetricPoint:
    """Represents a single metric sample."""

    name: str
    value: float
    tags: Dict[str, str] = field(default_factory=dict)
    recorded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class EventRecord:
    """Represents a structured event emitted by the runtime."""

    event_type: str
    severity: str
    message: str
    payload: Dict[str, Any] = field(default_factory=dict)
    emitted_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ObservabilityHarness:
    """Captures lightweight metrics, events, and health summaries."""

    def __init__(self) -> None:
        self.metrics: List[MetricPoint] = []
        self.events: List[EventRecord] = []

    def record_metric(self, name: str, value: float, tags: Optional[Dict[str, str]] = None) -> MetricPoint:
        """Record a metric sample."""
        metric = MetricPoint(name=name, value=value, tags=tags or {})
        self.metrics.append(metric)
        return metric

    def emit_event(self, event_type: str, severity: str, message: str, payload: Optional[Dict[str, Any]] = None) -> EventRecord:
        """Emit a structured operational event."""
        event = EventRecord(event_type=event_type, severity=severity, message=message, payload=payload or {})
        self.events.append(event)
        return event

    def get_metrics_summary(self) -> Dict[str, Dict[str, float]]:
        """Summarize metrics by name."""
        summary: Dict[str, List[float]] = {}
        for metric in self.metrics:
            summary.setdefault(metric.name, []).append(metric.value)
        return {
            name: {
                'count': float(len(values)),
                'min': min(values),
                'max': max(values),
                'avg': round(mean(values), 4),
            }
            for name, values in summary.items()
        }

    def check_health(self) -> Dict[str, object]:
        """Return a basic health rollup from recent events."""
        critical_events = [event for event in self.events if event.severity.lower() == 'critical']
        status = 'degraded' if critical_events else 'healthy'
        return {
            'status': status,
            'metric_count': len(self.metrics),
            'event_count': len(self.events),
            'critical_event_count': len(critical_events),
        }
