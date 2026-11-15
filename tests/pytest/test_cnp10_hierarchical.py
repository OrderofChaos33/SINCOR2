"""CNP-10: hierarchical CNP — composable subcontracting + nested seals.

Covers: seal mint/verify round-trips (secp256k1 + HMAC demo path), seal-chain
verification (broken parent, tampering, cycles, depth smuggling, expiry,
unauthorized signers), 3-level nesting end-to-end through the flat CNP
engine, depth-cap enforcement, task-graph cycle rejection, orphaned
subcontracts, winner authorization, and whole-graph audit.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_SRC = str(ROOT / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from eth_account import Account  # noqa: E402
from eth_account.messages import encode_typed_data  # noqa: E402

from marketplace.contract_net.eip712 import (  # noqa: E402
    demo_signing_secret,
    demo_wallet,
    to_hex,
)
from marketplace.contract_net.engine import ContractNetEngine  # noqa: E402
from marketplace.contract_net.keccak import keccak256  # noqa: E402
from marketplace.contract_net.types import (  # noqa: E402
    BASE_SEPOLIA_CHAIN_ID,
    AgentProfile,
    ContractNetConfig,
    TaskSpec,
)
from sincor2.hierarchical_cnp import (  # noqa: E402
    EMPTY_SEAL_HASH,
    MAX_DEPTH_HARD_CAP,
    AuthorizationError,
    CycleDetected,
    DepthLimitExceeded,
    DuplicateSeal,
    GraphError,
    HierarchicalConfig,
    HierarchicalTaskGraph,
    NestedSeal,
    OrphanedSubcontract,
    SealChainError,
    SealError,
    mint_nested_seal,
    nested_seal_digest,
    nested_seal_typed_data,
    verify_nested_seal,
    verify_seal_chain,
)

CHAIN_ID = BASE_SEPOLIA_CHAIN_ID
NOW = 1_800_000_000


def net_config(**kw):
    return ContractNetConfig(chain_id=CHAIN_ID, allow_hmac_bids=True, **kw)


def h_config(**kw):
    kw.setdefault("allow_hmac_seals", True)
    return HierarchicalConfig(**kw)


def _agent(agent_id, skills, price_axm=1.0):
    return AgentProfile(
        agent_id=agent_id,
        name=agent_id,
        skills=tuple(skills),
        wallet=demo_wallet(agent_id),
        tasks_completed=10,
        success_rate=0.9,
        true_min_price=int(price_axm * 1_000_000),
        estimated_tokens=400,
        signing_secret=demo_signing_secret(agent_id),
    )


SKILLS = (
    "research", "analyze", "summarize", "curate",
    "validate", "monitor", "scrape", "prospect",
)


def agents():
    return [
        _agent("atlas", SKILLS, 1.20),
        _agent("helix", SKILLS, 1.10),
        _agent("cinder", SKILLS, 1.35),
        _agent("onyx", SKILLS, 1.05),
        _agent("lyra", SKILLS, 1.25),
        _agent("vesta", SKILLS, 1.15),
    ]


def task(task_id, requirements):
    return TaskSpec(
        task_id=task_id,
        goal=f"goal for {task_id}",
        requirements=tuple(requirements),
        budget_tokens=1000,
        max_price=10_000_000,
    )


def key_resolver(ags):
    table = {a.agent_id: ("", a.signing_secret) for a in ags}

    def resolve(agent_id):
        return table[agent_id]

    return resolve


def hmac_secret_resolver(ags):
    table = {a.wallet.lower(): a.signing_secret for a in ags}
    return lambda addr: table.get(addr.lower(), "")


def graph(ags=None, **cfg_kw):
    ags = ags or agents()
    return HierarchicalTaskGraph(
        ContractNetEngine(net_config()), h_config(**cfg_kw), key_resolver(ags)
    )


def secp256k1_identity():
    acct = Account.create()
    return acct.address, acct.key.hex()


def mint_secp256k1(net_cfg, *, depth=0, parent_seal_hash=EMPTY_SEAL_HASH,
                   task_id="t-root", nonce=1, expiry=NOW + 3600):
    address, key = secp256k1_identity()
    seal = mint_nested_seal(
        net_cfg,
        agent_did="did:sincor:prover",
        agent_address=address,
        task_id=task_id,
        capabilities=("subcontract:award",),
        expiry=expiry,
        nonce=nonce,
        depth=depth,
        parent_seal_hash=parent_seal_hash,
        private_key=key,
        max_depth=MAX_DEPTH_HARD_CAP,
        now=NOW,
    )
    return seal, address, key


# ---------------------------------------------------------------------------
# Seal mint / verify
# ---------------------------------------------------------------------------


def test_seal_digest_is_byte_identical_to_eth_account():
    cfg = net_config()
    td = nested_seal_typed_data(
        cfg,
        parent_seal_hash=EMPTY_SEAL_HASH,
        agent_did="did:sincor:atlas",
        agent_address="0x0000000000000000000000000000000000000001",
        task_id="t1",
        capabilities=("subcontract:award",),
        expiry=9999999999,
        nonce=1,
        depth=0,
    )
    sm = encode_typed_data(full_message=td)
    expected = keccak256(b"\x19" + sm.version + sm.header + sm.body)
    got = nested_seal_digest(
        cfg,
        parent_seal_hash=EMPTY_SEAL_HASH,
        agent_did="did:sincor:atlas",
        agent_address="0x0000000000000000000000000000000000000001",
        task_id="t1",
        capabilities=("subcontract:award",),
        expiry=9999999999,
        nonce=1,
        depth=0,
    )
    assert got == expected


def test_secp256k1_seal_round_trip():
    cfg = net_config()
    seal, address, _key = mint_secp256k1(cfg)
    verify_nested_seal(seal, cfg, now=NOW)
    assert seal.seal_id == seal.digest
    assert seal.depth == 0
    assert seal.sig_type == "eip712-secp256k1"


def test_tampered_seal_field_rejected():
    cfg = net_config()
    seal, _address, _key = mint_secp256k1(cfg)
    tampered = NestedSeal(
        seal_id=seal.seal_id,
        parent_seal_hash=seal.parent_seal_hash,
        agent_did=seal.agent_did,
        agent_address=seal.agent_address,
        task_id="t-EVIL",  # tampered after signing
        capabilities=seal.capabilities,
        expiry=seal.expiry,
        nonce=seal.nonce,
        depth=seal.depth,
        digest=seal.digest,
        signature=seal.signature,
        sig_type=seal.sig_type,
        typed_data=seal.typed_data,
    )
    with pytest.raises(SealError):
        verify_nested_seal(tampered, cfg, now=NOW)


def test_forged_seal_id_rejected():
    cfg = net_config()
    seal, _address, _key = mint_secp256k1(cfg)
    forged = NestedSeal(
        seal_id=EMPTY_SEAL_HASH,  # self-declared id is never trusted
        parent_seal_hash=seal.parent_seal_hash,
        agent_did=seal.agent_did,
        agent_address=seal.agent_address,
        task_id=seal.task_id,
        capabilities=seal.capabilities,
        expiry=seal.expiry,
        nonce=seal.nonce,
        depth=seal.depth,
        digest=seal.digest,
        signature=seal.signature,
        sig_type=seal.sig_type,
        typed_data=seal.typed_data,
    )
    with pytest.raises(SealError):
        verify_nested_seal(forged, cfg, now=NOW)


def test_expired_seal_rejected():
    cfg = net_config()
    seal, _address, _key = mint_secp256k1(cfg, expiry=NOW + 10)
    with pytest.raises(SealError):
        verify_nested_seal(seal, cfg, now=NOW + 11)


def test_hmac_seal_default_deny():
    cfg = net_config()
    seal = mint_nested_seal(
        cfg,
        agent_did="did:sincor:atlas",
        agent_address=demo_wallet("atlas"),
        task_id="t1",
        expiry=NOW + 3600,
        nonce=1,
        depth=0,
        signing_secret=demo_signing_secret("atlas"),
        now=NOW,
    )
    assert seal.sig_type == "eip712-hmac-sha256"
    with pytest.raises(SealError):
        verify_nested_seal(seal, cfg, now=NOW)  # default-deny
    verify_nested_seal(
        seal, cfg, now=NOW, allow_hmac=True,
        hmac_secret=demo_signing_secret("atlas"),
    )


def test_wrong_chain_id_rejected():
    cfg = net_config()
    other = ContractNetConfig(chain_id=1, allow_hmac_bids=True)
    seal, _address, _key = mint_secp256k1(cfg)
    with pytest.raises(SealError):
        verify_nested_seal(seal, other, now=NOW)


def test_hostile_field_ranges_fail_as_seal_error():
    cfg = net_config()
    seal, _address, _key = mint_secp256k1(cfg)
    evil = NestedSeal(
        seal_id=seal.seal_id,
        parent_seal_hash=seal.parent_seal_hash,
        agent_did=seal.agent_did,
        agent_address=seal.agent_address,
        task_id=seal.task_id,
        capabilities=seal.capabilities,
        expiry=1 << 256,  # out of uint256 range
        nonce=seal.nonce,
        depth=seal.depth,
        digest=seal.digest,
        signature=seal.signature,
        sig_type=seal.sig_type,
        typed_data=seal.typed_data,
    )
    with pytest.raises(SealError):
        verify_nested_seal(evil, cfg, now=NOW)
    evil2 = NestedSeal(
        seal_id=seal.seal_id,
        parent_seal_hash=seal.parent_seal_hash,
        agent_did=seal.agent_did,
        agent_address=seal.agent_address,
        task_id=seal.task_id,
        capabilities=seal.capabilities,
        expiry=seal.expiry,
        nonce=-1,  # negative nonce
        depth=seal.depth,
        digest=seal.digest,
        signature=seal.signature,
        sig_type=seal.sig_type,
        typed_data=seal.typed_data,
    )
    with pytest.raises(SealError):
        verify_nested_seal(evil2, cfg, now=NOW)


def test_seal_ttl_cap_enforced_at_mint():
    cfg = net_config()
    _address, key = secp256k1_identity()
    with pytest.raises(SealError):
        mint_nested_seal(
            cfg,
            agent_did="did:sincor:atlas",
            agent_address=demo_wallet("atlas"),
            task_id="t1",
            expiry=NOW + 31 * 24 * 3600,  # beyond the 30-day cap
            nonce=1,
            depth=0,
            private_key=key,
            now=NOW,
        )


# ---------------------------------------------------------------------------
# Seal-chain verification
# ---------------------------------------------------------------------------


def _chain_fixture(depth=2):
    """Build a valid chain of `depth` nesting levels (root + depth children)."""
    cfg = net_config()
    registry = {}
    parent_hash = EMPTY_SEAL_HASH
    seals = []
    for level in range(depth + 1):
        seal, _addr, _key = mint_secp256k1(
            cfg, depth=level, parent_seal_hash=parent_hash,
            task_id=f"t-{level}", nonce=level + 1,
        )
        registry[seal.seal_id.lower()] = seal
        seals.append(seal)
        parent_hash = seal.seal_id
    return cfg, registry, seals


def test_valid_chain_verifies_root_first():
    cfg, registry, seals = _chain_fixture(depth=2)
    chain = verify_seal_chain(
        seals[-1], registry, cfg, now=NOW, max_depth=MAX_DEPTH_HARD_CAP
    )
    assert [s.depth for s in chain] == [0, 1, 2]
    assert chain[0].parent_seal_hash == EMPTY_SEAL_HASH


def test_broken_parent_chain_rejected():
    cfg, registry, seals = _chain_fixture(depth=1)
    # A validly signed seal referencing a parent hash that was never
    # registered: the realistic broken-chain attack.
    attacker_seal, _a, _k = mint_secp256k1(
        cfg, depth=1,
        parent_seal_hash=to_hex(keccak256(b"no-such-parent")),
        task_id="t-evil", nonce=99,
    )
    with pytest.raises(SealChainError):
        verify_seal_chain(
            attacker_seal, registry, cfg, now=NOW, max_depth=MAX_DEPTH_HARD_CAP
        )


def test_tampered_parent_in_registry_rejected():
    cfg, registry, seals = _chain_fixture(depth=1)
    root, leaf = seals
    tampered_root = NestedSeal(
        seal_id=root.seal_id,
        parent_seal_hash=root.parent_seal_hash,
        agent_did=root.agent_did,
        agent_address=root.agent_address,
        task_id="t-TAMPERED",
        capabilities=root.capabilities,
        expiry=root.expiry,
        nonce=root.nonce,
        depth=root.depth,
        digest=root.digest,
        signature=root.signature,
        sig_type=root.sig_type,
        typed_data=root.typed_data,
    )
    registry[root.seal_id.lower()] = tampered_root
    with pytest.raises(SealChainError):
        verify_seal_chain(
            leaf, registry, cfg, now=NOW, max_depth=MAX_DEPTH_HARD_CAP
        )


def test_seal_reference_cycle_rejected():
    cfg = net_config()
    root, _, _ = mint_secp256k1(cfg, depth=0, task_id="t-r", nonce=1)
    b_tmp, _, _ = mint_secp256k1(
        cfg, depth=1, parent_seal_hash=root.seal_id, task_id="t-b", nonce=2
    )
    a, _, _ = mint_secp256k1(
        cfg, depth=2, parent_seal_hash=b_tmp.seal_id, task_id="t-a", nonce=3
    )
    b, _, _ = mint_secp256k1(
        cfg, depth=1, parent_seal_hash=a.seal_id, task_id="t-b2", nonce=4
    )
    # Hostile registry: alias the ids so the walk goes a -> b -> a.
    # (Seal ids are derived, so a true honest 2-cycle is unconstructible;
    # registry aliasing is the realistic smuggling shape.)
    # Note: the strict depth-decrease-by-1 check fires before the
    # visited-set on any reference cycle (a repeated node cannot satisfy
    # it), so the rejection surfaces as a depth violation; the visited
    # set remains as defense-in-depth.
    registry = {
        root.seal_id.lower(): root,
        b_tmp.seal_id.lower(): b,
        a.seal_id.lower(): b,
        b.seal_id.lower(): a,
    }
    with pytest.raises(SealChainError):
        verify_seal_chain(a, registry, cfg, now=NOW, max_depth=MAX_DEPTH_HARD_CAP)


def test_self_parent_seal_rejected():
    cfg = net_config()
    root, _, _ = mint_secp256k1(cfg, depth=0, task_id="t-r", nonce=1)
    x, _, _ = mint_secp256k1(
        cfg, depth=1, parent_seal_hash=root.seal_id, task_id="t-x", nonce=2
    )
    # Hostile registry: the parent hash resolves to the seal itself.
    registry = {root.seal_id.lower(): x, x.seal_id.lower(): x}
    with pytest.raises(SealChainError):
        verify_seal_chain(x, registry, cfg, now=NOW, max_depth=MAX_DEPTH_HARD_CAP)


def test_depth_smuggling_rejected():
    cfg, registry, seals = _chain_fixture(depth=1)
    root, leaf = seals
    # Attacker re-registers the root claiming depth 5 (valid signature is
    # impossible without the key, so simulate a registry lie by re-minting
    # with a fresh key at the wrong depth — the depth-step check must fire).
    liar, _a, _k = mint_secp256k1(cfg, depth=5, task_id="t-liar", nonce=77)
    registry2 = dict(registry)
    registry2[root.seal_id.lower()] = liar
    # leaf (depth 1) expects parent depth 0; liar claims 5.
    evil_leaf, _a2, _k2 = mint_secp256k1(
        cfg, depth=6, parent_seal_hash=liar.seal_id, task_id="t-evil2", nonce=78,
    )
    registry2[evil_leaf.seal_id.lower()] = evil_leaf
    # Sanity: liar's own chain is broken (no depth-4 parent), which must also fail.
    with pytest.raises(SealChainError):
        verify_seal_chain(
            evil_leaf, registry2, cfg, now=NOW, max_depth=MAX_DEPTH_HARD_CAP
        )


def test_unauthorized_signer_rejected():
    cfg, registry, seals = _chain_fixture(depth=1)
    leaf = seals[-1]
    allow = {seals[0].agent_address}  # leaf signer not listed
    with pytest.raises(SealChainError):
        verify_seal_chain(
            leaf, registry, cfg, now=NOW, max_depth=MAX_DEPTH_HARD_CAP,
            authorized_signers=allow,
        )
    # Allowlisting both signers passes.
    allow_all = {s.agent_address for s in seals}
    chain = verify_seal_chain(
        leaf, registry, cfg, now=NOW, max_depth=MAX_DEPTH_HARD_CAP,
        authorized_signers=allow_all,
    )
    assert len(chain) == 2


def test_expired_parent_breaks_chain():
    cfg = net_config()
    root, _a, _k = mint_secp256k1(cfg, depth=0, task_id="t-r", nonce=1,
                                  expiry=NOW + 10)
    leaf, _a2, _k2 = mint_secp256k1(cfg, depth=1, parent_seal_hash=root.seal_id,
                                    task_id="t-l", nonce=2, expiry=NOW + 3600)
    registry = {root.seal_id.lower(): root, leaf.seal_id.lower(): leaf}
    with pytest.raises(SealChainError):
        verify_seal_chain(
            leaf, registry, cfg, now=NOW + 11, max_depth=MAX_DEPTH_HARD_CAP
        )


def test_genesis_with_parent_hash_rejected():
    cfg = net_config()
    seal, _a, _k = mint_secp256k1(
        cfg, depth=0,
        parent_seal_hash=to_hex(keccak256(b"not-empty")),
        task_id="t-g", nonce=1,
    )
    registry = {seal.seal_id.lower(): seal}
    with pytest.raises(SealChainError):
        verify_seal_chain(
            seal, registry, cfg, now=NOW, max_depth=MAX_DEPTH_HARD_CAP
        )


# ---------------------------------------------------------------------------
# Graph: end-to-end nesting
# ---------------------------------------------------------------------------


def test_three_level_nesting_end_to_end():
    ags = agents()
    g = graph(ags, max_depth=3)
    root_task = task("root-001", ("research", "analyze"))
    l1_task = task("sub-001", ("summarize", "curate"))
    l2_task = task("sub-002", ("validate", "monitor"))
    l3_task = task("sub-003", ("scrape", "prospect"))

    n0 = g.run_root(root_task, ags, seed=11, now=NOW)
    assert n0.depth == 0 and n0.parent_node_id == ""
    n1 = g.subcontract(n0.node_id, l1_task, ags, seed=12, now=NOW)
    n2 = g.subcontract(n1.node_id, l2_task, ags, seed=13, now=NOW)
    n3 = g.subcontract(n2.node_id, l3_task, ags, seed=14, now=NOW)

    assert (n1.depth, n2.depth, n3.depth) == (1, 2, 3)
    # Every level ran a real engine auction with a winner.
    for node in (n0, n1, n2, n3):
        assert node.award.winner_id
        assert node.award.phase == "cleared"
    # Seal chain integrity across the whole tree.
    assert g.verify_graph(now=NOW) == 4
    lineage = g.lineage(n3.node_id)
    assert [n.task.task_id for n in lineage] == [
        "root-001", "sub-001", "sub-002", "sub-003",
    ]
    # Child seals chain to their parent seals.
    assert n1.seal.parent_seal_hash.lower() == n0.seal.seal_id.lower()
    assert n2.seal.parent_seal_hash.lower() == n1.seal.seal_id.lower()
    assert n3.seal.parent_seal_hash.lower() == n2.seal.seal_id.lower()
    # Subcontracts are authorized by the parent award winner.
    assert n1.seal.agent_address.lower() == n0.award.winner_wallet.lower()


def test_depth_cap_enforced():
    ags = agents()
    g = graph(ags, max_depth=2)
    n0 = g.run_root(task("root-d", ("research",)), ags, seed=21, now=NOW)
    n1 = g.subcontract(n0.node_id, task("d-1", ("summarize",)), ags, seed=22, now=NOW)
    n2 = g.subcontract(n1.node_id, task("d-2", ("validate",)), ags, seed=23, now=NOW)
    assert n2.depth == 2
    with pytest.raises(DepthLimitExceeded):
        g.subcontract(n2.node_id, task("d-3", ("scrape",)), ags, seed=24, now=NOW)
    # max_depth=1 also enforced at config validation time.
    with pytest.raises(ValueError):
        HierarchicalConfig(max_depth=0)
    with pytest.raises(ValueError):
        HierarchicalConfig(max_depth=MAX_DEPTH_HARD_CAP + 1)


def test_task_id_cycle_rejected():
    ags = agents()
    g = graph(ags)
    n0 = g.run_root(task("root-c", ("research",)), ags, seed=31, now=NOW)
    n1 = g.subcontract(n0.node_id, task("c-1", ("summarize",)), ags, seed=32, now=NOW)
    # A "subcontract" reusing an ancestor's task id is a cycle.
    with pytest.raises(CycleDetected):
        g.subcontract(n1.node_id, task("root-c", ("research",)), ags, seed=33, now=NOW)
    # Reusing any existing task id (even a sibling) is rejected too.
    with pytest.raises(GraphError):
        g.subcontract(n0.node_id, task("c-1", ("summarize",)), ags, seed=34, now=NOW)


def test_orphaned_subcontract_rejected():
    ags = agents()
    g = graph(ags)
    n0 = g.run_root(task("root-o", ("research",)), ags, seed=41, now=NOW)
    # Unknown parent node.
    child = task("o-1", ("summarize",))
    award = g.engine.run(child, ags, seed=42, now=NOW)
    seal = mint_nested_seal(
        g.engine.config,
        agent_did="did:sincor:atlas",
        agent_address=demo_wallet("atlas"),
        task_id="o-1",
        expiry=NOW + 3600,
        nonce=g.next_seal_nonce(demo_wallet("atlas")),
        depth=1,
        parent_seal_hash=n0.seal.seal_id,
        signing_secret=demo_signing_secret("atlas"),
        max_depth=3,
        now=NOW,
    )
    with pytest.raises(GraphError):
        g.register_subcontract("htn-nope", child, award, seal, now=NOW)
    # A child seal whose parent hash is not registered is rejected even
    # when the parent node exists (broken parent chain).
    evil_seal = mint_nested_seal(
        g.engine.config,
        agent_did=f"did:sincor:{n0.award.winner_id}",
        agent_address=n0.award.winner_wallet,
        task_id="o-1",
        expiry=NOW + 3600,
        nonce=g.next_seal_nonce(n0.award.winner_wallet),
        depth=1,
        parent_seal_hash=to_hex(keccak256(b"unregistered-parent")),
        signing_secret=demo_signing_secret(n0.award.winner_id),
        max_depth=3,
        now=NOW,
    )
    with pytest.raises(SealChainError):
        g.register_subcontract(
            n0.node_id, child, award, evil_seal, now=NOW,
            hmac_secret_resolver=hmac_secret_resolver(ags),
        )


def test_winner_authorization_enforced():
    ags = agents()
    g = graph(ags)  # require_winner_authorization=True by default
    n0 = g.run_root(task("root-w", ("research",)), ags, seed=51, now=NOW)
    child = task("w-1", ("summarize",))
    award = g.engine.run(child, ags, seed=52, now=NOW)
    # Forge: a non-winner signs the child seal with a *valid* signature.
    forger = next(a for a in ags if a.agent_id != n0.award.winner_id)
    forged = mint_nested_seal(
        g.engine.config,
        agent_did=f"did:sincor:{forger.agent_id}",
        agent_address=forger.wallet,
        task_id="w-1",
        expiry=NOW + 3600,
        nonce=g.next_seal_nonce(forger.wallet),
        depth=1,
        parent_seal_hash=n0.seal.seal_id,
        signing_secret=forger.signing_secret,
        max_depth=3,
        now=NOW,
    )
    with pytest.raises(AuthorizationError):
        g.register_subcontract(
            n0.node_id, child, award, forged, now=NOW,
            hmac_secret_resolver=hmac_secret_resolver(ags),
        )


def test_duplicate_seal_registration_rejected():
    ags = agents()
    g = graph(ags)
    n0 = g.run_root(task("root-x", ("research",)), ags, seed=61, now=NOW)
    n1 = g.subcontract(n0.node_id, task("x-1", ("summarize",)), ags, seed=62, now=NOW)
    # Re-registering an already-registered seal under a fresh task/award.
    task2 = task("x-2", ("analyze",))
    award2 = g.engine.run(task2, ags, seed=63, now=NOW)
    with pytest.raises(DuplicateSeal):
        g.register_subcontract(
            n0.node_id, task2, award2, n1.seal, now=NOW,
            hmac_secret_resolver=hmac_secret_resolver(ags),
        )


def test_failed_auction_cannot_be_sealed():
    g = graph(agents())
    # No agent can satisfy the task: empty roster => failed auction.
    bad = task("root-f", ("research",))
    with pytest.raises(GraphError):
        g.run_root(bad, [], seed=71, now=NOW)
    assert len(g) == 0


def test_verify_graph_catches_registry_tampering():
    ags = agents()
    g = graph(ags)
    n0 = g.run_root(task("root-v", ("research",)), ags, seed=81, now=NOW)
    n1 = g.subcontract(n0.node_id, task("v-1", ("summarize",)), ags, seed=82, now=NOW)
    assert g.verify_graph(now=NOW) == 2
    # Tamper with a registered seal in place (simulates registry corruption).
    tampered = NestedSeal(
        seal_id=n0.seal.seal_id,
        parent_seal_hash=n0.seal.parent_seal_hash,
        agent_did=n0.seal.agent_did,
        agent_address=n0.seal.agent_address,
        task_id="root-EVIL",
        capabilities=n0.seal.capabilities,
        expiry=n0.seal.expiry,
        nonce=n0.seal.nonce,
        depth=n0.seal.depth,
        digest=n0.seal.digest,
        signature=n0.seal.signature,
        sig_type=n0.seal.sig_type,
        typed_data=n0.seal.typed_data,
    )
    g._seals[n0.seal.seal_id.lower()] = tampered
    with pytest.raises(SealChainError):
        g.verify_graph(now=NOW)
    # The child node's own chain still verifies against its stored seal,
    # but the parent lookup now fails -> broken chain.
    with pytest.raises(SealChainError):
        g.verify_node_chain(n1, now=NOW)


def test_high_level_flows_use_secp256k1_when_configured():
    # Money-grade path: secp256k1 seals end-to-end through the graph.
    ags = agents()
    accts = {a.agent_id: Account.create() for a in ags}
    for a in ags:
        a.wallet = accts[a.agent_id].address

    def secp_resolver(agent_id):
        return (accts[agent_id].key.hex(), "")

    g = HierarchicalTaskGraph(
        ContractNetEngine(net_config()),
        HierarchicalConfig(max_depth=3),  # HMAC default-deny
        secp_resolver,
    )
    n0 = g.run_root(task("root-s", ("research",)), ags, seed=91, now=NOW)
    assert n0.seal.sig_type == "eip712-secp256k1"
    n1 = g.subcontract(n0.node_id, task("s-1", ("summarize",)), ags, seed=92, now=NOW)
    assert g.verify_graph(now=NOW) == 2
    # Recovered signer matches the declared agent address.
    chain = g.verify_node_chain(n1, now=NOW)
    assert chain[-1].agent_address.lower() == n0.award.winner_wallet.lower()
