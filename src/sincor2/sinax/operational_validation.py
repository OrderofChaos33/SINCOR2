"""Phase benchmark and verification suite for deterministic memory routing."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Dict, List

import numpy as np

from .vector_retrieval_engine import QuerySpec, ThreeTierVectorEngine, VectorRecord


@dataclass(frozen=True)
class ValidationThresholds:
    max_memory_factor: float = 2.0
    max_lock_pause_ms: float = 5.0
    min_recall_at_10: float = 0.98
    max_top10_drift: float = 0.01


@dataclass(frozen=True)
class ValidationResult:
    name: str
    ok: bool
    metrics: Dict[str, float]


@dataclass(frozen=True)
class HeadroomProfile:
    ops_per_second: int = 10_000
    read_ratio: float = 0.80
    rebuild_interval_seconds: float = 60.0
    duration_seconds: float = 65.0
    vector_dim: int = 64


@dataclass(frozen=True)
class EntropyProfile:
    cycles: int = 100_000
    graph_nodes: int = 500_000
    query_count: int = 5_000
    vector_dim: int = 64
    k: int = 10


def _mk_record(node_id: str, dim: int, token: str, created_at: float, *, weight: float = 1.0) -> VectorRecord:
    rng = np.random.default_rng(abs(hash(node_id)) % (2**31 - 1))
    vec = rng.normal(size=dim).astype(np.float64)
    vec /= np.linalg.norm(vec) or 1.0
    return VectorRecord(
        node_id=node_id,
        vector=vec,
        text=f"{token} {node_id}",
        attributes={"entity": "bench", "schema": "default"},
        capabilities={"bench", token},
        created_at=created_at,
        weight=weight,
    )


def memory_headroom_and_swap_latency_test(
    profile: HeadroomProfile = HeadroomProfile(),
    thresholds: ValidationThresholds = ValidationThresholds(),
) -> ValidationResult:
    engine = ThreeTierVectorEngine(model_version="bench-v1", decay_lambda=1e-4, epsilon=0.01)
    now = time.time()

    seed_n = max(1024, min(50_000, profile.ops_per_second // 2))
    for i in range(seed_n):
        engine.write(_mk_record(f"seed-{i}", profile.vector_dim, "seed", now))
    engine.compact_warm()
    engine.stage_epoch_from_warm()
    engine.cutover()

    base = engine.swap.active_epoch()
    base_bytes = float(engine.swap._estimate_bytes(base) if base is not None else 1.0)  # noqa: SLF001

    stop = threading.Event()
    peak_headroom = 1.0
    lock = threading.Lock()
    mutation_ids = [f"seed-{i}" for i in range(seed_n)]

    def mixed_ops() -> None:
        nonlocal peak_headroom
        period = 1.0 / max(1, profile.ops_per_second)
        i = 0
        qv = np.ones(profile.vector_dim, dtype=np.float64)
        qv /= np.linalg.norm(qv) or 1.0
        while not stop.is_set():
            if (i % 10) < int(profile.read_ratio * 10):
                engine.query(
                    QuerySpec(
                        query_vector=qv,
                        query_text="seed",
                        required_attributes={"entity": "bench", "schema": "default"},
                        required_capabilities={"bench"},
                        k=10,
                    )
                )
            else:
                victim = mutation_ids[i % len(mutation_ids)]
                replacement = f"mut-{i}"
                engine.delete(victim, rewire_targets={replacement})
                engine.write(_mk_record(replacement, profile.vector_dim, "seed", time.time()))
                mutation_ids[i % len(mutation_ids)] = replacement

            active = engine.swap.active_epoch()
            staged = engine.swap._staged_epoch  # noqa: SLF001
            active_bytes = float(engine.swap._estimate_bytes(active) if active else 0.0)  # noqa: SLF001
            staged_bytes = float(engine.swap._estimate_bytes(staged) if staged else 0.0)  # noqa: SLF001
            cur = (active_bytes + staged_bytes) / max(1.0, base_bytes)
            with lock:
                if cur > peak_headroom:
                    peak_headroom = cur
            i += 1
            time.sleep(period)

    def rebuild_worker() -> None:
        while not stop.wait(profile.rebuild_interval_seconds):
            engine.compact_warm()
            engine.stage_epoch_from_warm(model_version="bench-v2")
            engine.cutover(max_pause_ms=5.0)

    t_ops = threading.Thread(target=mixed_ops, daemon=True)
    t_rebuild = threading.Thread(target=rebuild_worker, daemon=True)
    t_ops.start()
    t_rebuild.start()

    deadline = time.time() + profile.duration_seconds
    while time.time() < deadline:
        time.sleep(0.05)

    stop.set()
    t_ops.join(timeout=2.0)
    t_rebuild.join(timeout=2.0)

    telemetry = engine.telemetry_snapshot()
    max_pause = max(float(engine.swap.last_pause_ms), float(telemetry.get("last_swap_pause_ms", 0.0)))
    ok = (
        peak_headroom < thresholds.max_memory_factor
        and max_pause < thresholds.max_lock_pause_ms
    )
    return ValidationResult(
        name="memory_headroom_swap_latency",
        ok=ok,
        metrics={
            "peak_memory_factor": float(peak_headroom),
            "lock_pause_ms": float(max_pause),
            "ops_per_second_target": float(profile.ops_per_second),
            "read_ratio": float(profile.read_ratio),
        },
    )


def graph_entropy_and_recall_floor_test(
    profile: EntropyProfile = EntropyProfile(),
    thresholds: ValidationThresholds = ValidationThresholds(),
) -> ValidationResult:
    engine = ThreeTierVectorEngine(model_version="bench-v1", decay_lambda=1e-5, epsilon=0.01)
    now = time.time()

    for i in range(max(1, profile.graph_nodes)):
        engine.write(_mk_record(f"g-{i}", profile.vector_dim, "cap", now))
    engine.compact_warm()
    engine.stage_epoch_from_warm()
    engine.cutover()

    active_ids = [f"g-{i}" for i in range(max(1, profile.graph_nodes))]
    for i in range(max(1, profile.cycles)):
        victim = active_ids[i % len(active_ids)]
        replacement = f"g2-{i}"
        engine.delete(victim, rewire_targets={replacement})
        engine.write(_mk_record(replacement, profile.vector_dim, "cap", now + i * 1e-4))
        active_ids[i % len(active_ids)] = replacement

    recalls: List[float] = []
    qv_base = np.ones(profile.vector_dim, dtype=np.float64)
    qv_base /= np.linalg.norm(qv_base) or 1.0

    for q in range(max(1, profile.query_count)):
        qv = np.roll(qv_base, q % profile.vector_dim)
        query = QuerySpec(
            query_vector=qv,
            query_text="cap",
            required_attributes={"entity": "bench", "schema": "default"},
            required_capabilities={"cap"},
            k=profile.k,
        )
        got = engine.query(query)

        active = engine.swap.active_epoch()
        records = dict(active.records) if active is not None else {}
        delta_records, tombstones = engine.delta.read_delta()
        for tid in tombstones:
            records.pop(tid, None)
        records.update(delta_records)

        candidates = [
            rec
            for rec in records.values()
            if rec.attributes.get("entity") == "bench"
            and rec.attributes.get("schema") == "default"
            and "cap" in rec.capabilities
        ]

        sims = []
        for rec in candidates:
            rn = np.linalg.norm(rec.vector) or 1.0
            sim = float(np.dot(rec.vector / rn, qv))
            sims.append((sim, rec.node_id))
        sims.sort(key=lambda x: (-x[0], x[1]))
        baseline = {nid for _, nid in sims[: profile.k]}

        got_ids = {row.node_id for row in got}
        recall = len(got_ids & baseline) / max(1, len(baseline))
        recalls.append(recall)

    recall_at_10 = float(sum(recalls) / max(1, len(recalls)))
    ok = recall_at_10 >= thresholds.min_recall_at_10
    return ValidationResult(
        name="graph_entropy_recall_floor",
        ok=ok,
        metrics={
            "recall_at_10": recall_at_10,
            "cycles": float(profile.cycles),
            "graph_nodes": float(profile.graph_nodes),
            "query_count": float(profile.query_count),
        },
    )


def alignment_precision_and_drift_check(
    *,
    n_vectors: int = 10_000,
    dim: int = 64,
    top_k: int = 10,
    thresholds: ValidationThresholds = ValidationThresholds(),
) -> ValidationResult:
    rng = np.random.default_rng(17)
    x = rng.normal(size=(n_vectors, dim)).astype(np.float64)
    x /= np.linalg.norm(x, axis=1, keepdims=True)

    q, _ = np.linalg.qr(rng.normal(size=(dim, dim)))
    y = x @ q

    u, _, vt = np.linalg.svd(x.T @ y, full_matrices=False)
    r = u @ vt
    y_hat = x @ r

    sample = min(256, n_vectors)
    drifts = []
    for idx in rng.integers(0, n_vectors, size=sample):
        qx = x[idx]
        sim_ref = x @ qx
        sim_map = y_hat @ (qx @ r)
        top_ref = set(np.argpartition(sim_ref, -top_k)[-top_k:].tolist())
        top_map = set(np.argpartition(sim_map, -top_k)[-top_k:].tolist())
        drifts.append(1.0 - (len(top_ref & top_map) / float(top_k)))

    drift = float(sum(drifts) / max(1, len(drifts)))
    ok = drift < thresholds.max_top10_drift
    return ValidationResult(
        name="alignment_precision_drift",
        ok=ok,
        metrics={
            "top10_drift": drift,
            "n_vectors": float(n_vectors),
        },
    )


def run_phase1_suite(
    *,
    headroom_profile: HeadroomProfile = HeadroomProfile(),
    entropy_profile: EntropyProfile = EntropyProfile(),
    thresholds: ValidationThresholds = ValidationThresholds(),
) -> Dict[str, ValidationResult]:
    return {
        "memory_headroom_swap_latency": memory_headroom_and_swap_latency_test(headroom_profile, thresholds),
        "graph_entropy_recall_floor": graph_entropy_and_recall_floor_test(entropy_profile, thresholds),
        "alignment_precision_drift": alignment_precision_and_drift_check(thresholds=thresholds),
    }


# Backward-compatible aliases used by earlier tests/callers.
def swap_headroom_test(*, ops_per_second: int = 10_000, duration_seconds: float = 65.0, dim: int = 64) -> ValidationResult:
    return memory_headroom_and_swap_latency_test(
        HeadroomProfile(ops_per_second=ops_per_second, duration_seconds=duration_seconds, vector_dim=dim)
    )


def entropy_recall_decay_test(
    *,
    cycles: int = 100_000,
    dim: int = 64,
    sample_size: int = 500_000,
) -> ValidationResult:
    q_count = min(5_000, max(64, sample_size))
    return graph_entropy_and_recall_floor_test(
        EntropyProfile(cycles=cycles, graph_nodes=sample_size, query_count=q_count, vector_dim=dim, k=10)
    )


def alignment_precision_test(*, n_vectors: int = 10_000, dim: int = 64, top_k: int = 10) -> ValidationResult:
    return alignment_precision_and_drift_check(n_vectors=n_vectors, dim=dim, top_k=top_k)


def run_all_validations() -> Dict[str, ValidationResult]:
    return run_phase1_suite()
