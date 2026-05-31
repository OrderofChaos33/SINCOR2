#!/usr/bin/env python3
"""
SINAX — Proof Topology Navigator: Layer 4 — Morse Theory Filter

Applies Morse-theoretic ideas to the proof manifold:

* **Critical points** (local minima of a "proof complexity" function) are
  lemmas that dramatically simplify multiple proof paths.
* **Saddle points** are choice points where multiple proof strategies diverge.
* The **index theorem** gives a lower bound on the number of tactic steps
  required to complete a proof (related to the Morse index of the problem).

How it works
------------
1. Define a Morse function h : ManifoldPoint → ℝ.  We use the embedding
   norm as a proxy for "distance from trivial / already-proven" states;
   lower norm ≈ simpler state.
2. Approximate the gradient of h from the pairwise distance matrix.
3. Classify each embedded state as local minimum, saddle, or local maximum
   based on its gradient and neighbourhood topology.
4. Return the minimal-Morse-index path (the critical points that form the
   "backbone" of any shortest proof).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

import numpy as np

from sincor2.sinax.proof_manifold import ManifoldPoint, ProofManifold


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

class CriticalPointType(Enum):
    MINIMUM = "minimum"    # key lemma — simplifies everything below it
    SADDLE = "saddle"      # branching point — strategies diverge here
    MAXIMUM = "maximum"    # "hardest" state — usually the original goal


@dataclass
class CriticalPoint:
    """A critical point of the Morse function on the proof manifold."""
    point: ManifoldPoint
    point_type: CriticalPointType
    morse_index: int          # number of negative-eigenvalue directions
    complexity_value: float   # value of the Morse function h(z)
    influence_radius: float   # how much of the manifold this cp affects
    simplification_power: float   # 0-1 estimate of how much it reduces proof work
    label: str = ""           # human-readable description

    @property
    def is_key_lemma(self) -> bool:
        return self.point_type == CriticalPointType.MINIMUM

    @property
    def is_branch_point(self) -> bool:
        return self.point_type == CriticalPointType.SADDLE


@dataclass
class MorseDecomposition:
    """Complete Morse-theoretic decomposition of a proof problem."""
    critical_points: List[CriticalPoint]
    morse_complex: List[Tuple[CriticalPoint, CriticalPoint]]  # gradient-flow edges
    min_proof_length_bound: int       # Morse inequality lower bound
    key_lemmas: List[str]             # states that are critical minima
    branch_points: List[str]          # states that are saddles
    complexity_landscape: Dict[str, float]   # state → Morse function value


# ---------------------------------------------------------------------------
# Morse function & gradient
# ---------------------------------------------------------------------------

def _morse_function(point: ManifoldPoint) -> float:
    """
    Scalar Morse function h : manifold → ℝ.

    Uses the L2-norm of the embedding coordinates as a proxy for
    "proof complexity" — states closer to the origin of the embedding
    space are simpler / closer to being proven.

    A learned version would use a neural network trained on verified
    proof difficulties.
    """
    return float(np.linalg.norm(point.coordinates))


def _discrete_gradient(
    point: ManifoldPoint,
    neighbours: List[ManifoldPoint],
    h_fn=_morse_function,
) -> np.ndarray:
    """Approximate gradient of h at *point* from its neighbourhood."""
    if not neighbours:
        return np.zeros_like(point.coordinates)

    h0 = h_fn(point)
    grad = np.zeros_like(point.coordinates, dtype=np.float64)
    for nb in neighbours:
        diff = nb.coordinates - point.coordinates
        dist = np.linalg.norm(diff)
        if dist < 1e-9:
            continue
        dh = h_fn(nb) - h0
        grad += (dh / (dist ** 2)) * diff

    return grad


def _hessian_eigenvalues(
    point: ManifoldPoint,
    neighbours: List[ManifoldPoint],
    h_fn=_morse_function,
) -> np.ndarray:
    """
    Approximate Hessian eigenvalues at *point*.

    Uses finite differences from the neighbour list.
    The Morse index = number of negative eigenvalues.
    """
    if len(neighbours) < 2:
        return np.array([0.0])

    h0 = h_fn(point)
    second_diffs = []
    for nb in neighbours:
        dist = point.distance_to(nb)
        if dist < 1e-9:
            continue
        dh = h_fn(nb) - h0
        # Discrete second derivative along the direction to this neighbour
        second_diffs.append(2.0 * dh / (dist ** 2))

    if not second_diffs:
        return np.array([0.0])
    return np.array(second_diffs)


def _classify_critical_point(
    eigenvalues: np.ndarray,
    gradient_norm: float,
    gradient_threshold: float = 0.05,
) -> Tuple[CriticalPointType, int]:
    """
    Classify a point based on gradient norm and Hessian eigenvalue signs.

    Returns (CriticalPointType, morse_index).
    """
    if gradient_norm > gradient_threshold:
        # Not critical — not used in final decomposition, but classify anyway
        num_negative = int(np.sum(eigenvalues < 0))
        return CriticalPointType.SADDLE, num_negative

    num_negative = int(np.sum(eigenvalues < -1e-6))
    num_positive = int(np.sum(eigenvalues > 1e-6))

    if num_negative == 0:
        return CriticalPointType.MINIMUM, 0
    elif num_positive == 0:
        return CriticalPointType.MAXIMUM, len(eigenvalues)
    else:
        return CriticalPointType.SADDLE, num_negative


# ---------------------------------------------------------------------------
# Morse Theory Filter
# ---------------------------------------------------------------------------

class MorseFilter:
    """
    PTN Layer 4 — Morse Theory Filter.

    Parameters
    ----------
    manifold : ProofManifold
        The embedding manifold.
    morse_fn : callable, optional
        Scalar function h : ManifoldPoint → float.  Defaults to L2-norm.
    neighbour_k : int
        Number of nearest neighbours to use for gradient estimation.
    gradient_threshold : float
        Points with gradient norm below this are considered critical.
    """

    def __init__(
        self,
        manifold: ProofManifold,
        morse_fn=None,
        neighbour_k: int = 6,
        gradient_threshold: float = 0.05,
    ):
        self.manifold = manifold
        self._h = morse_fn or _morse_function
        self.neighbour_k = neighbour_k
        self.gradient_threshold = gradient_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def decompose(self, proof_states: List[str]) -> MorseDecomposition:
        """
        Perform a Morse decomposition of the given proof states.

        Parameters
        ----------
        proof_states : list[str]
            The collection of states to analyse (start, intermediate, target).

        Returns
        -------
        MorseDecomposition
        """
        if not proof_states:
            return MorseDecomposition(
                critical_points=[],
                morse_complex=[],
                min_proof_length_bound=0,
                key_lemmas=[],
                branch_points=[],
                complexity_landscape={},
            )

        points = [self.manifold.embed(s) for s in proof_states]
        critical_points: List[CriticalPoint] = []
        landscape: Dict[str, float] = {}

        for idx, (state, pt) in enumerate(zip(proof_states, points)):
            h_val = self._h(pt)
            landscape[state[:80]] = h_val

            neighbours = self.manifold.nearest_neighbours(pt, k=self.neighbour_k)
            grad = _discrete_gradient(pt, neighbours, self._h)
            grad_norm = float(np.linalg.norm(grad))
            eigenvalues = _hessian_eigenvalues(pt, neighbours, self._h)

            cp_type, morse_idx = _classify_critical_point(
                eigenvalues, grad_norm, self.gradient_threshold
            )

            simplification = self._simplification_power(pt, points, h_val)
            influence = self._influence_radius(pt, points)

            cp = CriticalPoint(
                point=pt,
                point_type=cp_type,
                morse_index=morse_idx,
                complexity_value=h_val,
                influence_radius=influence,
                simplification_power=simplification,
                label=state[:80],
            )
            critical_points.append(cp)

        # Sort: minima first (key lemmas), then saddles, then maxima
        critical_points.sort(key=lambda cp: (cp.point_type.value, cp.complexity_value))

        # Morse complex: edges from each saddle down to the closest minimum
        morse_complex = self._build_morse_complex(critical_points)

        # Morse inequality: # proof steps ≥ # critical points of even index
        # (simplified discrete version)
        min_length = max(
            1,
            sum(1 for cp in critical_points if cp.morse_index % 2 == 0),
        )

        key_lemmas = [
            cp.label for cp in critical_points if cp.is_key_lemma
        ]
        branch_points = [
            cp.label for cp in critical_points if cp.is_branch_point
        ]

        return MorseDecomposition(
            critical_points=critical_points,
            morse_complex=morse_complex,
            min_proof_length_bound=min_length,
            key_lemmas=key_lemmas,
            branch_points=branch_points,
            complexity_landscape=landscape,
        )

    def filter_path(
        self,
        waypoints: List[ManifoldPoint],
        decomposition: MorseDecomposition,
    ) -> List[ManifoldPoint]:
        """
        Filter a geodesic path to retain only the critical-point waypoints.

        This prunes redundant intermediate steps, keeping only the points
        that correspond to key lemmas or branch points.
        """
        if not waypoints or not decomposition.critical_points:
            return waypoints

        cp_coords = np.stack([cp.point.coordinates for cp in decomposition.critical_points])
        filtered: List[ManifoldPoint] = [waypoints[0]]  # always keep start

        for wp in waypoints[1:-1]:
            dists = np.linalg.norm(cp_coords - wp.coordinates, axis=1)
            nearest_cp = decomposition.critical_points[int(np.argmin(dists))]
            # Keep waypoint if it is close to a critical point
            if float(np.min(dists)) < nearest_cp.influence_radius:
                filtered.append(wp)

        filtered.append(waypoints[-1])  # always keep end
        return filtered

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _simplification_power(
        self,
        point: ManifoldPoint,
        all_points: List[ManifoldPoint],
        h_val: float,
    ) -> float:
        """
        Estimate how much applying the tactic at *point* simplifies future work.

        Heuristic: fraction of remaining states whose Morse value exceeds
        this point's value (i.e., states that become "simpler" after this step).
        """
        if not all_points:
            return 0.0
        harder = sum(1 for p in all_points if self._h(p) > h_val)
        return harder / len(all_points)

    def _influence_radius(
        self,
        point: ManifoldPoint,
        all_points: List[ManifoldPoint],
    ) -> float:
        """Radius within which this critical point affects the Morse flow."""
        if len(all_points) < 2:
            return 0.1
        dists = [p.distance_to(point) for p in all_points if p is not point]
        if not dists:
            return 0.1
        return float(np.percentile(dists, 25))  # inner quartile radius

    def _build_morse_complex(
        self, critical_points: List[CriticalPoint]
    ) -> List[Tuple[CriticalPoint, CriticalPoint]]:
        """
        Build the Morse complex: gradient-flow edges from saddles to minima.
        """
        minima = [cp for cp in critical_points if cp.is_key_lemma]
        saddles = [cp for cp in critical_points if cp.is_branch_point]

        if not minima:
            return []

        complex_edges: List[Tuple[CriticalPoint, CriticalPoint]] = []
        for saddle in saddles:
            # Connect each saddle to its nearest minimum
            nearest_min = min(
                minima,
                key=lambda m: saddle.point.distance_to(m.point),
            )
            complex_edges.append((saddle, nearest_min))
        return complex_edges
