"""Operational validation harness for three-tier retrieval + contract-net integration."""

from __future__ import annotations

import math
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

import numpy as np

from .vector_retrieval_engine import QuerySpec, ThreeTierVectorEngine, VectorRecord


@dataclass(frozen=True)
class ValidationThresholds:
    max_memory_factor: float = 2.0
    max_lock_contention_ms: float = 5.0
    min_hybrid_recall: float = 0.98
    max_alignment_drift: float = 0.01


@dataclass(frozen=True)
class ValidationResult:
    name: str
    ok: bool
    metrics: Dict[str, float]


def _mk_record(node_id: str, dim: int, token: str, created_at: float) -> VectorRecord:
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
        weight=1.0,
    )


def swap_headroom_test(
    *,
    ops_per_second: int = 10_000,
    duration_seconds: float = 1.0,
    dim: int = 32,
    thresholds: ValidationThresholds = ValidationThresholds(),
) -> ValidationResult:
    engine = ThreeTierVectorEngine(model_version="bench-v1", decay_lambda=0.0, epsilon=0.0)

    for i in range(400):
        engine.write(_mk_record(f"seed-{i}", dim, "seed", time.time()))
    engine.compact_warm()
    engine.stage_epoch_from_warm()
    engine.cutover()

    stop = threading.Event()
    latencies_ms: List[float] = []
    lat_lock = threading.Lock()

    def writer() -> None:
        i = 0
        while not stop.is_set():
            t0 = time.perf_counter()
            engine.write(_mk_record(f"w-{i}", dim, "write", time.time()))
            t1 = time.perf_counter()
            with lat_lock:
                latencies_ms.append((t1 - t0) * 1000.0)
            i += 1

    def reader() -> None:
        qv = np.ones(dim, dtype=np.float64)
        qv /= np.linalg.norm(qv) or 1.0
        while not stop.is_set():
            t0 = time.perf_counter()
            engine.query(
                QuerySpec(
                    query_vector=qv,
                    query_text="seed",
                    required_attributes={"entity": "bench"},
                    required_capabilities={"bench"},
                    k=5,
                )
            )
            t1 = time.perf_counter()
            with lat_lock:
                latencies_ms.append((t1 - t0) * 1000.0)

    tw = threading.Thread(target=writer, daemon=True)
    tr = threading.Thread(target=reader, daemon=True)
    tw.start()
    tr.start()

    deadline = time.time() + duration_seconds
    while time.time() < deadline:
        time.sleep(max(0.001, 1.0 / max(1, ops_per_second // 50)))

    engine.stage_epoch_from_warm(model_version="bench-v2")
    t_swap_0 = time.perf_counter()
    engine.cutover(max_pause_ms=5.0)
    t_swap_1 = time.perf_counter()

    stop.set()
    tw.join(timeout=1.0)
    tr.join(timeout=1.0)

    active = engine.swap.active_epoch()
    staged = engine.swap._staged_epoch  # noqa: SLF001
    active_bytes = float(engine.swap._estimate_bytes(active) if active is not None else 0)  # noqa: SLF001
    staged_bytes = float(engine.swap._estimate_bytes(staged) if staged is not None else 0)  # noqa: SLF001
    memory_factor = (active_bytes + staged_bytes) / max(1.0, active_bytes)
    p99 = float(np.percentile(np.array(latencies_ms or [0.0], dtype=np.float64), 99))
    swap_pause_ms = (t_swap_1 - t_swap_0) * 1000.0

    ok = (
        memory_factor <= thresholds.max_memory_factor
        and p99 <= thresholds.max_lock_contention_ms
        and swap_pause_ms <= thresholds.max_lock_contention_ms
    )
    return ValidationResult(
        name="swap_headroom",
        ok=ok,
        metrics={
            "memory_factor": memory_factor,
            "p99_lock_contention_ms": p99,
            "swap_pause_ms": swap_pause_ms,
        },
    )


def entropy_recall_decay_test(
    *,
    cycles: int = 100_000,
    dim: int = 32,
    sample_size: int = 128,
    thresholds: ValidationThresholds = ValidationThresholds(),
) -> ValidationResult:
    engine = ThreeTierVectorEngine(model_version="bench-v1", decay_lambda=1e-6, epsilon=0.0)
    now = time.time()

    for i in range(sample_size):
        engine.write(_mk_record(f"n-{i}", dim, "cap", now))
    engine.compact_warm()
    engine.stage_epoch_from_warm()
    engine.cutover()

    ids = [f"n-{i}" for i in range(sample_size)]
    qv = np.ones(dim, dtype=np.float64)
    qv /= np.linalg.norm(qv) or 1.0

    for i in range(max(1, cycles)):
        victim = ids[i % len(ids)]
        engine.delete(victim, rewire_targets={ids[(i + 1) % len(ids)]})
        engine.write(_mk_record(victim, dim, "cap", now + i * 1e-4))

    query = QuerySpec(
        query_vector=qv,
        query_text="cap",
        required_attributes={"entity": "bench", "schema": "default"},
        required_capabilities={"cap"},
        k=min(16, sample_size),
    )
    out = engine.query(query)

    # Brute-force baseline over active epoch + hot delta (pre-compaction comparison)
    active = engine.swap.active_epoch()
    records = dict(active.records) if active is not None else {}
    delta_records, tombstones = engine.delta.read_delta()
    for tid in tombstones:
        records.pop(tid, None)
    records.update(delta_records)
    if not records:
        return ValidationResult(
            name="entropy_recall_decay",
            ok=False,
            metrics={"cycles": float(cycles), "recall": 0.0, "baseline_size": 0.0},
        )

    candidates = []
    for rec in records.values():
        if rec.attributes.get("entity") != "bench" or rec.attributes.get("schema") != "default":
            continue
        if "cap" not in rec.capabilities:
            continue
        candidates.append(rec)
    if not candidates:
        return ValidationResult(
            name="entropy_recall_decay",
            ok=False,
            metrics={"cycles": float(cycles), "recall": 0.0, "baseline_size": 0.0},
        )

    qn = np.linalg.norm(qv) or 1.0
    qn_vec = qv / qn
    v_scores = {}
    l_scores = defaultdict(float)
    for rec in candidates:
        rn = np.linalg.norm(rec.vector) or 1.0
        v_scores[rec.node_id] = float(np.dot(rec.vector / rn, qn_vec))
        if "cap" in rec.text.lower():
            l_scores[rec.node_id] += 1.0
    l_max = max(l_scores.values()) if l_scores else 1.0

    brute = []
    for rec in candidates:
        v = v_scores.get(rec.node_id, 0.0)
        l = l_scores.get(rec.node_id, 0.0)
        combo = 0.7 * v + 0.3 * (l / max(l_max, 1e-9))
        brute.append((combo, rec.node_id))
    brute.sort(key=lambda x: (-x[0], x[1]))
    target = {node_id for _, node_id in brute[: query.k]}

    got = {row.node_id for row in out}
    overlap = len(got & target)
    recall = overlap / max(1, len(target))

    ok = recall >= thresholds.min_hybrid_recall
    return ValidationResult(
        name="entropy_recall_decay",
        ok=ok,
        metrics={
            "cycles": float(cycles),
            "recall": float(recall),
            "baseline_size": float(len(target)),
        },
    )


def alignment_precision_test(
    *,
    n_vectors: int = 4096,
    dim: int = 64,
    top_k: int = 16,
    thresholds: ValidationThresholds = ValidationThresholds(),
) -> ValidationResult:
    rng = np.random.default_rng(7)
    x = rng.normal(size=(n_vectors, dim)).astype(np.float64)
    x /= np.linalg.norm(x, axis=1, keepdims=True)

    q, _ = np.linalg.qr(rng.normal(size=(dim, dim)))
    y = x @ q

    # Recover rotation via Procrustes and evaluate top-k stability.
    u, _, vt = np.linalg.svd(x.T @ y, full_matrices=False)
    r = u @ vt
    y_hat = x @ r

    idx = rng.integers(0, n_vectors)
    qx = x[idx]

    sim_ref = x @ qx
    sim_aligned = y_hat @ (qx @ r)

    top_ref = np.argpartition(sim_ref, -top_k)[-top_k:]
    top_align = np.argpartition(sim_aligned, -top_k)[-top_k:]
    overlap = len(set(top_ref.tolist()) & set(top_align.tolist())) / float(top_k)

    cos_loss = np.mean(1.0 - np.sum(y * y_hat, axis=1))
    drift = 1.0 - overlap

    ok = drift <= thresholds.max_alignment_drift and cos_loss <= thresholds.max_alignment_drift
    return ValidationResult(
        name="alignment_precision",
        ok=ok,
        metrics={
            "cosine_loss": float(cos_loss),
            "topk_drift": float(drift),
        },
    )


def run_all_validations() -> Dict[str, ValidationResult]:
    return {
        "swap_headroom": swap_headroom_test(),
        "entropy_recall_decay": entropy_recall_decay_test(),
        "alignment_precision": alignment_precision_test(),
    }
