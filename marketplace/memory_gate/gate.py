"""Orchestrates episodic write → merit gate → optional semantic promote → purge."""

from __future__ import annotations

from typing import Sequence

from marketplace.contract_net.keccak import keccak256_hex

from .episodic import EpisodicStore
from .semantic import SemanticVault
from .types import GateResult, ScratchStep


class MemoryGate:
    def __init__(
        self,
        episodic: EpisodicStore | None = None,
        semantic: SemanticVault | None = None,
    ) -> None:
        self.episodic = episodic if episodic is not None else EpisodicStore()
        self.semantic = semantic if semantic is not None else SemanticVault()

    def record_step(self, step: ScratchStep) -> None:
        self.episodic.append(step)

    def close_task(
        self,
        task_id: str,
        *,
        merit: float,
        summary: str,
        closed_at: float,
    ) -> GateResult:
        steps = self.episodic.list_task(task_id)
        if not steps:
            return GateResult(
                task_id=task_id,
                agent_id="",
                merit=merit,
                steps_recorded=0,
                poison_blocked=0,
                promoted=False,
                promote_reason="no_episodic_steps",
                purged=0,
            )
        agent_id = steps[0].agent_id
        poison_blocked = sum(1 for step in steps if step.is_poison())
        source = keccak256_hex(
            (task_id + "|" + summary + "|" + str(round(merit, 4))).encode("utf-8")
        )
        trace_id = "sem-" + source[2:18]
        trace, reason = self.semantic.promote_from_steps(
            steps,
            trace_id=trace_id,
            merit=merit,
            source_hash=source,
            summary=summary,
            created_at=closed_at,
        )
        purged = self.episodic.purge_task(task_id)
        return GateResult(
            task_id=task_id,
            agent_id=agent_id,
            merit=merit,
            steps_recorded=len(steps),
            poison_blocked=poison_blocked,
            promoted=trace is not None,
            promote_reason=reason,
            purged=purged,
            semantic_id=None if trace is None else trace.trace_id,
            episodic_remaining=self.episodic.count(task_id),
        )

    def ingest(
        self,
        steps: Sequence[ScratchStep],
        *,
        merit: float,
        summary: str,
        closed_at: float,
    ) -> GateResult:
        if not steps:
            raise ValueError("steps required")
        for step in steps:
            self.record_step(step)
        return self.close_task(
            steps[0].task_id,
            merit=merit,
            summary=summary,
            closed_at=closed_at,
        )
