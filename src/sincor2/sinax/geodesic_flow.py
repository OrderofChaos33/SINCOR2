#!/usr/bin/env python3
"""
SINAX — Proof Topology Navigator: Layer 2 — Geodesic Flow Engine

Instead of discrete tactic steps, the Geodesic Flow Engine computes
continuous "proof flows" — smooth paths between proof states.

Key ideas
---------
* A **flow field** f(z, t) acts as a vector field on the manifold,
  directing the trajectory from start_state toward target_state.
* The trajectory is integrated with a simple fixed-step Euler integrator
  (the "neural ODE" analogue) so there are zero heavy library dependencies.
  Replace ``_euler_integrate`` with ``scipy.integrate.solve_ivp`` once
  scipy is available in the deployment environment.
* At each timestep the latent point is decoded back to a tactic suggestion
  via nearest-neighbour lookup in a tactic vocabulary.
* **Shortcut teleportation**: if two regions have very low inter-distance,
  the engine can jump directly rather than traversing the full geodesic.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from sincor2.sinax.proof_manifold import ManifoldPoint, ProofManifold


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class GeodesicPath:
    """A sequence of latent-space waypoints forming a proof geodesic."""
    start_state: str
    target_state: str
    waypoints: List[ManifoldPoint]        # ordered manifold points along the path
    tactics: List[str]                    # decoded tactic at each waypoint
    path_length: float                    # total Riemannian length
    converged: bool = False               # did the flow reach the target?
    teleported: bool = False              # was a shortcut used?
    metadata: dict = field(default_factory=dict)

    @property
    def num_steps(self) -> int:
        return len(self.waypoints)


@dataclass
class FlowConfig:
    """Hyper-parameters for the geodesic integration."""
    max_time: float = 1.0           # integration horizon [0, max_time]
    num_steps: int = 20             # number of Euler steps
    convergence_tol: float = 0.05   # distance threshold to declare convergence
    teleport_threshold: float = 0.10  # jump if inter-point distance < this
    step_size_scale: float = 1.0    # multiply default dt by this factor


# ---------------------------------------------------------------------------
# Default flow field
# ---------------------------------------------------------------------------

def _straight_line_flow(
    z: np.ndarray,
    target: np.ndarray,
    _t: float,          # reserved for learned time-dependent fields
) -> np.ndarray:
    """
    Simplest flow field: a unit vector pointing from z toward target.

    A trained neural ODE would replace this with a learned f_θ(z, t).
    """
    direction = target - z
    norm = np.linalg.norm(direction)
    if norm < 1e-9:
        return np.zeros_like(z)
    return direction / norm


# ---------------------------------------------------------------------------
# Tactic vocabulary + decoder
# ---------------------------------------------------------------------------

# A minimal built-in tactic vocabulary.  Replace / extend with real Lean4
# tactics derived from training data.
_DEFAULT_TACTICS: List[str] = [
    "intro", "apply", "rw", "simp", "exact", "refine",
    "cases", "induction", "ring", "linarith", "norm_num",
    "constructor", "use", "ext", "funext", "congr",
    "contradiction", "omega", "decide", "tauto",
]


class TacticDecoder:
    """
    Maps a latent manifold point back to a Lean tactic string.

    In production this would be a fine-tuned decoder network.
    Here we use a deterministic hash → index mapping so unit tests
    work without any model weights.
    """

    def __init__(self, vocabulary: Optional[List[str]] = None):
        self._vocab = vocabulary or list(_DEFAULT_TACTICS)
        self._embeddings: Dict[str, np.ndarray] = {}

    def register(self, tactic: str, embedding: np.ndarray) -> None:
        """Associate a tactic with a known embedding vector."""
        self._embeddings[tactic] = embedding.copy()

    def decode(self, z: np.ndarray) -> str:
        """Return the tactic whose embedding is nearest to z."""
        if self._embeddings:
            best_tactic = min(
                self._embeddings,
                key=lambda t: float(np.linalg.norm(self._embeddings[t] - z)),
            )
            return best_tactic

        # Fallback: deterministic hash of the rounded coordinate vector
        idx = int(abs(round(float(np.sum(z) * 1000)))) % len(self._vocab)
        return self._vocab[idx]


# ---------------------------------------------------------------------------
# Geodesic Flow Engine
# ---------------------------------------------------------------------------

class GeodesicFlowEngine:
    """
    PTN Layer 2 — Geodesic Flow Engine.

    Parameters
    ----------
    manifold : ProofManifold
        The embedding manifold to navigate.
    flow_fn : callable, optional
        ``flow_fn(z, target, t) -> dz`` vector field.  Defaults to
        the straight-line field.  Inject a trained neural ODE here.
    config : FlowConfig, optional
        Integration hyper-parameters.
    decoder : TacticDecoder, optional
        Maps latent points to tactic strings.
    """

    def __init__(
        self,
        manifold: ProofManifold,
        flow_fn: Optional[Callable] = None,
        config: Optional[FlowConfig] = None,
        decoder: Optional[TacticDecoder] = None,
    ):
        self.manifold = manifold
        self._flow_fn: Callable = flow_fn or _straight_line_flow
        self.config = config or FlowConfig()
        self.decoder = decoder or TacticDecoder()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def geodesic_flow(
        self,
        start_state: str,
        target_state: str,
        max_time: Optional[float] = None,
    ) -> GeodesicPath:
        """
        Compute the geodesic from *start_state* to *target_state*.

        Returns a GeodesicPath with waypoints and decoded tactics.
        """
        cfg = self.config
        t_max = max_time if max_time is not None else cfg.max_time

        z_start = self.manifold.embed(start_state)
        z_target = self.manifold.embed(target_state)

        # Check if a shortcut teleportation is available
        if z_start.distance_to(z_target) < cfg.teleport_threshold:
            return self._teleport(z_start, z_target, start_state, target_state)

        return self._integrate(z_start, z_target, start_state, target_state, t_max)

    def multi_target_flow(
        self,
        start_state: str,
        target_states: List[str],
    ) -> List[GeodesicPath]:
        """Compute geodesics from one start to multiple targets in parallel."""
        return [self.geodesic_flow(start_state, t) for t in target_states]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _integrate(
        self,
        z_start: ManifoldPoint,
        z_target: ManifoldPoint,
        start_state: str,
        target_state: str,
        t_max: float,
    ) -> GeodesicPath:
        """Euler integration of the flow field."""
        cfg = self.config
        dt = (t_max * cfg.step_size_scale) / cfg.num_steps

        z = z_start.coordinates.copy()
        target = z_target.coordinates

        waypoints: List[ManifoldPoint] = [z_start]
        tactics: List[str] = [self.decoder.decode(z)]
        path_length = 0.0
        converged = False

        for step in range(cfg.num_steps):
            t = step * dt
            dz = self._flow_fn(z, target, t)
            z_new = z + dt * dz

            dist_to_target = float(np.linalg.norm(z_new - target))
            path_length += float(np.linalg.norm(z_new - z))
            z = z_new

            wp = ManifoldPoint(
                coordinates=z.copy(),
                source_state=f"step_{step + 1}",
                curvature=float(np.var(z)),
            )
            waypoints.append(wp)
            tactics.append(self.decoder.decode(z))

            if dist_to_target < cfg.convergence_tol:
                converged = True
                break

        # Always include the target as the final waypoint
        waypoints.append(z_target)
        tactics.append(self.decoder.decode(z_target.coordinates))

        return GeodesicPath(
            start_state=start_state,
            target_state=target_state,
            waypoints=waypoints,
            tactics=tactics,
            path_length=path_length,
            converged=converged,
            teleported=False,
        )

    def _teleport(
        self,
        z_start: ManifoldPoint,
        z_target: ManifoldPoint,
        start_state: str,
        target_state: str,
    ) -> GeodesicPath:
        """Direct jump when start and target are very close on the manifold."""
        path_length = z_start.distance_to(z_target)
        return GeodesicPath(
            start_state=start_state,
            target_state=target_state,
            waypoints=[z_start, z_target],
            tactics=[
                self.decoder.decode(z_start.coordinates),
                self.decoder.decode(z_target.coordinates),
            ],
            path_length=path_length,
            converged=True,
            teleported=True,
        )

    # ------------------------------------------------------------------
    # Proof Transfer
    # ------------------------------------------------------------------

    def proof_transfer_score(
        self,
        source_path: GeodesicPath,
        new_target_state: str,
    ) -> float:
        """
        Estimate how much of an existing geodesic can be reused for a new target.

        Returns a score in [0, 1] — higher means more of the existing path
        is transferable (nearby theorems share geodesic segments).
        """
        new_target = self.manifold.embed(new_target_state)
        if not source_path.waypoints:
            return 0.0

        # Find the closest waypoint on the source path to the new target
        min_dist = min(
            wp.distance_to(new_target) for wp in source_path.waypoints
        )
        # Normalise by path length (longer paths → more transfer opportunity)
        score = math.exp(-min_dist / (source_path.path_length + 1e-9))
        return float(np.clip(score, 0.0, 1.0))
