"""CNP-10 regression tests for hunt-50 finding W-164.

W-164: hierarchical subcontracting had no capability subset rule — a
parent seal holding a narrow capability set (e.g. ``research:read``)
could spawn a child seal claiming arbitrary capabilities
(``treasury:release``, ``stake:slash``) and the graph verified. Fix:
capability attenuation — a child seal's capabilities must be a subset
of its parent seal's capabilities, enforced in ``_register_node`` (the
registration choke point, so ``register_subcontract`` is covered too)
and pre-checked in ``subcontract`` before an auction is spent. Root
registration is unaffected.

Attack shape replicates red-team PoC
``poc50_hier_stream_capescal.py`` §D1.
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

from marketplace.contract_net.eip712 import (  # noqa: E402
    demo_signing_secret,
    demo_wallet,
)
from marketplace.contract_net.engine import ContractNetEngine  # noqa: E402
from marketplace.contract_net.types import (  # noqa: E402
    BASE_SEPOLIA_CHAIN_ID,
    AgentProfile,
    ContractNetConfig,
    TaskSpec,
)
from sincor2.hierarchical_cnp import (  # noqa: E402
    CapabilityEscalation,
    HierarchicalConfig,
    HierarchicalTaskGraph,
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


def task(task_id, requirements, max_price=10_000_000):
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


def mint_child_seal(g, parent, child_task_id, capabilities=()):
    """Mint a legitimate child seal signed by the parent award's winner."""
    return mint_nested_seal(
        g.engine.config,
        agent_did=f"did:sincor:{parent.award.winner_id}",
        agent_address=parent.award.winner_wallet,
        task_id=child_task_id,
        capabilities=capabilities,
        expiry=NOW + 3600,
        nonce=g.next_seal_nonce(parent.award.winner_wallet),
        depth=parent.depth + 1,
        parent_seal_hash=parent.seal.seal_id,
        signing_secret=demo_signing_secret(parent.award.winner_id),
        max_depth=3,
        now=NOW,
    )


# ---------------------------------------------------------------------------
# W-164: escalation rejected
# ---------------------------------------------------------------------------


def test_subcontract_capability_escalation_rejected():
    # PoC §D1 shape: read-only parent, child self-grants treasury/stake caps.
    ags = agents()
    g = hmac_graph(ags)
    n0 = g.run_root(task("root-cap", ("research",)), ags, seed=11, now=NOW,
                    capabilities=("research:read",))
    with pytest.raises(CapabilityEscalation):
        g.subcontract(n0.node_id, task("cap-1", ("summarize",), 2_000_000),
                      ags, seed=12, now=NOW,
                      capabilities=("treasury:release", "stake:slash"))
    # Fail-fast: no auction was spent and nothing was registered.
    assert len(g) == 1
    assert g.verify_graph(now=NOW) == 1


def test_subcontract_partial_overlap_rejected():
    # One held capability does not launder an unheld one.
    ags = agents()
    g = hmac_graph(ags)
    n0 = g.run_root(task("root-part", ("research",)), ags, seed=21, now=NOW,
                    capabilities=("research:read",))
    with pytest.raises(CapabilityEscalation):
        g.subcontract(n0.node_id, task("part-1", ("summarize",), 2_000_000),
                      ags, seed=22, now=NOW,
                      capabilities=("research:read", "payments.release"))


def test_register_subcontract_escalated_seal_rejected():
    # The choke point itself enforces the rule: an otherwise fully
    # legitimate child seal (signed by the parent winner, real engine
    # award, correct depth/hash) is rejected on capabilities alone.
    ags = agents()
    g = hmac_graph(ags)
    n0 = g.run_root(task("root-reg", ("research",)), ags, seed=31, now=NOW,
                    capabilities=("research:read",))
    child = task("reg-1", ("summarize",), 2_000_000)
    award = g.engine.run(child, ags, seed=32, now=NOW)
    seal = mint_child_seal(g, n0, "reg-1",
                           capabilities=("treasury:release",))
    with pytest.raises(CapabilityEscalation):
        g.register_subcontract(n0.node_id, child, award, seal, now=NOW,
                               hmac_secret_resolver=hmac_secret_resolver(ags))
    assert len(g) == 1


def test_grandchild_cannot_reclaim_dropped_capability():
    # Attenuation is transitive: a capability dropped at L1 cannot
    # reappear at L2, because L2 is checked against L1, not the root.
    ags = agents()
    g = hmac_graph(ags)
    n0 = g.run_root(task("root-att", ("research",)), ags, seed=41, now=NOW,
                    capabilities=("research:read", "data:write",
                                  "subcontract:award"))
    n1 = g.subcontract(n0.node_id, task("att-1", ("summarize",), 3_000_000),
                       ags, seed=42, now=NOW,
                       capabilities=("research:read", "data:write"))
    with pytest.raises(CapabilityEscalation):
        g.subcontract(n1.node_id, task("att-2", ("validate",), 2_000_000),
                      ags, seed=43, now=NOW,
                      capabilities=("research:read", "subcontract:award"))
    n2 = g.subcontract(n1.node_id, task("att-2", ("validate",), 2_000_000),
                       ags, seed=44, now=NOW,
                       capabilities=("research:read",))
    assert n2.seal.capabilities == ("research:read",)
    assert g.verify_graph(now=NOW) == 3


# ---------------------------------------------------------------------------
# W-164: legitimate attenuation still works
# ---------------------------------------------------------------------------


def test_subcontract_true_subset_registers_and_verifies():
    ags = agents()
    g = hmac_graph(ags)
    n0 = g.run_root(task("root-sub", ("research",)), ags, seed=51, now=NOW,
                    capabilities=("research:read", "data:write",
                                  "subcontract:award"))
    n1 = g.subcontract(n0.node_id, task("sub-1", ("summarize",), 2_000_000),
                       ags, seed=52, now=NOW,
                       capabilities=("research:read",))
    assert n1.seal.capabilities == ("research:read",)
    assert set(n1.seal.capabilities) <= set(n0.seal.capabilities)
    assert g.verify_graph(now=NOW) == 2


def test_subcontract_equal_capabilities_allowed():
    # Attenuation permits equality: a child may hold everything the
    # parent holds, just never more.
    ags = agents()
    g = hmac_graph(ags)
    caps = ("research:read", "subcontract:award")
    n0 = g.run_root(task("root-eq", ("research",)), ags, seed=61, now=NOW,
                    capabilities=caps)
    n1 = g.subcontract(n0.node_id, task("eq-1", ("summarize",), 2_000_000),
                       ags, seed=62, now=NOW, capabilities=caps)
    assert n1.seal.capabilities == caps
    assert g.verify_graph(now=NOW) == 2


def test_register_subcontract_subset_seal_registers():
    # Low-level path: a minted child seal inside the parent's caps
    # registers through the same choke point.
    ags = agents()
    g = hmac_graph(ags)
    n0 = g.run_root(task("root-low", ("research",)), ags, seed=71, now=NOW,
                    capabilities=("research:read", "data:write"))
    child = task("low-1", ("summarize",), 2_000_000)
    award = g.engine.run(child, ags, seed=72, now=NOW)
    seal = mint_child_seal(g, n0, "low-1", capabilities=("data:write",))
    node = g.register_subcontract(
        n0.node_id, child, award, seal, now=NOW,
        hmac_secret_resolver=hmac_secret_resolver(ags))
    assert node.seal.capabilities == ("data:write",)
    assert g.verify_graph(now=NOW,
                          hmac_secret_resolver=hmac_secret_resolver(ags)) == 2


def test_root_capabilities_unrestricted_by_subset_rule():
    # Roots take capabilities from their own seal/award: a genesis seal
    # may hold any well-formed capability set, including ones no parent
    # exists to grant.
    ags = agents()
    g = hmac_graph(ags)
    n0 = g.run_root(task("root-free", ("research",)), ags, seed=81, now=NOW,
                    capabilities=("treasury:release", "stake:slash"))
    assert n0.seal.capabilities == ("treasury:release", "stake:slash")
    assert g.verify_graph(now=NOW) == 1
