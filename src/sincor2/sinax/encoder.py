"""
SINAX — Phase 1: Proof State Encoder
=====================================
Converts proof states into fixed-length dense vector embeddings that can
be used for nearest-neighbour retrieval and contrastive training.

Design
------
* Deterministic versioning: every embedding carries a ``model_version``
  tag so that graphs built with different encoder versions remain
  distinguishable.
* Contrastive training support: ``contrastive_loss`` computes the
  NT-Xent (InfoNCE) loss given batches of anchor and positive embeddings.
* The default hashing encoder requires **no external ML dependencies**
  (only numpy, which is already in requirements.txt).  A heavier neural
  encoder can be swapped in by subclassing ``BaseEncoder``.

Public API
----------
    ProofState        — canonical input schema
    Embedding         — output value-object (vector + metadata)
    HashingEncoder    — default, deterministic encoder (no model download)
    BaseEncoder       — abstract base class for custom encoders
    get_encoder()     — factory respecting SINAX_ENCODER_VERSION env var
    contrastive_loss  — NT-Xent loss for training
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import config as cfg

logger = logging.getLogger("sincor.sinax.encoder")

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ProofState:
    """Canonical proof-state input schema.

    Attributes
    ----------
    goal:
        The current proof goal (as a string, e.g. Lean syntax).
    context:
        Local context / local hypotheses visible at this proof step.
    hypotheses:
        Active global hypotheses relevant to the current goal.
    tactic_history:
        Ordered list of tactics applied so far to reach this state.
    metadata:
        Arbitrary extra key-value pairs (theorem name, file, etc.).
    """

    goal: str
    context: str = ""
    hypotheses: List[str] = field(default_factory=list)
    tactic_history: List[str] = field(default_factory=list)
    metadata: Dict[str, str] = field(default_factory=dict)

    def canonical_text(self) -> str:
        """Produce a canonical string representation used by the encoder."""
        hyps = " | ".join(self.hypotheses)
        history = " -> ".join(self.tactic_history)
        parts = [
            f"GOAL:{self.goal}",
            f"CTX:{self.context}",
            f"HYP:{hyps}",
            f"HIST:{history}",
        ]
        return "  ".join(parts)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Embedding:
    """Output value-object for an encoded proof state.

    Attributes
    ----------
    vector:
        The dense embedding as a 1-D numpy array (float32).
    model_version:
        Encoder version string, used when comparing embeddings across runs.
    proof_state_hash:
        SHA-256 hex digest of the canonical proof-state text.
    dim:
        Embedding dimensionality.
    """

    vector: np.ndarray
    model_version: str
    proof_state_hash: str
    dim: int

    def normalised(self) -> "Embedding":
        """Return a copy with L2-normalised vector."""
        norm = float(np.linalg.norm(self.vector)) or 1.0
        return Embedding(
            vector=self.vector / norm,
            model_version=self.model_version,
            proof_state_hash=self.proof_state_hash,
            dim=self.dim,
        )

    def cosine_similarity(self, other: "Embedding") -> float:
        """Cosine similarity with another embedding in [-1, 1]."""
        a = self.vector / (float(np.linalg.norm(self.vector)) or 1.0)
        b = other.vector / (float(np.linalg.norm(other.vector)) or 1.0)
        return float(np.dot(a, b))


# ---------------------------------------------------------------------------
# Encoder ABC
# ---------------------------------------------------------------------------


class BaseEncoder(ABC):
    """Abstract base class for SINAX proof-state encoders.

    Subclass this to plug in a neural or LLM-based encoder.
    """

    @property
    @abstractmethod
    def version(self) -> str:
        """Unique version string for this encoder."""

    @property
    @abstractmethod
    def dim(self) -> int:
        """Output embedding dimensionality."""

    @abstractmethod
    def encode(self, state: ProofState) -> Embedding:
        """Encode a single proof state into an Embedding."""

    def encode_batch(self, states: Sequence[ProofState]) -> List[Embedding]:
        """Encode a batch (default: sequential; override for efficiency)."""
        return [self.encode(s) for s in states]

    # ------------------------------------------------------------------
    # Contrastive training support
    # ------------------------------------------------------------------

    def contrastive_loss(
        self,
        anchors: Sequence[Embedding],
        positives: Sequence[Embedding],
        temperature: Optional[float] = None,
    ) -> float:
        """NT-Xent (InfoNCE) loss for a batch of (anchor, positive) pairs.

        Both sequences must have the same length N.  Negatives are formed
        in-batch (all other pairs in the batch serve as negatives for each
        anchor).

        Parameters
        ----------
        anchors:
            N anchor embeddings.
        positives:
            N corresponding positive embeddings (augmented or similar states).
        temperature:
            Softmax temperature τ.  Defaults to ``cfg.CONTRASTIVE_TEMPERATURE``.

        Returns
        -------
        float
            Mean NT-Xent loss over the batch.
        """
        tau = temperature if temperature is not None else cfg.CONTRASTIVE_TEMPERATURE
        if len(anchors) != len(positives):
            raise ValueError("anchors and positives must have the same length")
        n = len(anchors)
        if n == 0:
            return 0.0

        # Stack into (N, D) matrices and L2-normalise rows
        a_mat = np.stack([e.vector for e in anchors]).astype(np.float64)
        p_mat = np.stack([e.vector for e in positives]).astype(np.float64)
        a_mat /= np.linalg.norm(a_mat, axis=1, keepdims=True) + 1e-12
        p_mat /= np.linalg.norm(p_mat, axis=1, keepdims=True) + 1e-12

        # Compute (N, N) similarity matrix between anchors and all positives
        sim = (a_mat @ p_mat.T) / tau  # (N, N)

        # Positive is the diagonal; negatives are off-diagonal
        loss = 0.0
        for i in range(n):
            log_softmax_pos = sim[i, i] - math.log(
                sum(math.exp(sim[i, j]) for j in range(n)) + 1e-12
            )
            loss -= log_softmax_pos
        return loss / n


# ---------------------------------------------------------------------------
# Hashing Encoder (default, no ML deps)
# ---------------------------------------------------------------------------


class HashingEncoder(BaseEncoder):
    """Deterministic encoder based on feature hashing.

    Produces a fixed-length ``EMBEDDING_DIM``-dimensional float32 vector by
    hashing the canonical proof-state text and distributing the hash bits
    across the embedding dimensions.  This is intentionally simple —
    semantics are approximated, not learned — but it is deterministic, fast,
    requires no model download, and serves as a useful baseline.

    Override with a neural encoder (e.g. sentence-transformer) for
    production-quality embeddings.
    """

    _VERSION_PREFIX = "hashing"

    def __init__(self, dim: Optional[int] = None, version: Optional[str] = None):
        self._dim = dim or cfg.EMBEDDING_DIM
        self._version = version or f"{self._VERSION_PREFIX}-v{cfg.ENCODER_VERSION}"

    @property
    def version(self) -> str:
        return self._version

    @property
    def dim(self) -> int:
        return self._dim

    def encode(self, state: ProofState) -> Embedding:
        text = state.canonical_text()
        digest = hashlib.sha256(text.encode()).hexdigest()

        # Use SHA-256 as a seed for a reproducible RNG
        seed = int(digest[:8], 16)
        rng = np.random.default_rng(seed)

        # Build the vector by hashing overlapping windows of the text
        vec = np.zeros(self._dim, dtype=np.float32)
        tokens = text.split()
        for idx, token in enumerate(tokens):
            h = hashlib.md5((token + str(idx % 17)).encode()).digest()
            # Read 4 bytes at a time, map to a bucket
            for j in range(0, min(len(h), self._dim * 4), 4):
                bucket = (h[j] ^ (h[j + 1] if j + 1 < len(h) else 0)) % self._dim
                sign = 1.0 if h[j] & 1 else -1.0
                vec[bucket] += sign

        # Add a small RNG perturbation for less-sparse coverage
        vec += rng.normal(0.0, 0.01, self._dim).astype(np.float32)

        return Embedding(
            vector=vec,
            model_version=self._version,
            proof_state_hash=digest,
            dim=self._dim,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_REGISTRY: Dict[str, BaseEncoder] = {}


def register_encoder(name: str, encoder: BaseEncoder) -> None:
    """Register a custom encoder under ``name``.

    Call this before ``get_encoder()`` to override the default.
    """
    _REGISTRY[name] = encoder
    logger.info("Registered SINAX encoder: %s (dim=%d)", name, encoder.dim)


def get_encoder(version: Optional[str] = None) -> BaseEncoder:
    """Return the active encoder, respecting ``SINAX_ENCODER_VERSION`` env var.

    Parameters
    ----------
    version:
        Explicit version key.  Falls back to ``cfg.ENCODER_VERSION``.

    Returns
    -------
    BaseEncoder
        The registered encoder, or a fresh ``HashingEncoder`` if none is
        registered under the requested version.
    """
    key = version or cfg.ENCODER_VERSION
    if key in _REGISTRY:
        return _REGISTRY[key]
    logger.debug("No registered encoder for version '%s'; using HashingEncoder", key)
    return HashingEncoder()


# ---------------------------------------------------------------------------
# Convenience helper
# ---------------------------------------------------------------------------


def contrastive_loss(
    anchors: Sequence[Embedding],
    positives: Sequence[Embedding],
    temperature: Optional[float] = None,
) -> float:
    """Module-level convenience wrapper for ``BaseEncoder.contrastive_loss``."""
    return HashingEncoder().contrastive_loss(anchors, positives, temperature)
