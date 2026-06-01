"""
SINAX Configuration
===================
All runtime parameters for the SINAX geometric proof-navigation layer.
Every value can be overridden by an environment variable so that the
system can be tuned (or disabled entirely) without code changes.

Feature flag
------------
    SINAX_ENABLED=true|false   (default: true)

Staged-rollout mode
-------------------
    SINAX_MODE=analytics | suggest | active   (default: analytics)

    analytics  — SINAX runs passively; records graph data, computes
                 curvature, discovers lemmas, but does NOT influence the
                 tactic search that AxiomSolver executes.
    suggest    — SINAX surfaces candidate tactics and lemmas as ranked
                 suggestions that a human or orchestrator may accept.
    active     — SINAX geometric search drives tactic selection; AxiomSolver
                 still validates every resulting proof step via the verifier.
"""

from __future__ import annotations

import os


def _bool(key: str, default: bool) -> bool:
    val = os.getenv(key, str(default)).strip().lower()
    return val in ("1", "true", "yes", "on")


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Feature flag & rollout mode
# ---------------------------------------------------------------------------
SINAX_ENABLED: bool = _bool("SINAX_ENABLED", True)

# "analytics" | "suggest" | "active"
SINAX_MODE: str = os.getenv("SINAX_MODE", "analytics").strip().lower()

# ---------------------------------------------------------------------------
# Proof State Encoder (Phase 1)
# ---------------------------------------------------------------------------
ENCODER_VERSION: str = os.getenv("SINAX_ENCODER_VERSION", "1")
EMBEDDING_DIM: int = _int("SINAX_EMBEDDING_DIM", 256)
# Contrastive training temperature (τ)
CONTRASTIVE_TEMPERATURE: float = _float("SINAX_CONTRASTIVE_TEMP", 0.07)

# ---------------------------------------------------------------------------
# Proof Graph Store (Phase 2)
# ---------------------------------------------------------------------------
# Maximum nodes kept in-memory before LRU eviction
GRAPH_MAX_NODES: int = _int("SINAX_GRAPH_MAX_NODES", 50_000)
# Only write a transition to the graph after this verifier result
GRAPH_WRITE_ONLY_VERIFIED: bool = _bool("SINAX_GRAPH_WRITE_ONLY_VERIFIED", True)

# ---------------------------------------------------------------------------
# Geometric Search (Phase 3 & 4)
# ---------------------------------------------------------------------------
# Beam width for latent-space beam search
BEAM_WIDTH: int = _int("SINAX_BEAM_WIDTH", 8)
# k for k-nearest-neighbour retrieval
KNN_K: int = _int("SINAX_KNN_K", 16)
# Exploration weight (0 = pure exploit, 1 = pure explore)
EXPLORATION_WEIGHT: float = _float("SINAX_EXPLORATION_WEIGHT", 0.3)
# Similarity threshold below which we fall back to tactic search
FALLBACK_SIMILARITY_THRESHOLD: float = _float("SINAX_FALLBACK_THRESHOLD", 0.30)

# ---------------------------------------------------------------------------
# Curvature Analyzer (Phase 5)
# ---------------------------------------------------------------------------
# Minimum observations needed before emitting a curvature score
CURVATURE_MIN_OBS: int = _int("SINAX_CURVATURE_MIN_OBS", 5)

# ---------------------------------------------------------------------------
# Lemma Discovery (Phase 6)
# ---------------------------------------------------------------------------
# Minimum failure-cluster size before a bridging lemma is generated
LEMMA_MIN_CLUSTER_SIZE: int = _int("SINAX_LEMMA_MIN_CLUSTER_SIZE", 3)
# Maximum lemma candidates to surface per discovery cycle
LEMMA_TOP_K: int = _int("SINAX_LEMMA_TOP_K", 10)

# ---------------------------------------------------------------------------
# Verifier (abstracted — default delegates to AxiomSolver / Lean)
# ---------------------------------------------------------------------------
VERIFIER_BACKEND: str = os.getenv("SINAX_VERIFIER_BACKEND", "lean")
# Timeout in seconds for a single verification call
VERIFIER_TIMEOUT_SECONDS: int = _int("SINAX_VERIFIER_TIMEOUT", 30)


def get_config() -> dict:
    """Return a snapshot of the current SINAX configuration."""
    return {
        "enabled": SINAX_ENABLED,
        "mode": SINAX_MODE,
        "encoder": {
            "version": ENCODER_VERSION,
            "embedding_dim": EMBEDDING_DIM,
            "contrastive_temperature": CONTRASTIVE_TEMPERATURE,
        },
        "graph": {
            "max_nodes": GRAPH_MAX_NODES,
            "write_only_verified": GRAPH_WRITE_ONLY_VERIFIED,
        },
        "search": {
            "beam_width": BEAM_WIDTH,
            "knn_k": KNN_K,
            "exploration_weight": EXPLORATION_WEIGHT,
            "fallback_similarity_threshold": FALLBACK_SIMILARITY_THRESHOLD,
        },
        "curvature": {
            "min_observations": CURVATURE_MIN_OBS,
        },
        "lemma_discovery": {
            "min_cluster_size": LEMMA_MIN_CLUSTER_SIZE,
            "top_k": LEMMA_TOP_K,
        },
        "verifier": {
            "backend": VERIFIER_BACKEND,
            "timeout_seconds": VERIFIER_TIMEOUT_SECONDS,
        },
    }
