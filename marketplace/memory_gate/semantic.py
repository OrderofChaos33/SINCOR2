"""Semantic vault — hashed 64-d vectors, high-merit inserts only.

Failed, hallucinated, low-confidence, and low-merit traces are refused so
they cannot poison future retrieval.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from marketplace.contract_net.vectors import cosine_similarity, embed_tokens

from .decay import decay_factor, ebbinghaus
from .types import (
    LAMBDA_PER_HOUR,
    MERIT_THRESHOLD,
    POISON_STATUSES,
    RetrievalHit,
    ScratchStep,
    SemanticTrace,
    VECTOR_DIM,
)


class SemanticVault:
    def __init__(
        self,
        merit_threshold: float = MERIT_THRESHOLD,
        lam: float = LAMBDA_PER_HOUR,
        dim: int = VECTOR_DIM,
    ) -> None:
        if not (0.0 < merit_threshold <= 1.0):
            raise ValueError("merit_threshold must be in (0, 1]")
        self.merit_threshold = merit_threshold
        self.lam = lam
        self.dim = dim
        self._traces: Dict[str, SemanticTrace] = {}

    def eligible(self, merit: float, status: str, confidence: float) -> Tuple[bool, str]:
        if status.lower() in POISON_STATUSES:
            return False, f"poison_status:{status}"
        if confidence < 0.4:
            return False, "low_confidence"
        if merit < self.merit_threshold:
            return False, f"merit_below_threshold:{merit:.3f}<{self.merit_threshold:.3f}"
        return True, "ok"

    def insert(
        self,
        trace: SemanticTrace,
        *,
        status: str = "ok",
        confidence: float = 1.0,
    ) -> Tuple[bool, str]:
        ok, reason = self.eligible(trace.merit, status, confidence)
        if not ok:
            return False, reason
        if len(trace.vector) != self.dim:
            return False, "vector_dim_mismatch"
        self._traces[trace.trace_id] = trace
        return True, "promoted"

    def promote_from_steps(
        self,
        steps: Sequence[ScratchStep],
        *,
        trace_id: str,
        merit: float,
        source_hash: str,
        summary: str,
        created_at: float,
    ) -> Tuple[Optional[SemanticTrace], str]:
        if not steps:
            return None, "no_steps"
        if any(step.is_poison() for step in steps) and merit < 0.95:
            # A poisoned scratchpad cannot leak into the vault unless an
            # independent high-merit auditor later certifies a clean summary.
            poison_n = sum(1 for step in steps if step.is_poison())
            if poison_n == len(steps):
                return None, "all_steps_poison"
        tokens: List[str] = []
        for step in steps:
            if step.is_poison():
                continue
            tokens.extend(step.tokens)
            tokens.extend(w.lower() for w in step.content.split() if len(w) > 2)
        if not tokens:
            return None, "no_clean_tokens"
        ok, reason = self.eligible(merit, "ok", 1.0)
        if not ok:
            return None, reason
        vector = tuple(embed_tokens(tokens, dim=self.dim))
        trace = SemanticTrace(
            trace_id=trace_id,
            agent_id=steps[0].agent_id,
            task_id=steps[0].task_id,
            content=summary,
            tokens=tuple(tokens[:48]),
            vector=vector,
            merit=merit,
            created_at=created_at,
            source_hash=source_hash,
        )
        stored, store_reason = self.insert(trace, status="ok", confidence=1.0)
        if not stored:
            return None, store_reason
        return trace, store_reason

    def retrieve(
        self,
        query_tokens: Sequence[str],
        *,
        now: float,
        limit: int = 8,
    ) -> List[RetrievalHit]:
        if not self._traces:
            return []
        query_vec = embed_tokens(list(query_tokens), dim=self.dim)
        hits: List[RetrievalHit] = []
        for trace in self._traces.values():
            cosine = cosine_similarity(query_vec, list(trace.vector))
            age_hours = max(0.0, (now - trace.created_at) / 3600.0)
            decay = decay_factor(age_hours, self.lam)
            score = ebbinghaus(cosine, age_hours, self.lam)
            hits.append(
                RetrievalHit(
                    trace=trace,
                    cosine=cosine,
                    decay=decay,
                    score=score,
                    age_hours=age_hours,
                )
            )
        hits.sort(key=lambda h: (-h.score, -h.trace.merit, h.trace.trace_id))
        return hits[:limit]

    def __len__(self) -> int:
        return len(self._traces)

    def __bool__(self) -> bool:
        return True

    def traces(self) -> List[SemanticTrace]:
        return list(self._traces.values())
