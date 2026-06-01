#!/usr/bin/env python3
"""
SINAX — Proof Topology Navigator (PTN)

Top-level orchestrator that exposes the full PTN system as a single
callable object.  Designed to be used:
  - Directly by Python callers / other SINCOR2 modules.
  - Via the Flask REST API (see app.py for route registration).
  - As a drop-in replacement for any linear tactic-sequencing solver.

Quick start
-----------
    from sincor2.sinax.ptn import ProofTopologyNavigator

    ptn = ProofTopologyNavigator()

    result = ptn.solve(
        start_state="⊢ ∀ n : ℕ, n + 0 = n",
        target_state="closed",
    )

    print(result.proof_narrative)
    print(result.tactic_sequence)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sincor2.sinax.axiom_solver import AxiomSolver, ProofResult
from sincor2.sinax.geodesic_flow import FlowConfig
from sincor2.sinax.homology_detector import HomologyReport
from sincor2.sinax.morse_filter import MorseDecomposition
from sincor2.sinax.proof_manifold import ProofManifold


class ProofTopologyNavigator:
    """
    Proof Topology Navigator — SINAX top-level API.

    Wraps AxiomSolver with convenience methods for the four PTN layers:

    +-------------------+-----------------------------------------------+
    | Method            | PTN Layer                                     |
    +===================+===============================================+
    | embed()           | Layer 1: Embedding Manifold                   |
    | geodesic()        | Layer 2: Geodesic Flow Engine                 |
    | homology()        | Layer 3: Homology Detector                    |
    | morse()           | Layer 4: Morse Theory Filter                  |
    | solve()           | All four layers end-to-end                    |
    +-------------------+-----------------------------------------------+

    Parameters
    ----------
    manifold_dim : int
        Dimensionality of the proof manifold (default 64).
    num_flow_steps : int
        Number of Euler integration steps for geodesic flow (default 20).
    teleport_threshold : float
        Distance below which a direct shortcut is taken (default 0.10).
    """

    def __init__(
        self,
        manifold_dim: int = 64,
        num_flow_steps: int = 20,
        teleport_threshold: float = 0.10,
        max_radius: float = 2.0,
    ):
        flow_config = FlowConfig(
            num_steps=num_flow_steps,
            teleport_threshold=teleport_threshold,
        )
        self._solver = AxiomSolver(
            manifold_dim=manifold_dim,
            flow_config=flow_config,
            max_radius=max_radius,
        )

    # ------------------------------------------------------------------
    # Layer-by-layer access
    # ------------------------------------------------------------------

    @property
    def manifold(self) -> ProofManifold:
        """Direct access to the underlying ProofManifold (Layer 1)."""
        return self._solver.manifold

    def embed(self, proof_state: str) -> dict:
        """
        Layer 1: Embed a proof state onto the manifold.

        Returns a dict with ``coordinates`` (list), ``curvature`` (float),
        and ``source_state`` (str).
        """
        point = self._solver.manifold.embed(proof_state)
        return {
            "source_state": point.source_state,
            "coordinates": point.coordinates.tolist(),
            "curvature": point.curvature,
        }

    def geodesic(
        self,
        start_state: str,
        target_state: str,
        max_time: float = 1.0,
    ) -> dict:
        """
        Layer 2: Compute the geodesic flow between two proof states.

        Returns a dict suitable for JSON serialisation.
        """
        path = self._solver.flow_engine.geodesic_flow(
            start_state, target_state, max_time=max_time
        )
        return {
            "start_state": path.start_state,
            "target_state": path.target_state,
            "tactics": path.tactics,
            "path_length": path.path_length,
            "num_steps": path.num_steps,
            "converged": path.converged,
            "teleported": path.teleported,
        }

    def homology(self, proof_states: List[str]) -> dict:
        """
        Layer 3: Compute homology of a set of proof states.

        Returns Betti numbers and hole-filling suggestions.
        """
        report: HomologyReport = self._solver.homology_detector.analyse(proof_states)
        return {
            "num_states": report.num_states,
            "has_holes": report.has_holes,
            "betti_numbers": report.betti_numbers,
            "num_components": report.num_components,
            "hole_filling_suggestions": report.hole_filling_suggestions,
        }

    def morse(self, proof_states: List[str]) -> dict:
        """
        Layer 4: Morse decomposition of a set of proof states.

        Returns key lemmas, branch points, and the minimum proof-length bound.
        """
        decomp: MorseDecomposition = self._solver.morse_filter.decompose(proof_states)
        return {
            "key_lemmas": decomp.key_lemmas,
            "branch_points": decomp.branch_points,
            "min_proof_length_bound": decomp.min_proof_length_bound,
            "num_critical_points": len(decomp.critical_points),
        }

    # ------------------------------------------------------------------
    # Full solve
    # ------------------------------------------------------------------

    def solve(
        self,
        start_state: str,
        target_state: str,
        context_states: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ProofResult:
        """
        End-to-end proof search through all four PTN layers.

        Parameters
        ----------
        start_state : str
            Initial proof state.
        target_state : str
            Desired final proof state.
        context_states : list[str], optional
            Additional intermediate states or lemmas.
        metadata : dict, optional
            Passed through to ProofResult.

        Returns
        -------
        ProofResult
            Full result including geodesic, homology, Morse decomposition,
            tactic sequence, and human-readable narrative.
        """
        return self._solver.solve(
            start_state=start_state,
            target_state=target_state,
            context_states=context_states,
            metadata=metadata,
        )

    def solve_batch(self, problems: List[Dict[str, str]]) -> List[ProofResult]:
        """
        Solve multiple proof problems, reusing manifold geometry for
        proof transfer across related theorems.
        """
        return self._solver.solve_batch(problems)

    def transfer_score(self, existing_result: ProofResult, new_target: str) -> float:
        """
        Estimate geodesic reuse between an existing proof and a new target.
        """
        return self._solver.transfer_score(existing_result, new_target)

    def training_signal(self, verified_states: List[str]) -> dict:
        """
        Extract curvature and homology data from verified proof states for
        the Verified Data Flywheel.
        """
        return self._solver.get_manifold_training_signal(verified_states)
