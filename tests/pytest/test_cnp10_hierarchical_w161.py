"""W-161 regression: hierarchical seal replay across graph instances.

The DuplicateSeal protection used to live only in each graph instance's
in-memory dict: the SAME root seal registered cleanly in a second fresh
graph (PoC50-D §D2), and a restart forgot every registered seal. With a
durable shared seal registry configured (``seal_registry_path`` or the
``SINCOR_HIER_SEAL_REGISTRY_PATH`` env var), a seal id registers exactly
once across instances and restarts. Without one, the legacy per-instance
behavior is preserved (dev/test mode) — production must configure a
registry, exactly as the module docstring states.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_SRC = str(ROOT / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from marketplace.contract_net.eip712 import demo_signing_secret, demo_wallet  # noqa: E402
from marketplace.contract_net.engine import ContractNetEngine  # noqa: E402
from marketplace.contract_net.types import (  # noqa: E402
    BASE_SEPOLIA_CHAIN_ID,
    AgentProfile,
    ContractNetConfig,
    TaskSpec,
)
from sincor2.hierarchical_cnp import (  # noqa: E402
    SEAL_REGISTRY_PATH_ENV,
    DuplicateSeal,
    FileSealRegistry,
    HierarchicalConfig,
    HierarchicalTaskGraph,
    SealRegistryError,
)

NOW = 1_800_000_000
SKILLS = (
    "research", "analyze", "summarize", "curate",
    "validate", "monitor", "scrape", "prospect",
)


def _agent(aid, price):
    return AgentProfile(
        agent_id=aid, name=aid, skills=SKILLS,
        wallet=demo_wallet(aid), tasks_completed=10, success_rate=0.9,
        true_min_price=int(price * 1_000_000), estimated_tokens=400,
        signing_secret=demo_signing_secret(aid),
    )


AGS = [
    _agent("atlas", 1.20), _agent("helix", 1.10), _agent("cinder", 1.35),
    _agent("onyx", 1.05), _agent("lyra", 1.25), _agent("vesta", 1.15),
]
_TABLE = {a.agent_id: ("", a.signing_secret) for a in AGS}
_RESOLVER = lambda aid: _TABLE[aid]  # noqa: E731
_HRES = lambda addr: {  # noqa: E731
    a.wallet.lower(): a.signing_secret for a in AGS
}.get(addr.lower(), "")


def _task(tid, req):
    return TaskSpec(
        task_id=tid, goal=f"goal {tid}", requirements=tuple(req),
        budget_tokens=1000, max_price=10_000_000,
    )


def _graph(registry_path=None):
    cfg = ContractNetConfig(chain_id=BASE_SEPOLIA_CHAIN_ID, allow_hmac_bids=True)
    hcfg = HierarchicalConfig(
        max_depth=3, allow_hmac_seals=True, seal_registry_path=registry_path,
    )
    return HierarchicalTaskGraph(ContractNetEngine(cfg), hcfg, _RESOLVER)


def _root_node(graph, tid="root-1"):
    return graph.run_root(
        _task(tid, ("research",)), AGS, seed=11, now=NOW,
        capabilities=("research:read",),
    )


# ---------------------------------------------------------------------------
# The PoC (§D2) shape, with a shared registry configured
# ---------------------------------------------------------------------------

def test_cross_instance_replay_rejected_with_shared_registry(tmp_path):
    reg = str(tmp_path / "seal-registry.json")
    g1 = _graph(reg)
    n0 = _root_node(g1)

    g2 = _graph(reg)
    with pytest.raises(DuplicateSeal):
        g2.register_root(
            n0.task, n0.award, n0.seal, now=NOW, hmac_secret_resolver=_HRES,
        )
    # The replay left no trace in the second graph.
    assert len(g2) == 0


def test_restart_replay_rejected(tmp_path):
    reg = str(tmp_path / "seal-registry.json")
    g1 = _graph(reg)
    n0 = _root_node(g1)
    del g1  # process restart: only the registry file survives

    g2 = _graph(reg)
    with pytest.raises(DuplicateSeal):
        g2.register_root(
            n0.task, n0.award, n0.seal, now=NOW, hmac_secret_resolver=_HRES,
        )


def test_env_var_wires_shared_registry(tmp_path, monkeypatch):
    reg = str(tmp_path / "seal-registry.json")
    monkeypatch.setenv(SEAL_REGISTRY_PATH_ENV, reg)
    g1 = _graph()  # no config path: env fallback wires the registry
    n0 = _root_node(g1)

    g2 = _graph()
    with pytest.raises(DuplicateSeal):
        g2.register_root(
            n0.task, n0.award, n0.seal, now=NOW, hmac_secret_resolver=_HRES,
        )


# ---------------------------------------------------------------------------
# Legitimate flows are untouched
# ---------------------------------------------------------------------------

def test_distinct_seals_register_across_instances_sharing_registry(tmp_path):
    reg = str(tmp_path / "seal-registry.json")
    g1 = _graph(reg)
    n1 = _root_node(g1, tid="root-a")
    g2 = _graph(reg)
    n2 = _root_node(g2, tid="root-b")
    assert n1.seal.seal_id != n2.seal.seal_id
    assert len(g1) == 1 and len(g2) == 1


def test_same_instance_chain_still_registers_with_registry(tmp_path):
    reg = str(tmp_path / "seal-registry.json")
    g = _graph(reg)
    n0 = _root_node(g)
    n1 = g.subcontract(
        n0.node_id, _task("child-1", ("summarize",)), AGS, seed=12, now=NOW,
        capabilities=("research:read",),
    )
    assert g.verify_graph(now=NOW, hmac_secret_resolver=_HRES) == 2
    # Both seals were claimed in the shared registry.
    shared = FileSealRegistry(reg)
    assert shared.contains(n0.seal.seal_id)
    assert shared.contains(n1.seal.seal_id)


def test_claim_is_durable_on_disk_before_registration_returns(tmp_path):
    reg = tmp_path / "seal-registry.json"
    g = _graph(str(reg))
    n0 = _root_node(g)
    data = json.loads(reg.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert n0.seal.seal_id.lower() in data["seal_ids"]


# ---------------------------------------------------------------------------
# Fail-closed registry behavior
# ---------------------------------------------------------------------------

def test_corrupt_registry_fails_closed(tmp_path):
    reg = tmp_path / "seal-registry.json"
    reg.write_text("this is not json", encoding="utf-8")
    g = _graph(str(reg))
    with pytest.raises(SealRegistryError):
        _root_node(g)


def test_wrong_shape_registry_fails_closed(tmp_path):
    reg = tmp_path / "seal-registry.json"
    reg.write_text(json.dumps({"version": 99, "seal_ids": []}), encoding="utf-8")
    g = _graph(str(reg))
    with pytest.raises(SealRegistryError):
        _root_node(g)


def test_registry_claim_race_second_claim_loses(tmp_path):
    reg = str(tmp_path / "seal-registry.json")
    seal_id = "0x" + "ab" * 32
    assert FileSealRegistry(reg).claim(seal_id) is True
    # A peer instance over the same file sees the claim immediately.
    assert FileSealRegistry(reg).claim(seal_id) is False
    assert FileSealRegistry(reg).contains(seal_id.upper().replace("0X", "0x"))


# ---------------------------------------------------------------------------
# Dev/test mode preserved: no registry configured = per-instance behavior
# ---------------------------------------------------------------------------

def test_no_registry_keeps_per_instance_behavior(tmp_path, monkeypatch):
    monkeypatch.delenv(SEAL_REGISTRY_PATH_ENV, raising=False)
    g1 = _graph()
    n0 = _root_node(g1)
    g2 = _graph()
    replayed = g2.register_root(
        n0.task, n0.award, n0.seal, now=NOW, hmac_secret_resolver=_HRES,
    )
    assert replayed.seal.seal_id == n0.seal.seal_id
