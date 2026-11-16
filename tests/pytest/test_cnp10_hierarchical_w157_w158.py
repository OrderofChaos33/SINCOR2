"""CNP-10 regression tests for hunt-50 findings W-157 and W-158.

W-157: hierarchical subcontracting had no budget conservation — a winner
could re-spend the parent budget across N subcontracts, each with its own
cap above the parent's. Fix: in ``subcontract``/``_register_node`` the sum
of child caps and the sum of committed child clearing prices must fit the
parent task's remaining budget (``max_price`` envelope).

W-158: ``register_root`` accepted a hand-built award (attacker-chosen
clearing price) sealed by a different party than the award winner. Fix:
the root branch requires seal signer == award winner and requires the
award to be present in the engine's history.

Attack shapes replicate red-team PoCs
``poc50_hier_stream_budget.py`` and ``poc50_hier_stream_sealchain.py`` §B3.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_SRC = str(ROOT / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from eth_account import Account  # noqa: E402

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
    AuthorizationError,
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


def agents(price_scale=1.0):
    return [
        _agent("atlas", 1.20 * price_scale),
        _agent("helix", 1.10 * price_scale),
        _agent("cinder", 1.35 * price_scale),
        _agent("onyx", 1.05 * price_scale),
        _agent("lyra", 1.25 * price_scale),
        _agent("vesta", 1.15 * price_scale),
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


def mint_child_seal(g, parent, child_task_id, winner_agent):
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
        signing_secret=winner_agent.signing_secret,
        max_depth=3,
        now=NOW,
    )


def winner_agent(ags, node):
    return next(a for a in ags if a.agent_id == node.award.winner_id)


# ---------------------------------------------------------------------------
# W-158: fabricated root awards
# ---------------------------------------------------------------------------


def _fabricated_award(task_id, winner_wallet, clearing=999_000_000):
    return Award(
        auction_id="0x" + "ab" * 32,
        task_id=task_id,
        winner_id="victim",
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


def _secp_graph():
    # Money-grade path: secp256k1 seals, HMAC default-deny (PoC B3 shape).
    return HierarchicalTaskGraph(
        ContractNetEngine(net_config()),
        HierarchicalConfig(max_depth=3),
        lambda aid: ("", ""),
    )


def test_fabricated_root_award_rejected():
    """PoC B3, history axis isolated: the seal IS signed by the claimed
    winner, but the award never ran in the engine."""
    g = _secp_graph()
    victim = Account.create()
    fake_task = task("fake-root", ("research",), 2_000_000)
    fake_award = _fabricated_award("fake-root", victim.address)
    seal = mint_nested_seal(
        g.engine.config,
        agent_did="did:sincor:victim",
        agent_address=victim.address,
        task_id="fake-root",
        expiry=NOW + 3600,
        nonce=1,
        depth=0,
        private_key=victim.key.hex(),
        now=NOW,
    )
    with pytest.raises(UnrecordedAward):
        g.register_root(fake_task, fake_award, seal, now=NOW)
    assert len(g) == 0
    assert g.engine.history() == []


def test_fabricated_root_award_attacker_sealed_rejected():
    """PoC B3 verbatim shape: fabricated award naming a victim winner,
    seal signed by the attacker."""
    g = _secp_graph()
    mallory = Account.create()
    victim = Account.create()
    fake_task = task("fake-root", ("research",), 2_000_000)
    fake_award = _fabricated_award("fake-root", victim.address)
    seal = mint_nested_seal(
        g.engine.config,
        agent_did="did:sincor:mallory",
        agent_address=mallory.address,
        task_id="fake-root",
        expiry=NOW + 3600,
        nonce=1,
        depth=0,
        private_key=mallory.key.hex(),
        now=NOW,
    )
    with pytest.raises(AuthorizationError):
        g.register_root(fake_task, fake_award, seal, now=NOW)
    assert len(g) == 0


def test_root_seal_winner_mismatch_rejected():
    """A real engine award, but the root seal is signed by a non-winner."""
    ags = agents()
    accts = {a.agent_id: Account.create() for a in ags}
    for a in ags:
        a.wallet = accts[a.agent_id].address
    g = _secp_graph()
    root_task = task("root-real", ("research",), 10_000_000)
    award = g.engine.run(root_task, ags, seed=11, now=NOW)
    assert award.winner_id
    attacker = Account.create()
    seal = mint_nested_seal(
        g.engine.config,
        agent_did="did:sincor:mallory",
        agent_address=attacker.address,
        task_id="root-real",
        expiry=NOW + 3600,
        nonce=1,
        depth=0,
        private_key=attacker.key.hex(),
        now=NOW,
    )
    with pytest.raises(AuthorizationError):
        g.register_root(root_task, award, seal, now=NOW)
    assert len(g) == 0


def test_root_award_from_another_engine_rejected():
    """An award produced by a *different* engine is not in this engine's
    history and cannot anchor a root, even sealed by its real winner."""
    ags = agents()
    accts = {a.agent_id: Account.create() for a in ags}
    for a in ags:
        a.wallet = accts[a.agent_id].address
    other_engine = ContractNetEngine(net_config())
    root_task = task("root-foreign", ("research",), 10_000_000)
    award = other_engine.run(root_task, ags, seed=11, now=NOW)
    winner_key = accts[award.winner_id].key.hex()
    g = _secp_graph()
    seal = mint_nested_seal(
        g.engine.config,
        agent_did=f"did:sincor:{award.winner_id}",
        agent_address=award.winner_wallet,
        task_id="root-foreign",
        expiry=NOW + 3600,
        nonce=1,
        depth=0,
        private_key=winner_key,
        now=NOW,
    )
    with pytest.raises(UnrecordedAward):
        g.register_root(root_task, award, seal, now=NOW)
    assert len(g) == 0


def test_register_root_with_real_engine_award_works():
    """Legitimate low-level root registration: real engine award sealed
    by its winner registers and verifies."""
    ags = agents()
    accts = {a.agent_id: Account.create() for a in ags}
    for a in ags:
        a.wallet = accts[a.agent_id].address
    g = _secp_graph()
    root_task = task("root-legit", ("research",), 10_000_000)
    award = g.engine.run(root_task, ags, seed=11, now=NOW)
    seal = mint_nested_seal(
        g.engine.config,
        agent_did=f"did:sincor:{award.winner_id}",
        agent_address=award.winner_wallet,
        task_id="root-legit",
        expiry=NOW + 3600,
        nonce=1,
        depth=0,
        private_key=accts[award.winner_id].key.hex(),
        now=NOW,
    )
    node = g.register_root(root_task, award, seal, now=NOW)
    assert node.depth == 0
    assert g.verify_graph(now=NOW) == 1


# ---------------------------------------------------------------------------
# W-157: budget conservation
# ---------------------------------------------------------------------------


def test_subcontract_cap_above_parent_budget_rejected():
    """PoC budget shape: root cap 2.0M, child cap 10.0M (5x parent)."""
    ags = agents()
    g = hmac_graph(ags, max_depth=3)
    root = g.run_root(task("root-budget", ("research", "analyze"), 2_000_000),
                      ags, seed=11, now=NOW)
    assert root.award.clearing_price == 1_200_000
    with pytest.raises(BudgetExceeded):
        g.subcontract(
            root.node_id,
            task("child-0", ("summarize", "curate"), 10_000_000),
            ags, seed=20, now=NOW,
        )
    assert len(g) == 1


def test_fanout_cap_sum_rejected():
    """Two children whose caps sum past the parent budget: the second is
    rejected even though each cap alone fits."""
    ags = agents()
    g = hmac_graph(ags, max_depth=3)
    root = g.run_root(task("root-fan", ("research",), 2_000_000),
                      ags, seed=11, now=NOW)
    parent_winner = winner_agent(ags, root)

    child1 = task("fan-1", ("summarize",), 1_500_000)
    award1 = g.engine.run(child1, ags, seed=20, now=NOW)
    seal1 = mint_child_seal(g, root, "fan-1", parent_winner)
    g.register_subcontract(root.node_id, child1, award1, seal1, now=NOW,
                           hmac_secret_resolver=hmac_secret_resolver(ags))

    child2 = task("fan-2", ("curate",), 1_500_000)
    award2 = g.engine.run(child2, ags, seed=21, now=NOW)
    seal2 = mint_child_seal(g, root, "fan-2", parent_winner)
    with pytest.raises(BudgetExceeded):
        g.register_subcontract(root.node_id, child2, award2, seal2, now=NOW,
                               hmac_secret_resolver=hmac_secret_resolver(ags))
    assert len(g) == 2


def test_fanout_clearing_sum_rejected():
    """Caps fit but realized clearing sums past the budget: rejected on
    the committed-clearing axis."""
    ags = agents()
    g = hmac_graph(ags, max_depth=3)
    root = g.run_root(task("root-clr", ("research",), 2_000_000),
                      ags, seed=11, now=NOW)
    parent_winner = winner_agent(ags, root)

    child1 = task("clr-1", ("summarize",), 1_500_000)
    award1 = g.engine.run(child1, ags, seed=20, now=NOW)
    assert award1.clearing_price == 1_200_000
    seal1 = mint_child_seal(g, root, "clr-1", parent_winner)
    g.register_subcontract(root.node_id, child1, award1, seal1, now=NOW,
                           hmac_secret_resolver=hmac_secret_resolver(ags))

    # Small cap (cap sum 1.9M <= 2M) but a claimed clearing of 1.2M:
    # clearing sum would be 2.4M > 2M budget.
    child2 = task("clr-2", ("curate",), 400_000)
    award2 = _fabricated_award("clr-2", parent_winner.wallet,
                               clearing=1_200_000)
    award2.winner_id = parent_winner.agent_id
    seal2 = mint_child_seal(g, root, "clr-2", parent_winner)
    with pytest.raises(BudgetExceeded):
        g.register_subcontract(root.node_id, child2, award2, seal2, now=NOW,
                               hmac_secret_resolver=hmac_secret_resolver(ags))
    assert len(g) == 2


def test_fanout_within_budget_works():
    """Legitimate fan-out inside the budget registers; the child that
    would cross the budget line is the one rejected."""
    cheap = agents(price_scale=0.25)  # bids ~0.26M-0.34M, clear under caps
    g = hmac_graph(cheap, max_depth=3)
    root = g.run_root(task("root-ok", ("research",), 2_000_000),
                      cheap, seed=11, now=NOW)
    n1 = g.subcontract(root.node_id, task("ok-1", ("summarize",), 800_000),
                       cheap, seed=20, now=NOW)
    n2 = g.subcontract(root.node_id, task("ok-2", ("curate",), 800_000),
                       cheap, seed=21, now=NOW)
    assert (n1.depth, n2.depth) == (1, 1)
    assert g.verify_graph(now=NOW) == 3
    # Committed caps are now 1.6M of the 2M envelope; a third 800k child
    # would commit 2.4M and is rejected.
    with pytest.raises(BudgetExceeded):
        g.subcontract(root.node_id, task("ok-3", ("validate",), 800_000),
                      cheap, seed=22, now=NOW)
    assert len(g) == 3
