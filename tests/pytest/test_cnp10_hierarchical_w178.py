"""CNP-10 regression tests for hunt-52 finding W-178.

W-178: ``register_subcontract`` accepted a hand-built child award that
never ran in the engine. The W-158 fix bound ROOT registration to
engine history + seal signer == award winner, but the child branch
performed no provenance check, so payable child awards could be minted
without any auction as long as the fabricated clearing fit the parent
budget cap. Fix: in ``_register_node`` the child branch requires the
award to be present in the engine's history (the same
``_award_in_engine_history`` match as the root branch), on top of the
existing winner binding for children (the child seal must be signed by
the parent award's winner). The provenance check runs after the W-157
budget check, so an over-budget fabricated award still reports
``BudgetExceeded``.

Attack shape replicates red-team PoC ``poc52a_hier.py`` §C.
"""

from __future__ import annotations

import sys
from dataclasses import replace as dreplace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_SRC = str(ROOT / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from marketplace.contract_net.eip712 import (  # noqa: E402
    demo_signing_secret,
    demo_wallet,
)
from marketplace.contract_net.engine import ContractNetEngine  # noqa: E402
from marketplace.contract_net.types import (  # noqa: E402
    BASE_SEPOLIA_CHAIN_ID,
    AgentProfile,
    Award,
    ContractNetConfig,
    TaskSpec,
)
from sincor2.hierarchical_cnp import (  # noqa: E402
    BudgetExceeded,
    HierarchicalConfig,
    HierarchicalTaskGraph,
    UnrecordedAward,
    mint_nested_seal,
)

NOW = 1_800_000_000
SKILLS = (
    "research", "analyze", "summarize", "curate",
    "validate", "monitor", "scrape", "prospect",
)


def _agent(agent_id, price_axm):
    return AgentProfile(
        agent_id=agent_id,
        name=agent_id,
        skills=SKILLS,
        wallet=demo_wallet(agent_id),
        tasks_completed=10,
        success_rate=0.9,
        true_min_price=int(price_axm * 1_000_000),
        estimated_tokens=400,
        signing_secret=demo_signing_secret(agent_id),
    )


def agents():
    return [
        _agent("atlas", 1.20),
        _agent("helix", 1.10),
        _agent("cinder", 1.35),
        _agent("onyx", 1.05),
        _agent("lyra", 1.25),
        _agent("vesta", 1.15),
    ]


def task(task_id, requirements, max_price):
    return TaskSpec(
        task_id=task_id,
        goal=f"goal for {task_id}",
        requirements=tuple(requirements),
        budget_tokens=1000,
        max_price=max_price,
    )


def net_config():
    return ContractNetConfig(chain_id=BASE_SEPOLIA_CHAIN_ID, allow_hmac_bids=True)


def hmac_graph(ags, **cfg_kw):
    cfg_kw.setdefault("allow_hmac_seals", True)
    table = {a.agent_id: ("", a.signing_secret) for a in ags}
    return HierarchicalTaskGraph(
        ContractNetEngine(net_config()),
        HierarchicalConfig(**cfg_kw),
        lambda aid: table[aid],
    )


def hmac_secret_resolver(ags):
    table = {a.wallet.lower(): a.signing_secret for a in ags}
    return lambda addr: table.get(addr.lower(), "")


def mint_child_seal(g, parent, child_task_id, parent_winner_agent):
    """Mint a legitimate child seal signed by the parent award's winner."""
    return mint_nested_seal(
        g.engine.config,
        agent_did=f"did:sincor:{parent.award.winner_id}",
        agent_address=parent.award.winner_wallet,
        task_id=child_task_id,
        expiry=NOW + 3600,
        nonce=g.next_seal_nonce(parent.award.winner_wallet),
        depth=parent.depth + 1,
        parent_seal_hash=parent.seal.seal_id,
        signing_secret=parent_winner_agent.signing_secret,
        max_depth=3,
        now=NOW,
    )


def winner_agent(ags, node):
    return next(a for a in ags if a.agent_id == node.award.winner_id)


def _fabricated_award(task_id, winner_id, winner_wallet, clearing):
    return Award(
        auction_id="0x" + "ab" * 32,
        task_id=task_id,
        winner_id=winner_id,
        winner_wallet=winner_wallet,
        winner_bid_price=clearing,
        clearing_price=clearing,
        savings_vs_first_price=0,
        mechanism="vickrey",
        junior_reserved=False,
        junior_winner=False,
        invites=[],
        bids=[],
        llm_calls_avoided=0,
        tokens_saved=0,
        valid_bid_count=0,
        rejected_bid_count=0,
        phase="cleared",
    )


def _rooted_graph(ags):
    g = hmac_graph(ags, max_depth=3)
    root = g.run_root(task("root-w178", ("research",), 2_000_000),
                      ags, seed=11, now=NOW)
    return g, root


# ---------------------------------------------------------------------------
# W-178: fabricated child awards
# ---------------------------------------------------------------------------


def test_fabricated_child_award_rejected():
    """PoC §C verbatim shape: legit root, then a hand-built child award
    (clearing fits the parent cap) that never ran in the engine. The
    child seal is properly signed by the parent award's winner — the
    award's provenance is the only defect."""
    ags = agents()
    g, root = _rooted_graph(ags)
    assert len(g.engine.history()) == 1  # only the root auction ran

    child = task("c-child-fake", ("summarize",), 1_900_000)
    fake = _fabricated_award(
        "c-child-fake", "mallory-accomplice", demo_wallet("lyra"), 1_900_000
    )
    seal = mint_child_seal(g, root, "c-child-fake", winner_agent(ags, root))
    with pytest.raises(UnrecordedAward):
        g.register_subcontract(root.node_id, child, fake, seal, now=NOW,
                               hmac_secret_resolver=hmac_secret_resolver(ags))
    assert len(g) == 1
    assert g.verify_graph(now=NOW,
                          hmac_secret_resolver=hmac_secret_resolver(ags)) == 1


def test_tampered_child_award_rejected():
    """A faithful copy of a real engine child award with the clearing
    price bumped by 1 no longer matches the engine's history."""
    ags = agents()
    g, root = _rooted_graph(ags)
    child = task("c-child-tampered", ("summarize",), 1_500_000)
    award = g.engine.run(child, ags, seed=30, now=NOW)
    tampered = dreplace(award, clearing_price=award.clearing_price + 1)
    seal = mint_child_seal(g, root, "c-child-tampered", winner_agent(ags, root))
    with pytest.raises(UnrecordedAward):
        g.register_subcontract(root.node_id, child, tampered, seal, now=NOW,
                               hmac_secret_resolver=hmac_secret_resolver(ags))
    assert len(g) == 1


def test_child_award_from_another_engine_rejected():
    """An award produced by a *different* engine is not in this engine's
    history and cannot anchor a subcontract, even sealed by the parent
    award's winner."""
    ags = agents()
    g, root = _rooted_graph(ags)
    child = task("c-child-foreign", ("summarize",), 1_500_000)
    other_engine = ContractNetEngine(net_config())
    award = other_engine.run(child, ags, seed=30, now=NOW)
    assert award.winner_id
    seal = mint_child_seal(g, root, "c-child-foreign", winner_agent(ags, root))
    with pytest.raises(UnrecordedAward):
        g.register_subcontract(root.node_id, child, award, seal, now=NOW,
                               hmac_secret_resolver=hmac_secret_resolver(ags))
    assert len(g) == 1


def test_overbudget_fabricated_child_reports_budget_first():
    """Ordering lock (W-157): a fabricated award whose clearing exceeds
    the parent budget is rejected on the budget axis before provenance
    is consulted, matching the pre-existing fan-out clearing test."""
    ags = agents()
    g, root = _rooted_graph(ags)
    child = task("c-child-over", ("summarize",), 1_900_000)
    fake = _fabricated_award(
        "c-child-over", "mallory-accomplice", demo_wallet("lyra"), 2_500_000
    )
    seal = mint_child_seal(g, root, "c-child-over", winner_agent(ags, root))
    with pytest.raises(BudgetExceeded):
        g.register_subcontract(root.node_id, child, fake, seal, now=NOW,
                               hmac_secret_resolver=hmac_secret_resolver(ags))
    assert len(g) == 1


# ---------------------------------------------------------------------------
# W-178: legitimate child awards still register
# ---------------------------------------------------------------------------


def test_register_subcontract_with_real_engine_award_works():
    """Legitimate low-level subcontract registration: real engine award
    sealed by the parent award's winner registers and verifies."""
    ags = agents()
    g, root = _rooted_graph(ags)
    child = task("c-child-legit", ("summarize",), 1_500_000)
    award = g.engine.run(child, ags, seed=30, now=NOW)
    seal = mint_child_seal(g, root, "c-child-legit", winner_agent(ags, root))
    node = g.register_subcontract(root.node_id, child, award, seal, now=NOW,
                                  hmac_secret_resolver=hmac_secret_resolver(ags))
    assert node.depth == 1
    assert g.verify_graph(now=NOW,
                          hmac_secret_resolver=hmac_secret_resolver(ags)) == 2
