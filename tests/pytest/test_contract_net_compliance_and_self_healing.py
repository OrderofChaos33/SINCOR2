from marketplace.contract_net.compliance_filter import ComplianceAttestationFilter
from marketplace.contract_net.engine import ContractNetEngine
from marketplace.contract_net.types import AgentProfile, TaskSpec
from sincor2.sinax.self_healing import ActiveRoutingTable, NodeHealthState, SelfHealingCoordinator
from sincor2.sinax.dispute_resolution import ConsensusDisputeResolver, ValidatorVote


def _agent(agent_id: str, wallet: str, *, region: str = "global", zk: str = "zk:ok") -> AgentProfile:
    return AgentProfile(
        agent_id=agent_id,
        name=agent_id,
        wallet=wallet,
        skills=("python", "ops"),
        true_min_price=10,
        estimated_tokens=100,
        success_rate=0.9,
        execution_budget=500,
        supported_schemas=("default",),
        region=region,
        zk_identity_proof=zk,
    )


def test_compliance_filter_blocks_sanctions_region_and_missing_zk():
    task = TaskSpec(
        task_id="t1",
        goal="optimize",
        requirements=("python",),
        budget_tokens=100,
        max_price=100,
        allowed_regions=("us",),
        require_zk_identity=True,
    )
    allowed = _agent("a1", "0x111", region="us", zk="zk:proof")
    bad_region = _agent("a2", "0x222", region="eu", zk="zk:proof")
    missing_zk = _agent("a3", "0x333", region="us", zk="")
    sanctioned = _agent("a4", "0x444", region="us", zk="zk:proof")

    filt = ComplianceAttestationFilter(sanctioned_wallets=["0x444"])
    kept = filt.prefilter(task, [allowed, bad_region, missing_zk, sanctioned])

    assert [a.agent_id for a in kept] == ["a1"]


def test_contract_net_engine_enforces_compliance_before_allocation():
    task = TaskSpec(
        task_id="t2",
        goal="ops",
        requirements=("python",),
        budget_tokens=100,
        max_price=100,
        allowed_regions=("us",),
        require_zk_identity=True,
    )
    good = _agent("good", "0xabc", region="us", zk="zk:proof")
    blocked = _agent("blocked", "0xdef", region="us", zk="")

    engine = ContractNetEngine(compliance_filter=ComplianceAttestationFilter())
    shortlisted = engine._prefilter_and_rank_agents(task, [good, blocked])

    assert [a.agent_id for a in shortlisted] == ["good"]


def test_self_healing_quarantines_and_reinstates_nodes():
    table = ActiveRoutingTable()
    table.seed(["n1", "n2", "n3"])
    coordinator = SelfHealingCoordinator(table)

    report1 = coordinator.reconcile(
        expected_epoch="e1",
        states=[
            NodeHealthState(node_id="n1", epoch_id="e1", determinism_ok=True, sync_ok=True),
            NodeHealthState(node_id="n2", epoch_id="e0", determinism_ok=True, sync_ok=False),
            NodeHealthState(node_id="n3", epoch_id="e1", determinism_ok=False, sync_ok=True),
        ],
    )

    assert report1.quarantined == {"n2": "epoch_sync_failed", "n3": "determinism_failed"}
    assert table.active_nodes == {"n1"}

    report2 = coordinator.reconcile(
        expected_epoch="e1",
        states=[
            NodeHealthState(node_id="n2", epoch_id="e1", determinism_ok=True, sync_ok=True),
            NodeHealthState(node_id="n3", epoch_id="e1", determinism_ok=True, sync_ok=True),
        ],
    )

    assert report2.reinstated == {"n2", "n3"}
    assert table.active_nodes == {"n1", "n2", "n3"}


def test_consensus_dispute_resolver_quarantines_on_invalid_quorum():
    table = ActiveRoutingTable(active_nodes={"agent-1"})
    resolver = ConsensusDisputeResolver(table, quorum_bps=6700)

    outcome = resolver.resolve(
        agent_id="agent-1",
        votes=[
            ValidatorVote(validator_id="v1", proof_valid=False),
            ValidatorVote(validator_id="v2", proof_valid=False),
            ValidatorVote(validator_id="v3", proof_valid=True),
        ],
    )

    assert outcome.slash
    assert outcome.quarantine
    assert table.quarantined_nodes["agent-1"] == "invalid_execution_proof"
