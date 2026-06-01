"""
SINAX — Phase 4: Geometric Search Engine
==========================================
Navigates proof space using latent trajectories rather than only tactic trees.

Algorithms
----------
* **Embedding similarity search** — retrieve the k nodes nearest to the
  current proof state and harvest their proven outgoing tactics.
* **Target-region prediction** — estimate the embedding of the goal
  state and use it to bias the beam.
* **Beam search over latent space** — maintain a beam of candidate nodes
  sorted by a score that blends verifier confidence, embedding similarity
  to the target, and curvature-weighted exploration.
* **Hard fallback** — if geometric search finds nothing above
  ``cfg.FALLBACK_SIMILARITY_THRESHOLD``, the engine returns ``None`` so
  that AxiomSolver falls back to its native tactic search.

Public API
----------
    SearchCandidate     — a proposed tactic with its predicted outcome node
    GeometricSearchEngine
    get_search_engine() — singleton accessor
"""

from __future__ import annotations

import heapq
import logging
import math
import time
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from . import config as cfg
from .encoder import Embedding, ProofState, get_encoder
from .graph_store import ProofStateNode, ProofGraphStore, VerificationResult, get_store
from .retrieval import RetrievalService, get_retrieval_service

logger = logging.getLogger("sincor.sinax.search")

# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(order=True)
class SearchCandidate:
    """A tactic candidate produced by geometric search.

    Attributes
    ----------
    tactic:
        Suggested tactic string to apply.
    score:
        Composite score (higher = more promising).
    predicted_node:
        A predicted ``ProofStateNode`` for the post-tactic state (may be
        a prototype, not yet verified).
    source_node_id:
        The node this candidate was generated from.
    similarity_to_target:
        Cosine similarity to the predicted goal embedding.
    provenance:
        ``"geometric"`` or ``"tactic_tree"`` (set when originating from
        the AxiomSolver fallback).
    """

    # order=True: heapq uses score; negate for max-heap
    score: float = field(compare=True)
    tactic: str = field(compare=False)
    predicted_node: Optional[ProofStateNode] = field(default=None, compare=False)
    source_node_id: str = field(default="", compare=False)
    similarity_to_target: float = field(default=0.0, compare=False)
    provenance: str = field(default="geometric", compare=False)


# ---------------------------------------------------------------------------
# Geometric Search Engine
# ---------------------------------------------------------------------------


class GeometricSearchEngine:
    """Navigate proof space via latent-space beam search.

    Parameters
    ----------
    store:
        The ``ProofGraphStore`` to draw historical trajectories from.
    retrieval:
        The ``RetrievalService`` for k-NN lookups.
    exploration_weight:
        Blend factor between exploitation (high similarity to target) and
        exploration (low curvature / novel regions).  Range [0, 1].
    beam_width:
        Number of candidates to maintain per beam step.
    fallback_threshold:
        Minimum similarity score required to trust geometric guidance.
        Below this, return ``None`` so the caller falls back to tactic search.
    """

    def __init__(
        self,
        store: Optional[ProofGraphStore] = None,
        retrieval: Optional[RetrievalService] = None,
        exploration_weight: Optional[float] = None,
        beam_width: Optional[int] = None,
        fallback_threshold: Optional[float] = None,
    ) -> None:
        self._store = store or get_store()
        self._retrieval = retrieval or get_retrieval_service()
        self._exploration_weight = (
            exploration_weight
            if exploration_weight is not None
            else cfg.EXPLORATION_WEIGHT
        )
        self._beam_width = beam_width or cfg.BEAM_WIDTH
        self._fallback_threshold = (
            fallback_threshold
            if fallback_threshold is not None
            else cfg.FALLBACK_SIMILARITY_THRESHOLD
        )
        self._encoder = get_encoder()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def search(
        self,
        current_state: ProofState,
        goal_state: Optional[ProofState] = None,
        max_steps: int = 5,
    ) -> Optional[List[SearchCandidate]]:
        """Run geometric beam search from ``current_state``.

        Parameters
        ----------
        current_state:
            The proof state to navigate from.
        goal_state:
            An optional target proof state.  When provided its embedding
            is used to bias the beam toward the goal region.
        max_steps:
            Maximum beam-search steps.

        Returns
        -------
        Ranked list of ``SearchCandidate`` objects, or ``None`` if the
        best available similarity is below ``fallback_threshold`` (signal
        to fall back to AxiomSolver's native tactic search).
        """
        current_emb = self._encoder.encode(current_state)
        goal_emb = self._encoder.encode(goal_state) if goal_state else None

        candidates = self._beam_search(current_emb, goal_emb, max_steps)
        if not candidates:
            logger.debug("Geometric search: no candidates — falling back")
            return None

        best_sim = candidates[0].similarity_to_target
        if best_sim < self._fallback_threshold:
            logger.debug(
                "Geometric search: best similarity %.3f below threshold %.3f "
                "— falling back",
                best_sim,
                self._fallback_threshold,
            )
            return None

        logger.debug(
            "Geometric search: returning %d candidates (best_sim=%.3f)",
            len(candidates),
            best_sim,
        )
        return candidates

    def generate_tactics(
        self,
        current_state: ProofState,
        goal_state: Optional[ProofState] = None,
        top_k: int = 5,
    ) -> List[str]:
        """Return a ranked list of tactic strings (for direct use by the prover).

        This is a convenience wrapper around ``search``.  Returns an empty
        list when geometric search falls back.
        """
        results = self.search(current_state, goal_state)
        if results is None:
            return []
        return [c.tactic for c in results[:top_k] if c.tactic]

    def predict_target_region(self, goal_state: ProofState) -> Embedding:
        """Encode the goal state to obtain its target-region embedding."""
        return self._encoder.encode(goal_state)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _beam_search(
        self,
        current_emb: Embedding,
        goal_emb: Optional[Embedding],
        max_steps: int,
    ) -> List[SearchCandidate]:
        """Core beam search over the latent proof space.

        Each step:
        1. Retrieve nearest neighbours of the current embedding.
        2. Harvest the outgoing tactics of those neighbours.
        3. Score each tactic by a blend of target similarity and
           exploration weight.
        4. Keep the top-``beam_width`` candidates and advance.
        """
        # Initialise beam: (score_negated, candidate)  — min-heap as max-heap
        beam: List[Tuple[float, SearchCandidate]] = []

        for step in range(max_steps):
            neighbours = self._retrieval.query(current_emb, k=cfg.KNN_K)
            if not neighbours:
                break

            step_candidates: List[SearchCandidate] = []
            for node, sim in neighbours:
                for target_node, edge in self._store.successors(node.node_id):
                    if not edge.verified:
                        continue
                    sim_to_target = self._similarity_to_goal(
                        target_node, goal_emb
                    )
                    score = self._score(sim, sim_to_target, step)
                    step_candidates.append(
                        SearchCandidate(
                            score=score,
                            tactic=edge.tactic,
                            predicted_node=target_node,
                            source_node_id=node.node_id,
                            similarity_to_target=sim_to_target,
                            provenance="geometric",
                        )
                    )

            if not step_candidates:
                break

            # Keep beam_width best per step
            step_candidates.sort(key=lambda c: c.score, reverse=True)
            best_this_step = step_candidates[: self._beam_width]
            beam.extend(best_this_step)

            # Advance current embedding toward best candidate's target
            if best_this_step[0].predicted_node and best_this_step[0].predicted_node.embedding:
                current_emb = best_this_step[0].predicted_node.embedding

        # Deduplicate by tactic, keeping highest score
        seen: dict = {}
        for c in beam:
            if c.tactic not in seen or c.score > seen[c.tactic].score:
                seen[c.tactic] = c
        results = sorted(seen.values(), key=lambda c: c.score, reverse=True)
        return results

    def _similarity_to_goal(
        self,
        node: ProofStateNode,
        goal_emb: Optional[Embedding],
    ) -> float:
        if goal_emb is None or node.embedding is None:
            return 0.5  # neutral when no goal is specified
        return node.embedding.cosine_similarity(goal_emb)

    def _score(
        self,
        retrieval_sim: float,
        target_sim: float,
        step: int,
    ) -> float:
        """Blend exploitation (target_sim) and exploration (step decay)."""
        exploit = (1.0 - self._exploration_weight) * target_sim
        # Exploration bonus decays with step count
        explore = self._exploration_weight * max(0.0, 1.0 - step * 0.1)
        # Weight by retrieval similarity
        return retrieval_sim * (exploit + explore)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_engine_instance: Optional[GeometricSearchEngine] = None
_engine_lock = __import__("threading").Lock()


def get_search_engine() -> GeometricSearchEngine:
    """Return the process-global ``GeometricSearchEngine`` singleton."""
    global _engine_instance
    if _engine_instance is None:
        import threading
        with _engine_lock:
            if _engine_instance is None:
                _engine_instance = GeometricSearchEngine()
    return _engine_instance
