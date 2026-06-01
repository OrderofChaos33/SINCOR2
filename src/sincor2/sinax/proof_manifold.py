#!/usr/bin/env python3
"""
SINAX — Proof Topology Navigator: Layer 1 — Embedding Manifold

Maps Lean/formal proof states onto a learned Riemannian manifold where:
  - Distance  ≈ "proof difficulty" between two states
  - Curvature ≈ "branching complexity" of the local search space

The encoder is a lightweight MLP that projects a feature vector (extracted
from a string proof-state representation) into a d-dimensional latent space.
The metric tensor G(z) is a positive-definite matrix that defines the local
geometry; it defaults to the identity (Euclidean) but can be replaced with a
learned one once training data is available.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class ManifoldPoint:
    """A point on the proof manifold together with its originating state."""
    coordinates: np.ndarray          # shape (d,)
    source_state: str                # original Lean / string proof state
    curvature: float = 0.0           # scalar Ricci-like curvature estimate
    metadata: dict = field(default_factory=dict)

    def distance_to(self, other: "ManifoldPoint") -> float:
        """Euclidean distance in the embedding space (fast approximation)."""
        return float(np.linalg.norm(self.coordinates - other.coordinates))


@dataclass
class ManifoldRegion:
    """A neighbourhood on the manifold described by a centre + radius."""
    centre: ManifoldPoint
    radius: float
    curvature_mean: float = 0.0
    curvature_std: float = 0.0
    points: List[ManifoldPoint] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

def _state_to_features(proof_state: str, dim: int) -> np.ndarray:
    """
    Convert an arbitrary proof-state string into a fixed-length feature vector.

    Uses a deterministic hashing scheme so that:
      - Structurally similar states produce nearby vectors.
      - The mapping is reproducible (no random seed dependency at inference).

    In production this would be replaced by a fine-tuned encoder (e.g. a
    Transformer trained on Lean 4 proof states).
    """
    tokens = proof_state.split()
    vec = np.zeros(dim, dtype=np.float64)

    for i, token in enumerate(tokens):
        digest = hashlib.sha256(token.encode()).digest()
        # Spread token influence over all dimensions
        for j in range(min(len(digest), dim)):
            vec[j % dim] += (digest[j] - 128.0) / 128.0

    # Normalise to unit sphere to avoid magnitude dominating distances
    norm = np.linalg.norm(vec)
    if norm > 1e-9:
        vec /= norm
    return vec


# ---------------------------------------------------------------------------
# Metric tensor
# ---------------------------------------------------------------------------

class MetricTensor:
    """
    Riemannian metric tensor G(z).

    The default implementation is the identity metric (flat / Euclidean).
    A learned metric can be injected by providing a callable
    ``metric_fn(z) -> np.ndarray`` that returns a (d×d) positive-definite
    matrix for each point z.
    """

    def __init__(self, dim: int, metric_fn=None):
        self.dim = dim
        self._metric_fn = metric_fn

    def at(self, z: np.ndarray) -> np.ndarray:
        """Return the metric matrix G at point z. Shape: (d, d)."""
        if self._metric_fn is not None:
            G = np.asarray(self._metric_fn(z), dtype=np.float64)
            assert G.shape == (self.dim, self.dim), "metric_fn must return (d,d) array"
            return G
        return np.eye(self.dim, dtype=np.float64)

    def inner_product(self, z: np.ndarray, u: np.ndarray, v: np.ndarray) -> float:
        """Riemannian inner product <u, v>_G(z)."""
        G = self.at(z)
        return float(u @ G @ v)

    def norm(self, z: np.ndarray, v: np.ndarray) -> float:
        """Riemannian norm ||v||_G(z)."""
        ip = self.inner_product(z, v, v)
        return math.sqrt(max(ip, 0.0))


# ---------------------------------------------------------------------------
# Proof Manifold
# ---------------------------------------------------------------------------

class ProofManifold:
    """
    Embedding Manifold for proof states (PTN Layer 1).

    Parameters
    ----------
    dim : int
        Dimensionality of the embedding space (default 64).
    metric_fn : callable, optional
        If provided, used as the learned Riemannian metric G(z).
    """

    def __init__(self, dim: int = 64, metric_fn=None):
        self.dim = dim
        self.metric = MetricTensor(dim, metric_fn)
        self._embedded: List[ManifoldPoint] = []   # cache of embedded points

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def embed(self, proof_state: str) -> ManifoldPoint:
        """
        Encode a proof state into a ManifoldPoint.

        The curvature estimate is computed from the variance of the
        feature vector — a proxy for how "spread out" the local
        neighbourhood is likely to be.
        """
        coords = _state_to_features(proof_state, self.dim)
        curvature = float(np.var(coords))
        point = ManifoldPoint(
            coordinates=coords,
            source_state=proof_state,
            curvature=curvature,
        )
        self._embedded.append(point)
        return point

    def riemannian_distance(self, p: ManifoldPoint, q: ManifoldPoint) -> float:
        """
        Approximate geodesic distance between two manifold points.

        Uses a midpoint-rule estimate: integrate the metric along the
        straight-line chord p→q sampling the metric at the midpoint.
        """
        midpoint = (p.coordinates + q.coordinates) / 2.0
        diff = q.coordinates - p.coordinates
        g_norm = self.metric.norm(midpoint, diff)
        return g_norm

    def local_curvature(self, centre: ManifoldPoint, neighbours: Sequence[ManifoldPoint]) -> float:
        """
        Estimate scalar curvature at *centre* from its neighbours.

        Uses the angular deficit heuristic: if the neighbourhood is
        "flat" the angles sum to 2π; deviations indicate curvature.
        """
        if len(neighbours) < 2:
            return centre.curvature

        angles = []
        c = centre.coordinates
        for i in range(len(neighbours) - 1):
            u = neighbours[i].coordinates - c
            v = neighbours[i + 1].coordinates - c
            nu, nv = np.linalg.norm(u), np.linalg.norm(v)
            if nu < 1e-9 or nv < 1e-9:
                continue
            cos_a = np.clip(np.dot(u, v) / (nu * nv), -1.0, 1.0)
            angles.append(math.acos(cos_a))

        if not angles:
            return centre.curvature

        angle_sum = sum(angles)
        # Angular deficit from 2π (2D analogy, extended to n-D heuristically)
        curvature = (2 * math.pi - angle_sum) / (2 * math.pi + 1e-9)
        return curvature

    def nearest_neighbours(self, point: ManifoldPoint, k: int = 5) -> List[ManifoldPoint]:
        """Return the k closest cached points to *point* by embedding distance."""
        if not self._embedded:
            return []
        distances = [
            (p.distance_to(point), p)
            for p in self._embedded
            if p is not point
        ]
        distances.sort(key=lambda x: x[0])
        return [p for _, p in distances[:k]]

    def build_region(self, centre_state: str, radius: float) -> ManifoldRegion:
        """
        Create a ManifoldRegion around a proof state.

        Embeds the state, finds neighbours within *radius*, and
        computes regional curvature statistics.
        """
        centre = self.embed(centre_state)
        neighbours = [
            p for p in self._embedded
            if p is not centre and centre.distance_to(p) <= radius
        ]
        curvatures = [p.curvature for p in neighbours] if neighbours else [centre.curvature]
        region = ManifoldRegion(
            centre=centre,
            radius=radius,
            curvature_mean=float(np.mean(curvatures)),
            curvature_std=float(np.std(curvatures)),
            points=neighbours,
        )
        return region

    # ------------------------------------------------------------------
    # Training signal helper
    # ------------------------------------------------------------------

    def curvature_training_signal(self, verified_proof_states: List[str]) -> dict:
        """
        Extract manifold geometry statistics from a set of verified proof states.

        Returns a dictionary of metrics that can feed into the Verified Data
        Flywheel — the manifold curvature data becomes part of the training
        signal so the system learns the shape of mathematical truth.
        """
        points = [self.embed(s) for s in verified_proof_states]
        if not points:
            return {}

        curvatures = [p.curvature for p in points]
        coords = np.stack([p.coordinates for p in points])

        return {
            "num_states": len(points),
            "mean_curvature": float(np.mean(curvatures)),
            "std_curvature": float(np.std(curvatures)),
            "manifold_spread": float(np.std(coords)),
            "centroid": coords.mean(axis=0).tolist(),
        }
