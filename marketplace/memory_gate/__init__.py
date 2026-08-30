"""Poison-resistant episodic / semantic memory gate."""

from .decay import decay_factor, ebbinghaus, half_life_hours
from .episodic import EpisodicStore
from .gate import MemoryGate
from .semantic import SemanticVault
from .types import (
    LAMBDA_PER_HOUR,
    MERIT_THRESHOLD,
    GateResult,
    RetrievalHit,
    ScratchStep,
    SemanticTrace,
)

__all__ = [
    "LAMBDA_PER_HOUR",
    "MERIT_THRESHOLD",
    "EpisodicStore",
    "GateResult",
    "MemoryGate",
    "RetrievalHit",
    "ScratchStep",
    "SemanticTrace",
    "SemanticVault",
    "decay_factor",
    "ebbinghaus",
    "half_life_hours",
]
