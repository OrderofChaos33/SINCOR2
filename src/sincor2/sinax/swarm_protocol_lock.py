"""Deterministic swarm simulation and memory-engine interface lock."""

from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass
from typing import Dict, List, Sequence

import numpy as np

from .vector_retrieval_engine import QuerySpec, ThreeTierVectorEngine, VectorRecord


@dataclass(frozen=True)
class SwarmDeterminismReport:
    ok: bool
    route_digest: str
    nodes: int
    queries: int


def _route_digest(routes: Sequence[Sequence[str]]) -> str:
    payload = "|".join(",".join(route) for route in routes)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def simulate_swarm_determinism(*, nodes: int = 4, queries: int = 128, dim: int = 16) -> SwarmDeterminismReport:
    engines: List[ThreeTierVectorEngine] = [ThreeTierVectorEngine(model_version="swarm-v1", decay_lambda=0.0, epsilon=0.0) for _ in range(nodes)]

    records = []
    for i in range(64):
        vec = np.zeros(dim, dtype=np.float64)
        vec[i % dim] = 1.0
        records.append(
            VectorRecord(
                node_id=f"agent-{i}",
                vector=vec,
                text=f"agent capability-{i % 5}",
                attributes={"entity": "agent", "schema": "default"},
                capabilities={"capability", f"capability-{i % 5}"},
            )
        )

    # Build identical state across all nodes.
    for engine in engines:
        for rec in records:
            engine.write(rec)
        engine.compact_warm()
        engine.stage_epoch_from_warm(model_version="swarm-v1")
        engine.cutover()

    all_route_sets = []
    for engine in engines:
        node_routes: List[List[str]] = []
        for q in range(queries):
            vec = np.zeros(dim, dtype=np.float64)
            vec[q % dim] = 1.0
            out = engine.query(
                QuerySpec(
                    query_vector=vec,
                    query_text=f"capability-{q % 5}",
                    required_attributes={"entity": "agent", "schema": "default"},
                    required_capabilities={"capability"},
                    k=5,
                )
            )
            node_routes.append([row.node_id for row in out])
        all_route_sets.append(node_routes)

    digests = [_route_digest(routes) for routes in all_route_sets]
    ok = len(set(digests)) == 1
    digest = digests[0] if digests else ""
    return SwarmDeterminismReport(ok=ok, route_digest=digest, nodes=nodes, queries=queries)


def memory_engine_interface_digest() -> str:
    classes = [
        ThreeTierVectorEngine,
    ]
    symbols: List[str] = []
    for cls in classes:
        for name, member in sorted(inspect.getmembers(cls)):
            if name.startswith("_"):
                continue
            if callable(member):
                try:
                    sig = str(inspect.signature(member))
                except (TypeError, ValueError):
                    sig = "()"
                symbols.append(f"{cls.__name__}.{name}{sig}")
    raw = "\n".join(symbols)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


MEMORY_ENGINE_INTERFACE_LOCK_V1 = memory_engine_interface_digest()


def assert_memory_engine_interface_lock(expected_digest: str = MEMORY_ENGINE_INTERFACE_LOCK_V1) -> bool:
    return memory_engine_interface_digest() == expected_digest
