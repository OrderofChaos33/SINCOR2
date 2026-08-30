from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from marketplace.cortex_sim import run_merit_scenario  # noqa: E402
from marketplace.merit import EigenTrust, MeritEngine, Rating  # noqa: E402


def test_self_rating_forbidden() -> None:
    trust = EigenTrust(pretrusted=("auditor-lynx",))
    try:
        trust.add_rating(Rating("a", "a", 10.0, "t"))
        raise AssertionError("self-rating")
    except ValueError:
        pass


def test_sybil_clique_loses_to_independently_verified_agents() -> None:
    payload = run_merit_scenario()
    board = {row["agent_id"]: row for row in payload["leaderboard"]}
    # Raw average still loves the clique (Goodhart).
    raw_top_ids = [row["agent_id"] for row in payload["raw_top"]]
    assert any(agent.startswith("sybil-") for agent in raw_top_ids)
    # EigenTrust + honeypot does not.
    eigen_top_ids = [row["agent_id"] for row in payload["eigen_top"]]
    assert all(not agent.startswith("sybil-") for agent in eigen_top_ids)
    assert board["atlas-scout"]["eigentrust"] > board["sybil-alpha"]["eigentrust"]
    assert board["sybil-alpha"]["honeypot_fails"] >= 1
    assert board["atlas-scout"]["honeypot_passes"] >= 1
    assert board["sybil-alpha"]["sybil_suspect"] is True


def test_honeypot_pass_is_independent_evidence() -> None:
    engine = MeritEngine()
    engine.rate("sybil-alpha", "sybil-beta", 10.0, "clique")
    engine.rate("sybil-beta", "sybil-alpha", 10.0, "clique")
    result = engine.honeypot("atlas-scout", "hp-chain", "8453")
    assert result.passed is True
    fail = engine.honeypot("sybil-alpha", "hp-chain", "999")
    assert fail.passed is False
    board = {row.agent_id: row for row in engine.leaderboard()}
    assert board["atlas-scout"].eigentrust > board["sybil-alpha"].eigentrust


def test_pretrusted_mass_is_conserved() -> None:
    trust = EigenTrust(pretrusted=("auditor-lynx",))
    trust.add_rating(Rating("auditor-lynx", "atlas-scout", 10.0, "hp", independent=True))
    scores = trust.compute()
    assert abs(sum(scores.values()) - 1.0) < 1e-9
    assert scores["auditor-lynx"] > 0


def test_existing_marketplace_exports_untouched() -> None:
    from marketplace import (
        ContractNetEngine,
        MemoryGate,
        MeritEngine,
        OptimisticBatcher,
        ReputationEngine,
        SettlementCoordinator,
    )

    assert ContractNetEngine is not None
    assert ReputationEngine is not None
    assert SettlementCoordinator is not None
    assert MemoryGate is not None
    assert MeritEngine is not None
    assert OptimisticBatcher is not None
