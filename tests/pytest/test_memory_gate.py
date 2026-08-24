from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from marketplace.cortex_sim import run_memory_scenario  # noqa: E402
from marketplace.memory_gate import (  # noqa: E402
    LAMBDA_PER_HOUR,
    MERIT_THRESHOLD,
    EpisodicStore,
    MemoryGate,
    ScratchStep,
    SemanticVault,
    ebbinghaus,
    half_life_hours,
)


def _step(**kwargs) -> ScratchStep:
    base = dict(
        step_id="s",
        task_id="t1",
        agent_id="atlas-scout",
        kind="thought",
        content="competitor pricing research",
        status="ok",
        confidence=0.9,
        created_at=1_700_000_000.0,
        tokens=("competitor", "pricing"),
    )
    base.update(kwargs)
    return ScratchStep(**base)


def test_ebbinghaus_half_life_is_24h() -> None:
    assert abs(half_life_hours() - 24.0) < 1e-6
    assert abs(ebbinghaus(1.0, 24.0) - 0.5) < 1e-6
    assert ebbinghaus(1.0, 0.0) == 1.0
    assert ebbinghaus(0.8, 24.0) < ebbinghaus(0.8, 1.0)


def test_failed_and_hallucinated_steps_never_enter_semantic() -> None:
    gate = MemoryGate()
    result = gate.ingest(
        [
            _step(step_id="a", status="hallucinated", confidence=0.2, content="fake co"),
            _step(step_id="b", status="failed", confidence=0.1, content="timeout"),
        ],
        merit=0.2,
        summary="garbage",
        closed_at=1_700_000_010.0,
    )
    assert result.promoted is False
    assert result.purged == 2
    assert result.episodic_remaining == 0
    assert len(gate.semantic) == 0
    assert result.poison_blocked == 2


def test_low_merit_success_is_not_promoted() -> None:
    gate = MemoryGate()
    result = gate.ingest(
        [_step(status="ok", confidence=0.9)],
        merit=MERIT_THRESHOLD - 0.1,
        summary="thin summary",
        closed_at=1_700_000_010.0,
    )
    assert result.promoted is False
    assert "merit_below_threshold" in result.promote_reason
    assert len(gate.semantic) == 0
    assert result.episodic_remaining == 0


def test_high_merit_promotes_and_purges_scratch() -> None:
    gate = MemoryGate()
    result = gate.ingest(
        [
            _step(step_id="1", content="map listings", tokens=("listings", "pricing")),
            _step(step_id="2", kind="tool", content="scraped cards", tokens=("scrape",)),
        ],
        merit=0.88,
        summary="median listing 1.2 AXM",
        closed_at=1_700_000_020.0,
    )
    assert result.promoted is True
    assert result.purged == 2
    assert result.episodic_remaining == 0
    assert len(gate.semantic) == 1
    assert gate.episodic.count() == 0


def test_mixed_poison_promotes_only_clean_tokens() -> None:
    gate = MemoryGate()
    result = gate.ingest(
        [
            _step(step_id="ok", status="ok", confidence=0.9, tokens=("listings",)),
            _step(step_id="bad", status="hallucinated", confidence=0.1, tokens=("axiomprime",)),
        ],
        merit=0.81,
        summary="listings only",
        closed_at=1_700_000_020.0,
    )
    assert result.promoted is True
    assert result.poison_blocked == 1
    hits = gate.semantic.retrieve(("listings",), now=1_700_000_030.0, limit=2)
    assert hits
    assert "axiomprime" not in hits[0].trace.tokens


def test_recent_trace_outranks_equal_older_trace() -> None:
    vault = SemanticVault()
    now = 1_700_100_000.0
    old = MemoryGate(semantic=vault).ingest(
        [_step(task_id="old", created_at=now - 48 * 3600, content="enterprise pricing")],
        merit=0.9,
        summary="old enterprise pricing",
        closed_at=now - 48 * 3600,
    )
    new = MemoryGate(semantic=vault).ingest(
        [_step(task_id="new", step_id="n", created_at=now, content="enterprise pricing")],
        merit=0.9,
        summary="new enterprise pricing",
        closed_at=now,
    )
    assert old.promoted and new.promoted
    hits = vault.retrieve(("competitor", "pricing"), now=now, limit=4)
    assert hits[0].trace.task_id == "new"
    assert hits[0].score > hits[1].score
    assert hits[1].decay < hits[0].decay


def test_lambda_matches_formula() -> None:
    sim = 0.7
    t = 10.0
    assert abs(ebbinghaus(sim, t) - sim * math.exp(-LAMBDA_PER_HOUR * t)) < 1e-12


def test_empty_vault_is_still_truthy() -> None:
    vault = SemanticVault()
    assert len(vault) == 0
    assert bool(vault) is True
    gate = MemoryGate(semantic=vault)
    gate.ingest(
        [_step()],
        merit=0.9,
        summary="keep this vault",
        closed_at=1_700_000_010.0,
    )
    assert len(vault) == 1


def test_direct_insert_rejects_poison_status() -> None:
    vault = SemanticVault()
    from marketplace.memory_gate.types import SemanticTrace

    trace = SemanticTrace(
        trace_id="x",
        agent_id="a",
        task_id="t",
        content="nope",
        tokens=("nope",),
        vector=tuple([0.0] * 64),
        merit=0.99,
        created_at=1.0,
        source_hash="0x00",
    )
    ok, reason = vault.insert(trace, status="failed", confidence=1.0)
    assert ok is False
    assert "poison" in reason


def test_sim_blocks_poison_and_ranks_recent() -> None:
    payload = run_memory_scenario()
    assert payload["poison"]["promoted"] is False
    assert payload["good"]["promoted"] is True
    assert payload["semantic_count"] == 2
    assert payload["episodic_remaining"] == 0
    assert payload["hits"][0]["trace"]["task_id"] == "t-research"
    assert EpisodicStore is not None
