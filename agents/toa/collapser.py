from __future__ import annotations

"""Wave Function Collapse–style path selector for the TOA pipeline.

:class:`WFCCollapser` resolves the superposition of evaluated forecast paths
into a ranked set of concrete, actionable task dispatches.  The "collapse"
algorithm:

1. Filters out paths whose probability falls below the configured
   :attr:`~agents.toa.config.TOAConfig.collapse_threshold`.
2. Ranks remaining paths by composite score
   (``utility_score × probability``).
3. Returns the top-k paths, each enriched with a ``rank``, an
   ``action_dispatch`` dict (suitable for passing to the SINCOR2 task
   router), and a human-readable ``rationale`` string.
"""

from typing import Any, Dict, List, Optional

from .base import CollapserAgent
from .config import TOAConfig


class WFCCollapser(CollapserAgent):
    """Selects the highest-utility paths and converts them to action plans.

    The composite score used for ranking is::

        composite = utility_score × probability × (1 + priority_boost)

    where ``priority_boost`` rewards paths aligned with objective priority
    order (configured via :attr:`~agents.toa.config.TOAConfig.objective_priority`).
    """

    agent_name = "wfc_collapser"

    def __init__(self, config: Optional[TOAConfig] = None) -> None:
        super().__init__(config=config)

    # ------------------------------------------------------------------
    # CollapserAgent interface
    # ------------------------------------------------------------------

    def collapse(
        self,
        evaluated_paths: List[Dict[str, Any]],
        top_k: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Collapse evaluated paths into a ranked, actionable plan.

        Parameters
        ----------
        evaluated_paths:
            Paths with ``utility_score`` and ``probability`` fields, as
            returned by :meth:`~agents.toa.simulator.MonteCarloSimulator.evaluate`.
        top_k:
            Maximum number of paths to return.

        Returns
        -------
        List[Dict[str, Any]]
            Up to *top_k* collapsed paths, each containing:

            * ``rank`` — 1-indexed position.
            * ``scenario_id`` — original scenario identifier.
            * ``composite_score`` — combined utility × probability metric.
            * ``utility_score`` — from the evaluator.
            * ``probability`` — from the forecaster.
            * ``objective_breakdown`` — per-objective scores.
            * ``action_dispatch`` — ready-to-dispatch task specification.
            * ``rationale`` — human-readable explanation.
        """
        k = top_k if top_k is not None else self.config.top_k_paths
        threshold = self.config.collapse_threshold

        # Filter below probability threshold
        viable = [
            p for p in evaluated_paths
            if float(p.get("probability", 0.0)) >= threshold
        ]

        if not viable:
            self._log("warning", "collapse: no paths above threshold", threshold=threshold)
            return []

        # Compute composite scores
        scored = [
            {**p, "_composite": self._composite_score(p)}
            for p in viable
        ]

        # Sort descending by composite score
        scored.sort(key=lambda p: p["_composite"], reverse=True)

        results: List[Dict[str, Any]] = []
        for rank, path in enumerate(scored[:k], start=1):
            composite = path.pop("_composite")
            results.append({
                "rank": rank,
                "scenario_id": path.get("scenario_id", f"path-{rank}"),
                "composite_score": round(composite, 6),
                "utility_score": round(float(path.get("utility_score", 0.0)), 6),
                "probability": round(float(path.get("probability", 0.0)), 6),
                "objective_breakdown": path.get("objective_breakdown", {}),
                "horizon": path.get("horizon", 0),
                "values": path.get("values", []),
                "action_dispatch": self._build_action_dispatch(path, rank),
                "rationale": self._build_rationale(path, rank, composite),
            })

        self._log(
            "debug",
            "collapse complete",
            viable_paths=len(viable),
            selected=len(results),
            top_composite=round(results[0]["composite_score"], 4) if results else 0.0,
        )
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _composite_score(self, path: Dict[str, Any]) -> float:
        """Compute the composite ranking score for a single path."""
        utility = float(path.get("utility_score", 0.0))
        probability = float(path.get("probability", 0.0))
        priority_boost = self._priority_boost(path)
        return utility * probability * (1.0 + priority_boost)

    def _priority_boost(self, path: Dict[str, Any]) -> float:
        """Small additive boost for paths that excel on high-priority objectives."""
        breakdown: Dict[str, float] = path.get("objective_breakdown", {})
        if not breakdown:
            return 0.0
        boost = 0.0
        priority_order = self.config.objective_priority
        for rank_idx, obj_name in enumerate(priority_order):
            obj_score = breakdown.get(obj_name, 0.0)
            # Higher-priority objectives yield a stronger boost coefficient.
            weight = (len(priority_order) - rank_idx) / len(priority_order)
            boost += obj_score * weight * 0.1  # Max total boost ≈ 0.55 × 0.1 = 0.055
        return round(boost, 6)

    def _build_action_dispatch(self, path: Dict[str, Any], rank: int) -> Dict[str, Any]:
        """Build an A2A-compatible task dispatch descriptor.

        The dispatch is intentionally generic so it can be passed directly to
        :class:`~core.router.TaskRouter.route` or to the TOA orchestrator.
        """
        breakdown: Dict[str, float] = path.get("objective_breakdown", {})
        # Identify the dominant objective as a routing hint
        dominant_obj = max(breakdown.items(), key=lambda item: item[1])[0] if breakdown else "general"
        return {
            "task_type": "toa_action",
            "priority": rank,
            "dominant_objective": dominant_obj,
            "scenario_id": path.get("scenario_id", ""),
            "horizon": path.get("horizon", 0),
            "terminal_value": round(float(path["values"][-1]), 6) if path.get("values") else None,
            "required_skills": [dominant_obj, "execution"],
            "metadata": {
                "utility_score": path.get("utility_score"),
                "probability": path.get("probability"),
            },
        }

    def _build_rationale(
        self,
        path: Dict[str, Any],
        rank: int,
        composite: float,
    ) -> str:
        """Build a human-readable rationale string for this collapsed path."""
        breakdown: Dict[str, float] = path.get("objective_breakdown", {})
        top_objectives = sorted(breakdown.items(), key=lambda x: -x[1])[:2]
        obj_str = ", ".join(f"{name}={score:.2f}" for name, score in top_objectives)
        return (
            f"Rank {rank}: composite={composite:.4f} "
            f"(utility={path.get('utility_score', 0.0):.3f}, "
            f"p={path.get('probability', 0.0):.4f}). "
            f"Top objectives: {obj_str}."
        )
