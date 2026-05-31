#!/usr/bin/env python3
"""
SINAX — AxiomSolver

Integrates the four PTN layers into a single solver that can be called
by SINCOR2 agents or Flask routes.

AxiomSolver replaces discrete tactic sequences with a three-phase pipeline:

Phase 1 — Embed
  Map the start and target proof states onto the Riemannian manifold
  (ProofManifold, Layer 1).

Phase 2 — Navigate
  Compute the geodesic from start to target
  (GeodesicFlowEngine, Layer 2).
  Simultaneously run Homology Detection (Layer 3) over any previously
  accumulated failed states to identify topological holes and suggest
  bridging lemmas.

Phase 3 — Filter & Report
  Apply the Morse filter (Layer 4) to the path, retaining only the
  critical-point waypoints, and emit a human-readable proof narrative.

The result is a ProofResult that can be serialised to JSON and fed back
into the SINCOR2 agent ecosystem or exposed via the Flask API.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from sincor2.sinax.geodesic_flow import GeodesicFlowEngine, GeodesicPath, FlowConfig
from sincor2.sinax.homology_detector import HomologyDetector, HomologyReport
from sincor2.sinax.morse_filter import MorseDecomposition, MorseFilter
from sincor2.sinax.proof_manifold import ProofManifold


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class ProofResult:
    """Complete result returned by AxiomSolver."""
    proof_id: str
    start_state: str
    target_state: str

    # Layer 2 output
    geodesic: GeodesicPath

    # Layer 3 output
    homology: HomologyReport

    # Layer 4 output
    morse: MorseDecomposition

    # Filtered tactic sequence (after Morse pruning)
    tactic_sequence: List[str]

    # Human-readable narrative
    proof_narrative: str

    # Performance telemetry
    elapsed_seconds: float
    manifold_dim: int
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.geodesic.converged

    @property
    def min_steps_bound(self) -> int:
        return self.morse.min_proof_length_bound

    def to_dict(self) -> dict:
        """Serialise to a JSON-compatible dict (omits numpy arrays)."""
        return {
            "proof_id": self.proof_id,
            "start_state": self.start_state,
            "target_state": self.target_state,
            "succeeded": self.succeeded,
            "tactic_sequence": self.tactic_sequence,
            "num_tactics": len(self.tactic_sequence),
            "min_steps_bound": self.min_steps_bound,
            "path_length": self.geodesic.path_length,
            "teleported": self.geodesic.teleported,
            "num_waypoints": self.geodesic.num_steps,
            "homology": {
                "has_holes": self.homology.has_holes,
                "betti_numbers": self.homology.betti_numbers,
                "hole_filling_suggestions": self.homology.hole_filling_suggestions,
            },
            "morse": {
                "key_lemmas": self.morse.key_lemmas,
                "branch_points": self.morse.branch_points,
                "min_proof_length_bound": self.morse.min_proof_length_bound,
            },
            "proof_narrative": self.proof_narrative,
            "elapsed_seconds": round(self.elapsed_seconds, 4),
            "manifold_dim": self.manifold_dim,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# AxiomSolver
# ---------------------------------------------------------------------------

class AxiomSolver:
    """
    SINAX AxiomSolver — Proof Topology Navigator integration.

    Parameters
    ----------
    manifold_dim : int
        Dimensionality of the embedding manifold (default 64).
    flow_config : FlowConfig, optional
        Hyper-parameters for the geodesic integration.
    max_radius : float
        Upper bound for the homology filtration radius.
    collect_failed_states : bool
        If True, accumulate failed proof states across calls for richer
        homology analysis.
    """

    def __init__(
        self,
        manifold_dim: int = 64,
        flow_config: Optional[FlowConfig] = None,
        max_radius: float = 2.0,
        collect_failed_states: bool = True,
    ):
        self.manifold = ProofManifold(dim=manifold_dim)
        self.flow_engine = GeodesicFlowEngine(
            manifold=self.manifold,
            config=flow_config or FlowConfig(),
        )
        self.homology_detector = HomologyDetector(
            manifold=self.manifold,
            max_radius=max_radius,
        )
        self.morse_filter = MorseFilter(manifold=self.manifold)
        self._failed_states: List[str] = []
        self._collect_failed = collect_failed_states

    # ------------------------------------------------------------------
    # Main solve entry point
    # ------------------------------------------------------------------

    def solve(
        self,
        start_state: str,
        target_state: str,
        context_states: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ProofResult:
        """
        Attempt to find a proof path from *start_state* to *target_state*.

        Parameters
        ----------
        start_state : str
            The initial proof state (e.g. Lean goal string, informal statement).
        target_state : str
            The desired proof state (e.g. "closed" / "⊢ False" / specific goal).
        context_states : list[str], optional
            Additional intermediate states (lemmas, hypotheses) to seed the
            manifold and improve homology analysis.
        metadata : dict, optional
            Arbitrary metadata passed through to the ProofResult.

        Returns
        -------
        ProofResult
        """
        t0 = time.perf_counter()
        proof_id = str(uuid.uuid4())[:8]

        # Seed the manifold with any context states
        all_states = [start_state, target_state] + (context_states or [])

        # Phase 1 + 2: Embed & geodesic flow
        geodesic = self.flow_engine.geodesic_flow(start_state, target_state)

        # Phase 3a: Homology over all available states
        homology_states = all_states + self._failed_states
        homology = self.homology_detector.analyse(homology_states)

        # Phase 3b: Morse decomposition over geodesic waypoint states
        waypoint_states = [wp.source_state for wp in geodesic.waypoints]
        morse = self.morse_filter.decompose(all_states + waypoint_states)

        # Phase 3c: Filter geodesic path through Morse critical points
        filtered_waypoints = self.morse_filter.filter_path(
            geodesic.waypoints, morse
        )

        # Build tactic sequence from filtered waypoints
        tactic_sequence = self._waypoints_to_tactics(filtered_waypoints, geodesic)

        # Generate human-readable narrative
        narrative = self._build_narrative(
            start_state, target_state, geodesic, homology, morse, tactic_sequence
        )

        # Track failed states for future runs
        if self._collect_failed and not geodesic.converged:
            self._failed_states.extend(waypoint_states[-3:])  # last 3 waypoints

        elapsed = time.perf_counter() - t0

        return ProofResult(
            proof_id=proof_id,
            start_state=start_state,
            target_state=target_state,
            geodesic=geodesic,
            homology=homology,
            morse=morse,
            tactic_sequence=tactic_sequence,
            proof_narrative=narrative,
            elapsed_seconds=elapsed,
            manifold_dim=self.manifold.dim,
            metadata=metadata or {},
        )

    # ------------------------------------------------------------------
    # Batch & transfer methods
    # ------------------------------------------------------------------

    def solve_batch(
        self,
        problems: List[Dict[str, str]],
    ) -> List[ProofResult]:
        """
        Solve multiple (start, target) pairs, reusing manifold geometry
        across problems for proof transfer efficiency.

        Each problem dict must have keys ``start`` and ``target``.
        """
        results = []
        for problem in problems:
            result = self.solve(
                start_state=problem["start"],
                target_state=problem["target"],
                context_states=problem.get("context"),
                metadata=problem.get("metadata"),
            )
            results.append(result)
        return results

    def transfer_score(self, existing_result: ProofResult, new_target: str) -> float:
        """
        Estimate how much of an existing proof can be reused for a new target.
        """
        return self.flow_engine.proof_transfer_score(
            existing_result.geodesic, new_target
        )

    def get_manifold_training_signal(self, verified_states: List[str]) -> dict:
        """
        Extract curvature/homology data for the Verified Data Flywheel.

        The returned dict can be used as an additional training signal:
        the manifold geometry is learned from verified proofs, making it
        a mathematically structured memory.
        """
        curvature_data = self.manifold.curvature_training_signal(verified_states)
        homology_data = self.homology_detector.analyse(verified_states)
        return {
            "curvature": curvature_data,
            "betti_numbers": homology_data.betti_numbers,
            "num_holes": len(
                [hc for hc in homology_data.classes if hc.dimension >= 1]
            ),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _waypoints_to_tactics(
        self,
        filtered_waypoints,
        geodesic: GeodesicPath,
    ) -> List[str]:
        """Map filtered waypoints back to tactic strings via the decoder."""
        tactics = []
        for wp in filtered_waypoints:
            tactic = self.flow_engine.decoder.decode(wp.coordinates)
            tactics.append(tactic)
        # Deduplicate consecutive identical tactics
        deduped = [tactics[0]] if tactics else []
        for t in tactics[1:]:
            if t != deduped[-1]:
                deduped.append(t)
        return deduped

    def _build_narrative(
        self,
        start: str,
        target: str,
        geodesic: GeodesicPath,
        homology: HomologyReport,
        morse: MorseDecomposition,
        tactics: List[str],
    ) -> str:
        """
        Render the proof as a "topological story" — a human-readable narrative
        describing how the proof navigated the manifold.

        This is the Auto-informalizer enhancement described in the PTN spec.
        """
        lines = [
            f"Proof Navigation Report (ID pending)",
            f"{'='*60}",
            f"",
            f"START : {start[:100]}",
            f"TARGET: {target[:100]}",
            f"",
            f"MANIFOLD JOURNEY",
            f"-" * 40,
        ]

        if geodesic.teleported:
            lines.append(
                "The start and target states were topologically adjacent — "
                "a direct shortcut was taken across the manifold."
            )
        elif geodesic.converged:
            lines.append(
                f"The geodesic flow converged in {geodesic.num_steps} steps "
                f"(path length: {geodesic.path_length:.4f})."
            )
        else:
            lines.append(
                f"The flow did not fully converge (path length: {geodesic.path_length:.4f}). "
                "The partial path below represents the furthest progress."
            )

        if morse.key_lemmas:
            lines.append("")
            lines.append("KEY LEMMAS (critical minima of the proof landscape):")
            for lemma in morse.key_lemmas[:5]:
                lines.append(f"  * {lemma[:80]}")

        if morse.branch_points:
            lines.append("")
            lines.append("BRANCH POINTS (proof strategy divergences):")
            for bp in morse.branch_points[:3]:
                lines.append(f"  ~ {bp[:80]}")

        if homology.has_holes:
            lines.append("")
            lines.append("TOPOLOGICAL HOLES DETECTED:")
            for suggestion in homology.hole_filling_suggestions[:3]:
                lines.append(f"  ! {suggestion}")

        lines.append("")
        lines.append("TACTIC SEQUENCE:")
        lines.append("  " + " → ".join(tactics) if tactics else "  (empty)")

        lines.append("")
        lines.append(
            f"Morse lower bound on proof length: {morse.min_proof_length_bound} step(s)."
        )

        return "\n".join(lines)
