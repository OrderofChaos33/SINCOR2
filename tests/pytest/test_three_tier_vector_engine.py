from __future__ import annotations

import time

import numpy as np

from sincor2.sinax.vector_retrieval_engine import QuerySpec, ThreeTierVectorEngine, VectorRecord


def _rec(node_id: str, vec, text: str, attrs=None, caps=None, created_at=None, weight=1.0):
    return VectorRecord(
        node_id=node_id,
        vector=np.array(vec, dtype=np.float64),
        text=text,
        attributes=attrs or {},
        capabilities=set(caps or set()),
        created_at=created_at if created_at is not None else time.time(),
        weight=weight,
    )


def _bootstrap_epoch(engine: ThreeTierVectorEngine):
    engine.compact_warm()
    engine.stage_epoch_from_warm()
    engine.cutover()


def test_prefilter_runs_before_ann_and_limits_candidates():
    engine = ThreeTierVectorEngine(decay_lambda=0.0, epsilon=0.0)
    engine.write(_rec("n1", [0.0, 1.0, 0.0], "aero gauge vote", {"region": "us"}, {"trade"}))
    engine.write(_rec("n2", [1.0, 0.0, 0.0], "psm arbitrage", {"region": "eu"}, {"hedge"}))
    _bootstrap_epoch(engine)

    # Vector is closest to n2, but hard pre-filter must restrict to n1.
    out = engine.query(
        QuerySpec(
            query_vector=np.array([1.0, 0.0, 0.0], dtype=np.float64),
            query_text="arbitrage",
            required_attributes={"region": "us"},
            required_capabilities={"trade"},
            k=5,
        )
    )

    assert out
    assert {r.node_id for r in out} == {"n1"}


def test_hybrid_floor_always_merges_lexical_candidates():
    engine = ThreeTierVectorEngine(decay_lambda=0.0, epsilon=0.0)
    engine.write(_rec("v1", [1.0, 0.0], "neutral text", {"lane": "x"}, {"serve"}))
    engine.write(_rec("v2", [0.0, 1.0], "rarekeyword anchor", {"lane": "x"}, {"serve"}))
    _bootstrap_epoch(engine)

    out = engine.query(
        QuerySpec(
            query_vector=np.array([1.0, 0.0], dtype=np.float64),
            query_text="rarekeyword",
            required_attributes={"lane": "x"},
            required_capabilities={"serve"},
            k=3,
        )
    )

    ids = [r.node_id for r in out]
    assert "v2" in ids  # lexical floor inclusion


def test_warm_compaction_temporal_decay_prunes_stale_records():
    now = time.time()
    engine = ThreeTierVectorEngine(decay_lambda=0.01, epsilon=0.5)
    engine.write(_rec("fresh", [1.0, 0.0], "fresh", created_at=now, weight=1.0))
    engine.write(_rec("stale", [0.0, 1.0], "stale", created_at=now - 1500.0, weight=1.0))

    warm = engine.compact_warm()
    assert "fresh" in warm.records
    assert "stale" not in warm.records


def test_epoch_commitments_are_deterministic_for_same_payload():
    engine = ThreeTierVectorEngine(decay_lambda=0.0, epsilon=0.0)
    now = time.time()
    records = {
        "a": _rec("a", [1.0, 0.0], "alpha", created_at=now),
        "b": _rec("b", [0.0, 1.0], "beta", created_at=now),
    }

    e1 = engine.builder.build(records, model_version="v1", parent_epoch=None)
    e2 = engine.builder.build(records, model_version="v1", parent_epoch=None)

    assert e1.epoch_id == e2.epoch_id
    assert e1.merkle_root == e2.merkle_root
    assert e1.manifest_hash == e2.manifest_hash


def test_two_phase_cutover_flushes_final_delta_into_new_epoch():
    engine = ThreeTierVectorEngine(decay_lambda=0.0, epsilon=0.0)
    engine.write(_rec("base", [1.0, 0.0], "base"))
    _bootstrap_epoch(engine)

    # Stage next epoch first.
    engine.stage_epoch_from_warm(model_version="v2")
    # Final writes land in current delta and must be flushed in cutover.
    engine.write(_rec("late", [0.0, 1.0], "late write", {"tier": "hot"}, {"exec"}))
    engine.cutover(max_pause_ms=5.0)

    out = engine.query(
        QuerySpec(
            query_vector=np.array([0.0, 1.0], dtype=np.float64),
            query_text="late",
            required_attributes={"tier": "hot"},
            required_capabilities={"exec"},
            k=5,
        )
    )

    assert any(r.node_id == "late" for r in out)
