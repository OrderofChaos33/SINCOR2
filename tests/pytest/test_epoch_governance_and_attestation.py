from __future__ import annotations

import tempfile
import time

import numpy as np

from sincor2.onchain.epoch_commitment_pipeline import EpochStateCommitmentPipeline
from sincor2.sinax.epoch_attestation import ERC7579EpochSessionValidator, publish_epoch_manifest
from sincor2.sinax.epoch_governor import AutomatedEpochGovernor, EpochGovernanceConfig
from sincor2.sinax.node_package import build_node_package_manifest
from sincor2.sinax.production_telemetry import telemetry_dict
from sincor2.sinax.swarm_protocol_lock import simulate_swarm_determinism
from sincor2.sinax.vector_retrieval_engine import QuerySpec, ThreeTierVectorEngine, VectorRecord


def _bootstrap(engine: ThreeTierVectorEngine):
    engine.compact_warm()
    engine.stage_epoch_from_warm()
    engine.cutover()


def _write(engine: ThreeTierVectorEngine, node_id: str, token: str = "cap"):
    vec = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float64)
    engine.write(
        VectorRecord(
            node_id=node_id,
            vector=vec,
            text=token,
            attributes={"entity": "agent", "schema": "default"},
            capabilities={"cap"},
            created_at=time.time(),
        )
    )


def test_epoch_manifest_and_attestation_pipeline():
    engine = ThreeTierVectorEngine(model_version="v1", decay_lambda=0.0, epsilon=0.0)
    _write(engine, "n1")
    _bootstrap(engine)

    validator = ERC7579EpochSessionValidator(engine.swap)
    proof = validator.attest_payload({"task": "settle", "amount": 1})
    assert proof.epoch_id.startswith("E_")
    assert validator.validate(proof)

    pipeline = EpochStateCommitmentPipeline(engine)
    env = pipeline.build_envelope({"task": "settle", "amount": 1})
    assert env.epoch_id == proof.epoch_id
    assert pipeline.verify_envelope(env)

    with tempfile.TemporaryDirectory() as td:
        out = publish_epoch_manifest(engine.swap, f"{td}/epoch_manifest.json")
        assert out["epoch_id"] == proof.epoch_id
        assert out["merkle_root"] == proof.epoch_merkle_root


def test_automated_epoch_governor_triggers_on_density_model_and_blocks():
    engine = ThreeTierVectorEngine(model_version="v1", decay_lambda=0.0, epsilon=0.01)
    _write(engine, "a")
    _bootstrap(engine)

    gov = AutomatedEpochGovernor(engine, EpochGovernanceConfig(tombstone_density_trigger=0.15, block_interval_trigger=10))

    # Trigger by block interval.
    assert gov.trigger_if_needed(current_block=10)

    # Trigger by model upgrade.
    assert gov.trigger_if_needed(current_block=11, model_version="v2")

    # Trigger by tombstone density.
    for i in range(5):
        engine.delete(f"missing-{i}")
    assert gov.should_trigger(current_block=12)


def test_telemetry_and_node_manifest_include_frozen_contracts():
    engine = ThreeTierVectorEngine(model_version="v1", decay_lambda=0.0, epsilon=0.0)
    _write(engine, "x")
    _bootstrap(engine)

    metrics = telemetry_dict(engine)
    assert set(metrics.keys()) == {"tombstone_ratio", "delta_write_latency_ms", "memory_headroom_factor"}

    active = engine.swap.active_epoch()
    manifest = build_node_package_manifest("2.0.0", epoch_id=active.epoch_id, merkle_root=active.merkle_root)
    assert manifest["frozen_contracts"]
    assert manifest["epoch_id"] == active.epoch_id


def test_multinode_determinism_uses_five_nodes_and_latency():
    report = simulate_swarm_determinism(nodes=5, queries=24, dim=8, latency_ms_min=0.1, latency_ms_max=0.5)
    assert report.ok
    assert report.nodes == 5


def test_query_contract_aliases_exist_and_work():
    engine = ThreeTierVectorEngine(model_version="v1", decay_lambda=0.0, epsilon=0.0)
    _write(engine, "qa")
    engine.CompactWarmSegment()
    engine.stage_epoch_from_warm()
    engine.SwapColdEpoch()

    out = engine.QueryPreFilter(
        QuerySpec(
            query_vector=np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float64),
            query_text="cap",
            required_attributes={"entity": "agent", "schema": "default"},
            required_capabilities={"cap"},
            k=3,
        )
    )
    assert out
