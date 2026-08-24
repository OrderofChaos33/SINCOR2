"""Types for the episodic / semantic memory gate.

Existing ``sincor2.memory_system.MemorySystem`` is unchanged. This package
adds a poison-resistant promotion path: scratchpad lives in episodic SQLite
and is purged on task close; the semantic vault only accepts high-merit,
non-failed traces.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

MERIT_THRESHOLD = 0.75
# Half-life of 24 hours: Score = similarity * e^{-λ t}
LAMBDA_PER_HOUR = 0.69314718056 / 24.0
POISON_STATUSES = frozenset(
    {"failed", "error", "hallucinated", "rejected", "timeout", "poison"}
)
VECTOR_DIM = 64


@dataclass
class ScratchStep:
    step_id: str
    task_id: str
    agent_id: str
    kind: str  # thought | tool | observation | error
    content: str
    status: str  # ok | failed | hallucinated | error
    confidence: float
    created_at: float  # unix seconds
    tokens: Tuple[str, ...] = ()

    def is_poison(self) -> bool:
        if self.status.lower() in POISON_STATUSES:
            return True
        if self.confidence < 0.4:
            return True
        return False


@dataclass
class SemanticTrace:
    trace_id: str
    agent_id: str
    task_id: str
    content: str
    tokens: Tuple[str, ...]
    vector: Tuple[float, ...]
    merit: float
    created_at: float
    source_hash: str
    promoted_from: str = "high_merit_close"


@dataclass
class RetrievalHit:
    trace: SemanticTrace
    cosine: float
    decay: float
    score: float
    age_hours: float

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["trace"] = asdict(self.trace)
        payload["trace"].pop("vector", None)
        return payload


@dataclass
class GateResult:
    task_id: str
    agent_id: str
    merit: float
    steps_recorded: int
    poison_blocked: int
    promoted: bool
    promote_reason: str
    purged: int
    semantic_id: Optional[str] = None
    episodic_remaining: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
