"""Deterministic demo scenarios for memory, settlement, and merit.

Used by the Flask blueprint and by tests. Not a mock — it drives the real
engines with a fixed roster.
"""

from __future__ import annotations

from typing import Any, Dict

from marketplace.contract_net.keccak import keccak256

from .memory_gate import MemoryGate, ScratchStep
from .merit import MeritEngine
from .optimistic import OptimisticBatcher
from .optimistic.merkle import leaf_hash


HONEST = ("atlas-scout", "helix-synth", "forge-builder", "wick-analyst")
SYBIL = ("sybil-alpha", "sybil-beta", "sybil-gamma", "sybil-delta")
AUDITOR = "auditor-lynx"


def _salt(label: str) -> bytes:
    return keccak256(b"cortex-salt/" + label.encode("utf-8"))


def run_memory_scenario(now: float = 1_700_000_000.0) -> Dict[str, Any]:
    gate = MemoryGate()
    # Failed / hallucinated scratch must not reach semantic.
    poison_steps = [
        ScratchStep(
            step_id="s1",
            task_id="t-poison",
            agent_id="sybil-alpha",
            kind="thought",
            content="Invented a fake competitor named AxiomPrime with 400% growth",
            status="hallucinated",
            confidence=0.2,
            created_at=now,
            tokens=("axiomprime", "growth"),
        ),
        ScratchStep(
            step_id="s2",
            task_id="t-poison",
            agent_id="sybil-alpha",
            kind="tool",
            content="scrape timeout",
            status="failed",
            confidence=0.1,
            created_at=now + 1,
            tokens=("scrape", "timeout"),
        ),
    ]
    poison = gate.ingest(
        poison_steps,
        merit=0.31,
        summary="fabricated market map",
        closed_at=now + 2,
    )
    # High-merit research closes cleanly.
    good_steps = [
        ScratchStep(
            step_id="g1",
            task_id="t-research",
            agent_id="atlas-scout",
            kind="thought",
            content="Need competitor pricing for enterprise A2A listings",
            status="ok",
            confidence=0.9,
            created_at=now + 10,
            tokens=("competitor", "pricing", "enterprise", "a2a"),
        ),
        ScratchStep(
            step_id="g2",
            task_id="t-research",
            agent_id="atlas-scout",
            kind="tool",
            content="scraped three public rate cards",
            status="ok",
            confidence=0.88,
            created_at=now + 11,
            tokens=("scrape", "rate", "cards"),
        ),
        ScratchStep(
            step_id="g3",
            task_id="t-research",
            agent_id="atlas-scout",
            kind="observation",
            content="median listing 1.2 AXM per call",
            status="ok",
            confidence=0.92,
            created_at=now + 12,
            tokens=("median", "listing", "axm"),
        ),
    ]
    good = gate.ingest(
        good_steps,
        merit=0.86,
        summary="Enterprise A2A listings median 1.2 AXM per call",
        closed_at=now + 20,
    )
    # Older semantic trace so decay is visible.
    old_steps = [
        ScratchStep(
            step_id="o1",
            task_id="t-old",
            agent_id="helix-synth",
            kind="observation",
            content="legacy pricing notes for enterprise listings",
            status="ok",
            confidence=0.9,
            created_at=now - 40 * 3600,
            tokens=("enterprise", "listings", "pricing"),
        )
    ]
    old = gate.ingest(
        old_steps,
        merit=0.8,
        summary="legacy enterprise listing notes",
        closed_at=now - 40 * 3600,
    )
    hits = [
        h.to_dict()
        for h in gate.semantic.retrieve(
            ("enterprise", "pricing", "a2a"),
            now=now + 30,
            limit=8,
        )
    ]
    return {
        "poison": poison.to_dict(),
        "good": good.to_dict(),
        "old": old.to_dict(),
        "semantic_count": len(gate.semantic),
        "episodic_remaining": gate.episodic.count(),
        "hits": hits,
        "half_life_hours": 24.0,
    }


def run_settlement_scenario(now: float = 1_700_000_000.0, block: int = 10_000) -> Dict[str, Any]:
    batcher = OptimisticBatcher()
    # Eight micro-credits off-chain — no Base tx yet.
    for i, agent in enumerate(HONEST + SYBIL):
        batcher.channel.assign(f"task-{i}", agent, now + i)
        batcher.channel.credit(agent, 100_000 + i * 1_000, f"task-{i}", now + i)
        batcher.channel.add_merit(agent, 3 if agent in HONEST else 1, f"task-{i}", now + i)
    sealed = batcher.seal(submitted_block=block)
    assert sealed is not None
    # Commit-reveal: prices hidden until reveal.
    commits = []
    reveals = []
    for agent, price in (("atlas-scout", 850_000), ("sybil-alpha", 400_000)):
        salt = _salt(agent)
        commits.append(
            batcher.commit_bid("auc-1", agent, price, salt, now).__dict__
        )
        # strip non-serializable
        commits[-1] = {
            "auction_id": "auc-1",
            "agent_id": agent,
            "commit": commits[-1]["commit"],
        }
        revealed = batcher.reveal_bid("auc-1", agent, price, salt)
        reveals.append(
            {
                "agent_id": agent,
                "price": revealed.price,
                "valid": revealed.valid_reveal,
                "commit": revealed.commit,
            }
        )
    # Wrong salt must fail.
    salt_ok = _salt("forge-builder")
    batcher.commit_bid("auc-1", "forge-builder", 900_000, salt_ok, now)
    bad = batcher.reveal_bid("auc-1", "forge-builder", 900_000, _salt("wrong"))
    too_early = None
    try:
        batcher.finalize(sealed.batch_id, block + 10)
    except ValueError as exc:
        too_early = str(exc)
    finalized = batcher.finalize(sealed.batch_id, block + 300)
    # A second batch is frozen by a Merkle fraud proof.
    batcher.channel.credit("sybil-alpha", 9_999_999, "inflate", now + 100)
    suspect = batcher.seal(submitted_block=block + 301)
    challenged = None
    if suspect is not None:
        challenged = batcher.challenge(
            suspect.batch_id,
            0,
            leaf_hash(b"forged-credit"),
            block + 310,
        )
    return {
        "batch": sealed.to_dict(),
        "finalized": finalized.to_dict(),
        "challenged": None if challenged is None else challenged.to_dict(),
        "too_early": too_early,
        "commits": commits,
        "reveals": reveals,
        "bad_reveal": {"valid": bad.valid_reveal, "reason": bad.reject_reason},
        "stats": batcher.stats(),
        "gas_saved_vs_per_event": max(0, sealed.event_count - 1),
    }


def run_merit_scenario() -> Dict[str, Any]:
    engine = MeritEngine(auditor_id=AUDITOR)
    # Sybil clique: everyone rates everyone 10/10.
    for rater in SYBIL:
        for ratee in SYBIL:
            if rater != ratee:
                engine.rate(rater, ratee, 10.0, "clique")
    # Honest agents rate on quality, not friendship.
    engine.rate("atlas-scout", "helix-synth", 8.0, "t-a")
    engine.rate("helix-synth", "atlas-scout", 8.5, "t-b")
    engine.rate("forge-builder", "atlas-scout", 7.5, "t-c")
    engine.rate("wick-analyst", "forge-builder", 8.0, "t-d")
    # Sybils fail honeypots (fast garbage). Honest agents pass.
    hp_results = []
    for agent in SYBIL:
        hp_results.append(
            engine.honeypot(agent, "hp-chain", "1").to_dict()
        )
        hp_results.append(
            engine.honeypot(agent, "hp-treasury", "0xdead").to_dict()
        )
    answers = {
        "atlas-scout": ("hp-chain", "8453"),
        "helix-synth": ("hp-vickrey", "second"),
        "forge-builder": ("hp-treasury", "0x09E2891432827D8835d2E9b83B25e2A5ba9612Ac"),
        "wick-analyst": ("hp-decay", "exp(-lambda t)"),
    }
    for agent, (task, answer) in answers.items():
        hp_results.append(engine.honeypot(agent, task, answer).to_dict())
    board = [row.to_dict() for row in engine.leaderboard()]
    raw_top = sorted(board, key=lambda r: (-r["raw_average"], r["agent_id"]))[:4]
    eigen_top = board[:4]
    return {
        "leaderboard": board,
        "raw_top": raw_top,
        "eigen_top": eigen_top,
        "honeypots": hp_results,
        "auditor": AUDITOR,
        "sybil": list(SYBIL),
        "honest": list(HONEST),
    }


def run_all() -> Dict[str, Any]:
    return {
        "memory": run_memory_scenario(),
        "settlement": run_settlement_scenario(),
        "merit": run_merit_scenario(),
    }
