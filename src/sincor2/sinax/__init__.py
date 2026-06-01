#!/usr/bin/env python3
"""
SINAX — Proof Topology Navigator

Package exports:
    ProofTopologyNavigator  — top-level PTN API
    AxiomSolver             — full end-to-end solver
    ProofManifold           — Layer 1: Embedding Manifold
    GeodesicFlowEngine      — Layer 2: Geodesic Flow Engine
    HomologyDetector        — Layer 3: Homology Detector
    MorseFilter             — Layer 4: Morse Theory Filter
    ProofResult             — result container
"""

from sincor2.sinax.proof_manifold import ProofManifold, ManifoldPoint, ManifoldRegion
from sincor2.sinax.geodesic_flow import GeodesicFlowEngine, GeodesicPath, FlowConfig
from sincor2.sinax.homology_detector import HomologyDetector, HomologyReport, HomologyClass
from sincor2.sinax.morse_filter import MorseFilter, MorseDecomposition, CriticalPoint
from sincor2.sinax.axiom_solver import AxiomSolver, ProofResult
from sincor2.sinax.ptn import ProofTopologyNavigator

__all__ = [
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
