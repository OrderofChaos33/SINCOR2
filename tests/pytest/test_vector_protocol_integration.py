from __future__ import annotations

import time

import numpy as np

from marketplace.contract_net.engine import ContractNetEngine
from marketplace.contract_net.hybrid_router import TaskAuctionMemoryRouter
from marketplace.contract_net.types import AgentProfile, ContractNetConfig, TaskSpec
from sincor2.sinax.operational_validation import (
    alignment_precision_test,
    entropy_recall_decay_test,
    swap_headroom_test,
)
from sincor2.sinax.swarm_protocol_lock import (
    assert_memory_engine_interface_lock,
    simulate_swarm_determinism,
)
from sincor2.sinax.node_package import build_node_package_manifest
from sincor2.sinax.vector_retrieval_engine import QuerySpec


def _agent(agent_id: str, skills: tuple[str, ...], *, schemas=("default",), budget=2000) -> AgentProfile:
    return AgentProfile(
        agent_id=agent_id,
        name=agent_id,
        skills=skills,
        wallet="0x" + ("%040x" % (abs(hash(agent_id)) % (1 << 160))),
        tasks_completed=10,
        success_rate=0.9,
        true_min_price=1_000_000,
        estimated_tokens=500,
        supported_schemas=schemas,
        execution_budget=budget,
        signing_secret="11" * 32,
    )


def test_contract_net_bids_embed_epoch_binding_fields():
    router = TaskAuctionMemoryRouter()
    engine = ContractNetEngine(ContractNetConfig(invite_k=3), memory_router=router)

    agents = [
        _agent("agent-a", ("research", "scrape", "analyze"), schemas=("a2a-v1",), budget=5000),
        _agent("agent-b", ("research",), schemas=("default",), budget=300),
        _agent("agent-c", ("deploy",), schemas=("a2a-v1",), budget=5000),
    ]
    task = TaskSpec(
        task_id="t-epoch",
        goal="research market",
        requirements=("research", "analyze"),
        budget_tokens=900,
        max_price=2_000_000,
        required_schema="a2a-v1",
        runtime_capabilities=("research",),
        execution_budget=800,
    )

    award = engine.run(task, agents, seed=1, force_junior=False)
    assert award.epoch_id.startswith("E_")
    assert award.epoch_merkle_root
    assert award.bids
    for bid in award.bids:
        assert bid.epoch_id == award.epoch_id
        assert bid.epoch_merkle_root == award.epoch_merkle_root
        assert bid.epoch_binding_hash.startswith("0x")


def test_router_prefilter_excludes_non_matching_agents():
    router = TaskAuctionMemoryRouter()
    agents = [
        _agent("fit", ("develop", "test"), schemas=("a2a-v1",), budget=2500),
        _agent("bad-schema", ("develop", "test"), schemas=("default",), budget=2500),
        _agent("bad-cap", ("monitor",), schemas=("a2a-v1",), budget=2500),
        _agent("bad-budget", ("develop", "test"), schemas=("a2a-v1",), budget=200),
    ]
    task = TaskSpec(
        task_id="t-prefilter",
        goal="ship deploy",
        requirements=("develop", "test"),
        budget_tokens=1000,
        max_price=2_000_000,
        required_schema="a2a-v1",
        runtime_capabilities=("develop",),
        execution_budget=800,
    )

    router.index_agents(agents)
    routed = router.route_agents(
        task,
        agents,
        required_schema=task.required_schema,
        runtime_capabilities=task.runtime_capabilities,
        execution_budget=task.execution_budget,
        top_k=5,
    )

    assert routed
    assert {r.agent.agent_id for r in routed} == {"fit"}


def test_temporal_decay_expires_bid_states_on_warm_merge():
    router = TaskAuctionMemoryRouter(bid_state_ttl_seconds=1)
    agent = _agent("temp", ("monitor",), schemas=("default",), budget=1000)
    router.ingest_bid_state(bid_id="b1", task_id="task-z", agent=agent, state="submitted", confidence=1.0)
    router.engine.compact_warm()
    router.engine.stage_epoch_from_warm()
    router.engine.cutover()

    time.sleep(1.05)
    router.engine.compactor.compact_once()
    router.engine.stage_epoch_from_warm()
    router.engine.cutover()

    active = router.engine.swap.active_epoch()
    assert active is not None
    out = router.engine.query(
        QuerySpec(
            query_vector=np.array([1.0] * 64, dtype=np.float64),
            query_text="submitted",
            required_attributes={"entity": "bid_state", "task_id": "task-z"},
            required_capabilities={"monitor"},
            k=3,
            epoch_id=active.epoch_id,
        )
    )
    assert out == []


def test_validation_benchmarks_and_swarm_determinism_contracts():
    headroom = swap_headroom_test(ops_per_second=500, duration_seconds=0.2, dim=8)
    entropy = entropy_recall_decay_test(cycles=200, dim=8, sample_size=32)
    align = alignment_precision_test(n_vectors=512, dim=16, top_k=8)

    assert set(headroom.metrics.keys()) >= {"memory_factor", "p99_lock_contention_ms", "swap_pause_ms"}
    assert set(entropy.metrics.keys()) >= {"cycles", "recall", "baseline_size"}
    assert set(align.metrics.keys()) >= {"cosine_loss", "topk_drift"}

    report = simulate_swarm_determinism(nodes=3, queries=32, dim=8)
    assert report.ok
    assert report.route_digest
    assert assert_memory_engine_interface_lock()
    manifest = build_node_package_manifest("1.2.3")
    assert manifest["interface_lock"]
    assert manifest["deterministic_routing"] is True
