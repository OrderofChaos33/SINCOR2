"""
SINAX — Geometric Proof Navigation Layer
==========================================

SINAX is an augmentation layer on top of AxiomSolver that navigates proof
space using dense vector embeddings and latent-space beam search rather than
only symbolic tactic trees.

Architecture
------------
::

    SINC (orchestration layer)
      └── AxiomSolver (formal proving / verification executor)
            └── SINAX (geometric navigation augmentation)
                  ├── encoder          — Proof State Encoder
                  ├── graph_store      — Proof Graph Store
                  ├── retrieval        — Embedding Retrieval
                  ├── search           — Geometric Search Engine
                  ├── curvature        — Curvature Analyzer
                  ├── lemma_discovery  — Lemma Discovery Engine
                  ├── integration      — A2A-Agnostic Integration Layer
                  ├── visualization    — Visualization Layer
                  ├── proof_manifold   — Layer 1: Embedding Manifold
                  ├── geodesic_flow    — Layer 2: Geodesic Flow Engine
                  ├── homology_detector — Layer 3: Homology Detector
                  ├── morse_filter     — Layer 4: Morse Theory Filter
                  └── ptn              — Proof Topology Navigator

Contract
--------
* SINAX **proposes / searches**; only AxiomSolver + Lean/verifier can
  **certify** proof validity.
* SINAX is guarded by a feature flag (``SINAX_ENABLED``) and a staged
  rollout mode (``SINAX_MODE``): ``analytics`` → ``suggest`` → ``active``.

Quick start
-----------
::

    from sincor2.sinax import (
        ProofState, get_encoder, get_store, get_search_engine,
        get_curvature_analyzer, get_lemma_engine, get_dashboard,
        get_config,
    )

    # Encode a proof state
    state = ProofState(goal="⊢ n + 0 = n", hypotheses=["n : ℕ"])
    emb = get_encoder().encode(state)

    # Search for tactic candidates
    tactics = get_search_engine().generate_tactics(state)

    # Analyse curvature of a graph region
    # (call after populating the proof graph store)
    hotspots = get_curvature_analyzer().top_curvature_nodes()

    # Dashboard
    summary = get_dashboard().render_summary()

    # PTN top-level API
    from sincor2.sinax import ProofTopologyNavigator
    nav = ProofTopologyNavigator(manifold_dim=64)
    result = nav.solve("⊢ n + 0 = n", "closed")
"""

from __future__ import annotations

from .config import SINAX_ENABLED, SINAX_MODE, get_config

# Phase 1
from .encoder import (
    BaseEncoder,
    Embedding,
    HashingEncoder,
    ProofState,
    contrastive_loss,
    get_encoder,
    register_encoder,
)

# Phase 2
from .graph_store import (
    ProofGraphStore,
    ProofStateNode,
    TacticEdge,
    VerificationResult,
    get_store,
)

# Phase 3
from .retrieval import (
    EmbeddingIndex,
    LinearScanIndex,
    RetrievalService,
    get_retrieval_service,
)

# Phase 4
from .search import (
    GeometricSearchEngine,
    SearchCandidate,
    get_search_engine,
)

# Phase 5
from .curvature import (
    CurvatureAnalyzer,
    RegionCurvature,
    get_curvature_analyzer,
)

# Phase 6
from .lemma_discovery import (
    LemmaCandidate,
    LemmaDiscoveryEngine,
    Verifier,
    get_lemma_engine,
)

# Phase 7 — Integration
from .integration import (
    A2AAdapter,
    BaseAdapter,
    ConjectureMessage,
    IntegrationRouter,
    LemmaMessage,
    LocalAdapter,
    MCPAdapter,
    ProofStateMessage,
    RESTAdapter,
    Task,
    TaskStatus,
    TaskType,
    Trajectory,
    VerificationResultMessage,
    get_router,
)

# Phase 7 — Visualization
from .visualization import (
    CurvatureHeatmap,
    GraphVisualizer,
    LemmaReport,
    SINAXDashboard,
    TrajectoryVisualizer,
    get_dashboard,
)

# Proof Topology Navigator (PTN) layers
from .proof_manifold import ProofManifold, ManifoldPoint, ManifoldRegion
from .geodesic_flow import GeodesicFlowEngine, GeodesicPath, FlowConfig
from .homology_detector import HomologyDetector, HomologyReport, HomologyClass
from .morse_filter import MorseFilter, MorseDecomposition, CriticalPoint
from .axiom_solver import AxiomSolver, ProofResult
from .ptn import ProofTopologyNavigator

__all__ = [
    # Config
    "SINAX_ENABLED",
    "SINAX_MODE",
    "get_config",
    # Encoder
    "BaseEncoder",
    "Embedding",
    "HashingEncoder",
    "ProofState",
    "contrastive_loss",
    "get_encoder",
    "register_encoder",
    # Graph Store
    "ProofGraphStore",
    "ProofStateNode",
    "TacticEdge",
    "VerificationResult",
    "get_store",
    # Retrieval
    "EmbeddingIndex",
    "LinearScanIndex",
    "RetrievalService",
    "get_retrieval_service",
    # Search
    "GeometricSearchEngine",
    "SearchCandidate",
    "get_search_engine",
    # Curvature
    "CurvatureAnalyzer",
    "RegionCurvature",
    "get_curvature_analyzer",
    # Lemma Discovery
    "LemmaCandidate",
    "LemmaDiscoveryEngine",
    "Verifier",
    "get_lemma_engine",
    # Integration
    "A2AAdapter",
    "BaseAdapter",
    "ConjectureMessage",
    "IntegrationRouter",
    "LemmaMessage",
    "LocalAdapter",
    "MCPAdapter",
    "ProofStateMessage",
    "RESTAdapter",
    "Task",
    "TaskStatus",
    "TaskType",
    "Trajectory",
    "VerificationResultMessage",
    "get_router",
    # Visualization
    "CurvatureHeatmap",
    "GraphVisualizer",
    "LemmaReport",
    "SINAXDashboard",
    "TrajectoryVisualizer",
    "get_dashboard",
    # PTN layers
    "ProofTopologyNavigator",
    "AxiomSolver",
    "ProofResult",
    "ProofManifold",
    "ManifoldPoint",
    "ManifoldRegion",
    "GeodesicFlowEngine",
    "GeodesicPath",
    "FlowConfig",
    "HomologyDetector",
    "HomologyReport",
    "HomologyClass",
    "MorseFilter",
    "MorseDecomposition",
    "CriticalPoint",
]
