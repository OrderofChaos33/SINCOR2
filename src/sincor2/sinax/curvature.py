"""
SINAX — Phase 5: Curvature Analyzer
=====================================
Measures local "curvature" of proof space to guide the geometric search
engine and surface complexity hotspots.

Metrics (per graph region / node neighbourhood)
------------------------------------------------
branching_factor:
    Average number of outgoing (verified) edges.
failure_density:
    Fraction of edge attempts that resulted in ``VerificationResult.FAILED``.
proof_depth:
    Mean depth of nodes in the neighbourhood.
success_frequency:
    Fraction of edge attempts that resulted in ``VerificationResult.VERIFIED``.

Curvature score
---------------
A composite scalar in [0, 1] derived from the above metrics.  High curvature
means high failure density or high branching — the search engine should spend
more exploration budget here.  Low curvature means the region is well-trodden
and reliable.

Public API
----------
    RegionCurvature     — value object for curvature metrics
    CurvatureAnalyzer
    get_curvature_analyzer()  — singleton accessor
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from . import config as cfg
from .graph_store import (
    ProofStateNode,
    ProofGraphStore,
    TacticEdge,
    VerificationResult,
    get_store,
)

logger = logging.getLogger("sincor.sinax.curvature")

# ---------------------------------------------------------------------------
# Value object
# ---------------------------------------------------------------------------


@dataclass
class RegionCurvature:
    """Curvature metrics for a graph region centred on a focal node.

    Attributes
    ----------
    focal_node_id:
        The node whose neighbourhood was analysed.
    radius:
        BFS radius of the neighbourhood.
    branching_factor:
        Average verified out-degree.
    failure_density:
        Fraction of edges that ended in ``FAILED``.
    proof_depth:
        Mean depth of nodes in the neighbourhood.
    success_frequency:
        Fraction of edges that are ``VERIFIED``.
    curvature_score:
        Composite score in [0, 1]; higher means more complex / risky region.
    node_count:
        Number of nodes in the analysed neighbourhood.
    observations:
        Total number of edge observations used.
    """

    focal_node_id: str
    radius: int
    branching_factor: float
    failure_density: float
    proof_depth: float
    success_frequency: float
    curvature_score: float
    node_count: int
    observations: int

    def to_dict(self) -> dict:
        return {
            "focal_node_id": self.focal_node_id,
            "radius": self.radius,
            "branching_factor": self.branching_factor,
            "failure_density": self.failure_density,
            "proof_depth": self.proof_depth,
            "success_frequency": self.success_frequency,
            "curvature_score": self.curvature_score,
            "node_count": self.node_count,
            "observations": self.observations,
        }


# ---------------------------------------------------------------------------
# Curvature Analyzer
# ---------------------------------------------------------------------------


class CurvatureAnalyzer:
    """Compute curvature metrics for regions of the proof graph.

    Usage
    -----
    ::

        analyzer = get_curvature_analyzer()
        curvature = analyzer.compute(node_id, radius=2)
        print(curvature.curvature_score)  # 0.0 – 1.0
    """

    def __init__(self, store: Optional[ProofGraphStore] = None) -> None:
        self._store = store or get_store()
        self._cache: Dict[Tuple[str, int], RegionCurvature] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def compute(
        self,
        focal_node_id: str,
        radius: int = 2,
        use_cache: bool = True,
    ) -> Optional[RegionCurvature]:
        """Compute curvature for the neighbourhood of ``focal_node_id``.

        Parameters
        ----------
        focal_node_id:
            Centre of the neighbourhood.
        radius:
            BFS depth to collect neighbourhood nodes.
        use_cache:
            When ``True``, return a cached result if one exists.

        Returns
        -------
        ``RegionCurvature`` or ``None`` if insufficient observations.
        """
        key = (focal_node_id, radius)
        if use_cache:
            with self._lock:
                if key in self._cache:
                    return self._cache[key]

        neighbourhood = self._store.bfs(focal_node_id, max_depth=radius)
        if not neighbourhood:
            return None

        # Collect all edges in the neighbourhood
        all_edges: List[TacticEdge] = []
        for node in neighbourhood:
            for _target, edge in self._store.successors(node.node_id):
                all_edges.append(edge)
            # Also include incoming edges whose source is in neighbourhood
            src_ids = {n.node_id for n in neighbourhood}
            for src, edge in self._store.predecessors(node.node_id):
                if src.node_id in src_ids:
                    all_edges.append(edge)

        # Deduplicate edges
        seen_eids: set = set()
        unique_edges: List[TacticEdge] = []
        for e in all_edges:
            if e.edge_id not in seen_eids:
                seen_eids.add(e.edge_id)
                unique_edges.append(e)

        n_obs = len(unique_edges)
        if n_obs < cfg.CURVATURE_MIN_OBS:
            logger.debug(
                "Node %s: only %d observations < min %d; skipping curvature",
                focal_node_id,
                n_obs,
                cfg.CURVATURE_MIN_OBS,
            )
            return None

        n_verified = sum(1 for e in unique_edges if e.verification_result == VerificationResult.VERIFIED)
        n_failed = sum(1 for e in unique_edges if e.verification_result == VerificationResult.FAILED)

        # Branching factor = avg verified out-degree
        out_degrees = [
            len([e for e in self._store.successors(n.node_id) if e[1].verified])
            for n in neighbourhood
        ]
        branching_factor = sum(out_degrees) / len(out_degrees) if out_degrees else 0.0

        failure_density = n_failed / n_obs if n_obs > 0 else 0.0
        success_frequency = n_verified / n_obs if n_obs > 0 else 0.0
        mean_depth = (
            sum(n.depth for n in neighbourhood) / len(neighbourhood)
            if neighbourhood
            else 0.0
        )

        curvature_score = self._composite_score(
            branching_factor, failure_density, mean_depth, success_frequency
        )

        result = RegionCurvature(
            focal_node_id=focal_node_id,
            radius=radius,
            branching_factor=branching_factor,
            failure_density=failure_density,
            proof_depth=mean_depth,
            success_frequency=success_frequency,
            curvature_score=curvature_score,
            node_count=len(neighbourhood),
            observations=n_obs,
        )

        if use_cache:
            with self._lock:
                self._cache[key] = result

        return result

    def top_curvature_nodes(
        self,
        top_k: int = 10,
        radius: int = 2,
    ) -> List[Tuple[str, float]]:
        """Return the top-k nodes with highest curvature scores.

        Useful for surfacing hotspots / bottlenecks in the proof graph.
        """
        scores: List[Tuple[str, float]] = []
        for node_id in list(self._store._nodes.keys()):
            rc = self.compute(node_id, radius=radius, use_cache=True)
            if rc is not None:
                scores.append((node_id, rc.curvature_score))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    def invalidate_cache(self) -> None:
        with self._lock:
            self._cache.clear()

    # ------------------------------------------------------------------
    # Composite scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _composite_score(
        branching_factor: float,
        failure_density: float,
        mean_depth: float,
        success_frequency: float,
    ) -> float:
        """Map metrics to a [0, 1] curvature score.

        High failure density and high branching → high curvature.
        High success frequency → lower curvature.
        Mean depth is log-scaled and contributes a modest bonus.
        """
        # Normalise branching factor with soft cap at 10
        bf_norm = min(branching_factor / 10.0, 1.0)
        # Depth contribution (log-scaled, soft cap at depth 20)
        depth_contribution = math.log1p(min(mean_depth, 20.0)) / math.log1p(20.0)
        # Weighted combination
        raw = (
            0.50 * failure_density
            + 0.30 * bf_norm
            + 0.10 * depth_contribution
            + 0.10 * (1.0 - success_frequency)
        )
        return max(0.0, min(1.0, raw))


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_analyzer_instance: Optional[CurvatureAnalyzer] = None
_analyzer_lock = threading.Lock()


def get_curvature_analyzer() -> CurvatureAnalyzer:
    """Return the process-global ``CurvatureAnalyzer`` singleton."""
    global _analyzer_instance
    if _analyzer_instance is None:
        with _analyzer_lock:
            if _analyzer_instance is None:
                _analyzer_instance = CurvatureAnalyzer()
    return _analyzer_instance
