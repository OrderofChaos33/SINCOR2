#!/usr/bin/env python3
"""
SINAX — Proof Topology Navigator: Layer 3 — Homology Detector

Identifies "holes" in the proof space — dead ends that humans (and agents)
consistently fall into — and suggests conjectures that "fill" those holes.

Topological approach
--------------------
We use a lightweight *persistent homology* approximation based on the
Vietoris-Rips filtration over a finite set of embedded proof states.

At filtration radius ε:
  H0 — connected components  (isolated clusters of proof attempts)
  H1 — loops / 1-cycles       (cyclic dependencies / circular reasoning)
  H2 — voids / 2-cycles       (gaps requiring "bridging lemmas")

The "barcode" (birth, death) pairs indicate how topologically significant
each hole is.  Long-lived features (large |death − birth|) correspond to
structural gaps that are hard to close by local tactics alone.

Implementation note
-------------------
A full Ripser / GUDHI implementation would give exact persistent homology.
Here we use a graph-theoretic approximation that works without C extensions:
  - Build Vietoris-Rips graph at each radius step.
  - Track connected-component merges  (H0 barcodes).
  - Detect 1-cycles via cycle rank = |E| − |V| + |components|  (H1 proxy).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import numpy as np

from sincor2.sinax.proof_manifold import ManifoldPoint, ProofManifold

# Maximum characters used when truncating proof-state strings to labels
MAX_STATE_LABEL_LEN = 80


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class HomologyClass:
    """A topological feature (hole) in the proof space."""
    dimension: int          # 0 = component, 1 = loop, 2 = void
    birth_radius: float     # filtration radius at which the feature appears
    death_radius: float     # filtration radius at which it is filled
    representative_states: List[str]  # proof states near this hole
    suggested_lemma: str = ""         # conjecture that could fill the hole

    @property
    def persistence(self) -> float:
        """How long-lived is this topological feature (larger = more significant)."""
        if self.death_radius == math.inf:
            return math.inf
        return self.death_radius - self.birth_radius

    @property
    def is_infinite(self) -> bool:
        return self.death_radius == math.inf


@dataclass
class HomologyReport:
    """Full topological analysis of a set of proof attempts."""
    num_states: int
    filtration_radii: List[float]
    classes: List[HomologyClass]
    betti_numbers: Dict[int, int]          # dimension → count of infinite classes
    hole_filling_suggestions: List[str]    # ranked list of suggested lemmas

    @property
    def has_holes(self) -> bool:
        return any(hc.dimension >= 1 for hc in self.classes)

    @property
    def num_components(self) -> int:
        return self.betti_numbers.get(0, 1)


# ---------------------------------------------------------------------------
# Union-Find for connected-component tracking
# ---------------------------------------------------------------------------

class _UnionFind:
    def __init__(self, n: int):
        self._parent = list(range(n))
        self._rank = [0] * n
        self.num_components = n

    def find(self, x: int) -> int:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, x: int, y: int) -> bool:
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return False
        if self._rank[rx] < self._rank[ry]:
            rx, ry = ry, rx
        self._parent[ry] = rx
        if self._rank[rx] == self._rank[ry]:
            self._rank[rx] += 1
        self.num_components -= 1
        return True


# ---------------------------------------------------------------------------
# Homology Detector
# ---------------------------------------------------------------------------

class HomologyDetector:
    """
    PTN Layer 3 — Homology Detector.

    Parameters
    ----------
    manifold : ProofManifold
        The embedding manifold that supplies pairwise distances.
    num_radii : int
        Number of filtration steps (finer = more accurate but slower).
    max_radius : float
        Upper bound on the filtration radius.  Set to ~2× the expected
        diameter of the proof-state cloud.
    """

    def __init__(
        self,
        manifold: ProofManifold,
        num_radii: int = 30,
        max_radius: float = 2.0,
    ):
        self.manifold = manifold
        self.num_radii = num_radii
        self.max_radius = max_radius

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyse(self, proof_states: List[str]) -> HomologyReport:
        """
        Compute the persistent homology of a collection of proof states.

        Parameters
        ----------
        proof_states : list[str]
            String representations of proof states to analyse (e.g. Lean
            goal strings, tactic state dumps, or informal descriptions).

        Returns
        -------
        HomologyReport
            Betti numbers, barcode pairs, and hole-filling suggestions.
        """
        if not proof_states:
            return HomologyReport(
                num_states=0,
                filtration_radii=[],
                classes=[],
                betti_numbers={0: 0},
                hole_filling_suggestions=[],
            )

        points = [self.manifold.embed(s) for s in proof_states]
        n = len(points)
        dist_matrix = self._pairwise_distances(points)

        radii = np.linspace(0.0, self.max_radius, self.num_radii).tolist()
        classes: List[HomologyClass] = []

        # --- H0 (connected components) via Union-Find ---
        h0_classes = self._compute_h0(points, proof_states, dist_matrix, radii)
        classes.extend(h0_classes)

        # --- H1 (loops) via cycle rank ---
        h1_classes = self._compute_h1(points, proof_states, dist_matrix, radii)
        classes.extend(h1_classes)

        # Betti numbers = number of infinite persistence classes per dimension
        betti: Dict[int, int] = {}
        for hc in classes:
            if hc.is_infinite:
                betti[hc.dimension] = betti.get(hc.dimension, 0) + 1

        suggestions = self._suggest_lemmas(classes, proof_states)

        return HomologyReport(
            num_states=n,
            filtration_radii=radii,
            classes=classes,
            betti_numbers=betti,
            hole_filling_suggestions=suggestions,
        )

    def detect_dead_ends(
        self,
        failed_states: List[str],
        radius: float = 0.3,
    ) -> List[ManifoldPoint]:
        """
        Find clusters of failed proof attempts — "dead end" regions.

        Returns the centroid of each cluster as a ManifoldPoint.
        """
        if not failed_states:
            return []

        points = [self.manifold.embed(s) for s in failed_states]
        clusters = self._cluster(points, radius)

        centroids = []
        for cluster in clusters:
            coords = np.mean([p.coordinates for p in cluster], axis=0)
            centroid = ManifoldPoint(
                coordinates=coords,
                source_state=f"dead_end_cluster_size_{len(cluster)}",
                curvature=float(np.mean([p.curvature for p in cluster])),
            )
            centroids.append(centroid)
        return centroids

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _pairwise_distances(self, points: List[ManifoldPoint]) -> np.ndarray:
        n = len(points)
        D = np.zeros((n, n), dtype=np.float64)
        for i in range(n):
            for j in range(i + 1, n):
                d = points[i].distance_to(points[j])
                D[i, j] = D[j, i] = d
        return D

    def _compute_h0(
        self,
        points: List[ManifoldPoint],
        states: List[str],
        D: np.ndarray,
        radii: List[float],
    ) -> List[HomologyClass]:
        """Track connected-component births and deaths (H0 barcode)."""
        n = len(points)
        uf = _UnionFind(n)
        birth_radius: Dict[int, float] = {i: 0.0 for i in range(n)}  # component representative → birth
        component_states: Dict[int, Set[int]] = {i: {i} for i in range(n)}
        classes: List[HomologyClass] = []

        # Sort all edges by distance
        edges: List[Tuple[float, int, int]] = []
        for i in range(n):
            for j in range(i + 1, n):
                edges.append((D[i, j], i, j))
        edges.sort()

        for dist, i, j in edges:
            ri, rj = uf.find(i), uf.find(j)
            if ri != rj:
                # Merge: the younger component (higher birth) dies
                b_i = birth_radius[ri]
                b_j = birth_radius[rj]
                if b_i >= b_j:
                    dying_root, surviving_root = ri, rj
                else:
                    dying_root, surviving_root = rj, ri

                rep_states = [states[k] for k in list(component_states[dying_root])[:3]]
                classes.append(HomologyClass(
                    dimension=0,
                    birth_radius=birth_radius[dying_root],
                    death_radius=dist,
                    representative_states=rep_states,
                ))
                # Merge component state sets
                component_states[surviving_root] = (
                    component_states[surviving_root] | component_states[dying_root]
                )
                birth_radius[surviving_root] = min(b_i, b_j)
                uf.union(i, j)

        # Surviving (infinite) components
        seen_roots: Set[int] = set()
        for i in range(n):
            r = uf.find(i)
            if r not in seen_roots:
                seen_roots.add(r)
                rep_states = [states[k] for k in list(component_states.get(r, {r}))[:3]]
                classes.append(HomologyClass(
                    dimension=0,
                    birth_radius=birth_radius.get(r, 0.0),
                    death_radius=math.inf,
                    representative_states=rep_states,
                ))

        return classes

    def _compute_h1(
        self,
        points: List[ManifoldPoint],
        states: List[str],
        D: np.ndarray,
        radii: List[float],
    ) -> List[HomologyClass]:
        """Approximate H1 (loops) via cycle rank at successive radii."""
        n = len(points)
        classes: List[HomologyClass] = []
        prev_cycle_rank = 0

        for idx, eps in enumerate(radii):
            # Build adjacency at this radius
            edges_at_eps = [
                (i, j)
                for i in range(n)
                for j in range(i + 1, n)
                if D[i, j] <= eps
            ]
            # Cycle rank = |E| - |V| + num_components
            uf = _UnionFind(n)
            for (i, j) in edges_at_eps:
                uf.union(i, j)
            cycle_rank = len(edges_at_eps) - n + uf.num_components

            new_loops = max(0, cycle_rank - prev_cycle_rank)
            if new_loops > 0:
                # Pick representative states near the loop
                rep_idx = np.argsort(D.sum(axis=1))[:3].tolist()
                rep_states = [states[k] for k in rep_idx]
                # These loops persist until the next step or forever
                death = radii[idx + 1] if idx + 1 < len(radii) else math.inf
                for _ in range(new_loops):
                    classes.append(HomologyClass(
                        dimension=1,
                        birth_radius=eps,
                        death_radius=death,
                        representative_states=rep_states,
                    ))
            prev_cycle_rank = cycle_rank

        return classes

    def _cluster(
        self, points: List[ManifoldPoint], radius: float
    ) -> List[List[ManifoldPoint]]:
        """Single-linkage clustering at a given radius."""
        n = len(points)
        uf = _UnionFind(n)
        for i in range(n):
            for j in range(i + 1, n):
                if points[i].distance_to(points[j]) <= radius:
                    uf.union(i, j)

        cluster_map: Dict[int, List[ManifoldPoint]] = {}
        for i in range(n):
            root = uf.find(i)
            cluster_map.setdefault(root, []).append(points[i])
        return list(cluster_map.values())

    def _suggest_lemmas(
        self,
        classes: List[HomologyClass],
        proof_states: List[str],
    ) -> List[str]:
        """
        Generate lemma suggestions that could fill detected topological holes.

        For now this produces templated suggestions from the representative
        states near each persistent hole.  In production, this would feed
        into the Conjecturer module which uses an LLM to propose actual Lean
        lemma statements.
        """
        suggestions: List[str] = []
        significant = sorted(
            [hc for hc in classes if hc.dimension >= 1 and hc.persistence > 0.1],
            key=lambda hc: hc.persistence,
            reverse=True,
        )
        for hc in significant[:5]:  # top 5 most significant holes
            if hc.representative_states:
                anchor = hc.representative_states[0][:MAX_STATE_LABEL_LEN].replace("\n", " ")
                dim_label = {1: "bridge lemma", 2: "existence lemma"}.get(
                    hc.dimension, "auxiliary lemma"
                )
                suggestions.append(
                    f"[H{hc.dimension} hole, persistence={hc.persistence:.3f}] "
                    f"Propose {dim_label} near: '{anchor}'"
                )
        return suggestions
