"""Live telemetry helpers for memory-engine health and governance signals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from .vector_retrieval_engine import ThreeTierVectorEngine


@dataclass(frozen=True)
class GraphEntropyTelemetry:
    tombstone_ratio: float
    delta_write_latency_ms: float
    memory_headroom_factor: float


def collect_graph_entropy_telemetry(engine: ThreeTierVectorEngine) -> GraphEntropyTelemetry:
    m = engine.telemetry_snapshot()
    write_latency = float(m.get("avg_write_latency_ms", 0.0))
    return GraphEntropyTelemetry(
        tombstone_ratio=float(m.get("tombstone_density", 0.0)),
        delta_write_latency_ms=write_latency,
        memory_headroom_factor=float(m.get("memory_headroom_factor", 1.0)),
    )


def telemetry_dict(engine: ThreeTierVectorEngine) -> Dict[str, float]:
    t = collect_graph_entropy_telemetry(engine)
    return {
        "tombstone_ratio": t.tombstone_ratio,
        "delta_write_latency_ms": t.delta_write_latency_ms,
        "memory_headroom_factor": t.memory_headroom_factor,
    }
