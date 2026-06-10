"""SINCOR Observability — lightweight OpenTelemetry-compatible span tracing.

Emits structured JSON trace events to stdout (Railway/Docker compatible) and
optionally to an OTLP HTTP endpoint.

Usage
-----
In agent execution code::

    from sincor2.observability import trace_task

    with trace_task(task_id="t-123", skill_id="healthcare-rcm",
                    agent_id="healthcare_rcm_agent") as span:
        result = do_work()
        span.set_attribute("result.status", result.get("status"))

In Flask routes / A2ARouter::

    from sincor2.observability import get_tracer
    tracer = get_tracer()
    with tracer.start_span("a2a.message_send", task_id=task.id) as span:
        ...

Environment variables
---------------------
``OTEL_EXPORTER_OTLP_ENDPOINT``
    If set, traces are also POSTed to this OTLP/HTTP endpoint in addition to
    stdout.  Example: ``http://otel-collector:4318``
``OTEL_SERVICE_NAME``
    Service name tag attached to every span (default: ``sincor2``).
``SINCOR_TRACE_LEVEL``
    Minimum level to emit traces: ``debug`` | ``info`` (default) | ``error``.
    Set to ``off`` to disable all tracing.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Generator, Optional

logger = logging.getLogger("sincor.observability")

_SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "sincor2")
_OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
_TRACE_LEVEL = os.getenv("SINCOR_TRACE_LEVEL", "info").lower()

# ---------------------------------------------------------------------------
# Span model
# ---------------------------------------------------------------------------


@dataclass
class Span:
    """A single trace span recording an operation within SINCOR."""

    span_id: str
    trace_id: str
    operation_name: str
    start_time_ms: float
    service: str = _SERVICE_NAME
    task_id: Optional[str] = None
    skill_id: Optional[str] = None
    agent_id: Optional[str] = None
    status: str = "ok"
    error: Optional[str] = None
    duration_ms: Optional[float] = None
    attributes: Dict[str, Any] = field(default_factory=dict)

    def set_attribute(self, key: str, value: Any) -> None:
        """Set a custom attribute on this span."""
        self.attributes[key] = value

    def set_error(self, exc: Exception) -> None:
        """Mark this span as errored."""
        self.status = "error"
        self.error = f"{type(exc).__name__}: {exc}"

    def finish(self) -> Dict[str, Any]:
        """Finalise the span and return its serialisable representation."""
        self.duration_ms = round((time.monotonic() * 1000) - self.start_time_ms, 2)
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "operation": self.operation_name,
            "service": self.service,
            "task_id": self.task_id,
            "skill_id": self.skill_id,
            "agent_id": self.agent_id,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "attributes": self.attributes,
            "timestamp_ms": round(self.start_time_ms),
        }


# ---------------------------------------------------------------------------
# Tracer
# ---------------------------------------------------------------------------


class Tracer:
    """Thin tracer that emits JSON spans to stdout and optionally to OTLP."""

    def __init__(
        self,
        service_name: str = _SERVICE_NAME,
        otlp_endpoint: str = _OTLP_ENDPOINT,
        trace_level: str = _TRACE_LEVEL,
    ) -> None:
        self.service_name = service_name
        self.otlp_endpoint = otlp_endpoint.rstrip("/") if otlp_endpoint else ""
        self.trace_level = trace_level

    def start_span(
        self,
        operation_name: str,
        task_id: Optional[str] = None,
        skill_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> Span:
        """Create and return a new :class:`Span` (not yet finished)."""
        return Span(
            span_id=uuid.uuid4().hex[:16],
            trace_id=trace_id or uuid.uuid4().hex,
            operation_name=operation_name,
            start_time_ms=time.monotonic() * 1000,
            service=self.service_name,
            task_id=task_id,
            skill_id=skill_id,
            agent_id=agent_id,
        )

    def emit(self, span: Span) -> None:
        """Emit a finished span to stdout and optionally OTLP."""
        if self.trace_level == "off":
            return

        payload = span.finish()

        # Always emit to stdout as structured JSON (Railway-compatible)
        try:
            print(json.dumps({"sincor_trace": payload}), flush=True)
        except Exception:  # pragma: no cover
            pass

        # Optionally ship to OTLP endpoint (fire-and-forget)
        if self.otlp_endpoint:
            self._send_otlp(payload)

    def _send_otlp(self, payload: Dict[str, Any]) -> None:
        """Best-effort POST to an OTLP/HTTP collector (non-blocking)."""
        import threading
        import urllib.request

        def _post() -> None:
            try:
                body = json.dumps(
                    {
                        "resourceSpans": [
                            {
                                "resource": {
                                    "attributes": [
                                        {
                                            "key": "service.name",
                                            "value": {"stringValue": self.service_name},
                                        }
                                    ]
                                },
                                "scopeSpans": [
                                    {
                                        "spans": [
                                            {
                                                "traceId": payload["trace_id"],
                                                "spanId": payload["span_id"],
                                                "name": payload["operation"],
                                                "startTimeUnixNano": int(
                                                    payload["timestamp_ms"] * 1_000_000
                                                ),
                                                "endTimeUnixNano": int(
                                                    (payload["timestamp_ms"] + (payload.get("duration_ms") or 0))
                                                    * 1_000_000
                                                ),
                                                "status": {
                                                    "code": 2 if payload["status"] == "error" else 1
                                                },
                                                "attributes": [
                                                    {
                                                        "key": k,
                                                        "value": {"stringValue": str(v)},
                                                    }
                                                    for k, v in (payload.get("attributes") or {}).items()
                                                ],
                                            }
                                        ]
                                    }
                                ],
                            }
                        ]
                    }
                ).encode()
                req = urllib.request.Request(
                    f"{self.otlp_endpoint}/v1/traces",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=2):
                    pass
            except Exception as exc:
                logger.debug("OTLP export failed (non-fatal): %s", exc)

        threading.Thread(target=_post, daemon=True).start()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_tracer: Optional[Tracer] = None


def get_tracer() -> Tracer:
    """Return the module-level :class:`Tracer` singleton."""
    global _tracer
    if _tracer is None:
        _tracer = Tracer()
    return _tracer


# ---------------------------------------------------------------------------
# Context manager helper
# ---------------------------------------------------------------------------


@contextmanager
def trace_task(
    task_id: str,
    skill_id: str = "",
    agent_id: str = "",
    operation_name: str = "sincor.task",
    trace_id: Optional[str] = None,
) -> Generator[Span, None, None]:
    """Context manager that traces a task execution span.

    Emits a JSON trace event on exit (or on exception).

    Example::

        with trace_task(task_id="t-42", skill_id="healthcare-rcm",
                        agent_id="healthcare_rcm_agent") as span:
            result = agent.run(payload)
            span.set_attribute("result.status", result.get("status"))

    Parameters
    ----------
    task_id:
        The A2A task identifier.
    skill_id:
        The skill being executed (e.g. ``"healthcare-rcm"``).
    agent_id:
        The executing agent name.
    operation_name:
        Span operation name (default ``"sincor.task"``).
    trace_id:
        Optional existing trace ID for distributed tracing correlation.
    """
    tracer = get_tracer()
    span = tracer.start_span(
        operation_name=operation_name,
        task_id=task_id,
        skill_id=skill_id,
        agent_id=agent_id,
        trace_id=trace_id,
    )
    try:
        yield span
    except Exception as exc:
        span.set_error(exc)
        tracer.emit(span)
        raise
    else:
        tracer.emit(span)
