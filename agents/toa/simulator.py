from __future__ import annotations

"""Scenario simulator and multi-criteria objective evaluator for the TOA pipeline.

:class:`MonteCarloSimulator` takes forecast paths produced by a
:class:`~agents.toa.base.ForecasterAgent` and scores each path against a
weighted set of objective functions.  The output is consumed by a
:class:`~agents.toa.base.CollapserAgent` to select the highest-utility actions.

Objective function pluggability:
    Register custom objective callables with
    :meth:`MonteCarloSimulator.register_objective`.  Each callable receives a
    single path dict and returns a float score in [0, 1].

DeFi update (2026-07-21): added the built-in ``treasury_inflow`` objective so
DeFi paths (Polyclaw + vault yield) that carry explicit fee-capture estimates
outscore paths that do not compound the treasury.
"""

from typing import Any, Callable, Dict, List, Optional

from .base import SimulatorAgent
from .config import TOAConfig

# Type alias for an objective function
ObjectiveFn = Callable[[Dict[str, Any]], float]


class MonteCarloSimulator(SimulatorAgent):
    """Scores forecast paths using a pluggable objective function registry.

    Built-in objectives (``revenue``, ``treasury_inflow``, ``risk``,
    ``timeline``, ``compliance``, ``governance``) derive normalised scores
    from path statistics.  You can override any built-in or add new
    objectives at runtime.

    Parameters
    ----------
    config:
        TOA configuration.  If ``None``, defaults are loaded from environment.
    """

    agent_name = "monte_carlo_simulator"

    def __init__(self, config: Optional[TOAConfig] = None) -> None:
        super().__init__(config=config)
        self._objective_registry: Dict[str, ObjectiveFn] = {}
        self._register_builtin_objectives()

    # ------------------------------------------------------------------
    # SimulatorAgent interface
    # ------------------------------------------------------------------

    def evaluate(
        self,
        paths: List[Dict[str, Any]],
        objectives: Optional[Dict[str, float]] = None,
    ) -> List[Dict[str, Any]]:
        """Score each forecast path against the configured objective functions.

        Parameters
        ----------
        paths:
            Forecast paths from a :class:`~agents.toa.base.ForecasterAgent`.
        objectives:
            Optional per-call weight override mapping objective name → weight.
            Weights are normalised internally so they do not need to sum to 1.

        Returns
        -------
        List[Dict[str, Any]]
            Paths augmented with ``utility_score`` (float) and
            ``objective_breakdown`` (Dict[str, float]).
        """
        weights = self._normalise_weights(objectives or self.config.objective_weights)
        evaluated: List[Dict[str, Any]] = []

        for path in paths:
            breakdown: Dict[str, float] = {}
            for obj_name, weight in weights.items():
                fn = self._objective_registry.get(obj_name)
                if fn is None:
                    self._log("warning", "unknown objective skipped", objective=obj_name)
                    continue
                try:
                    raw_score = float(fn(path))
                except Exception as exc:  # pragma: no cover
                    self._log("error", "objective fn raised", objective=obj_name, error=str(exc))
                    raw_score = 0.0
                breakdown[obj_name] = round(max(0.0, min(1.0, raw_score)), 6)

            utility = sum(breakdown.get(name, 0.0) * w for name, w in weights.items())
            augmented = dict(path)
            augmented["utility_score"] = round(utility, 6)
            augmented["objective_breakdown"] = breakdown
            evaluated.append(augmented)

        self._log(
            "debug",
            "evaluate complete",
            n_paths=len(evaluated),
            objectives=list(weights.keys()),
        )
        return evaluated

    def register_objective(self, name: str, fn: ObjectiveFn) -> None:
        """Register a custom objective function.

        Parameters
        ----------
        name:
            Identifier matching a key in the objective weights config.
        fn:
            Callable ``(path: Dict) -> float`` returning a score in [0, 1].
        """
        self._objective_registry[name] = fn
        self._log("debug", "objective registered", objective=name)

    # ------------------------------------------------------------------
    # Built-in objectives
    # ------------------------------------------------------------------

    def _register_builtin_objectives(self) -> None:
        """Register all built-in objective scoring functions."""
        self.register_objective("revenue", self._score_revenue)
        self.register_objective("treasury_inflow", self._score_treasury_inflow)
        self.register_objective("risk", self._score_risk)
        self.register_objective("timeline", self._score_timeline)
        self.register_objective("compliance", self._score_compliance)
        self.register_objective("governance", self._score_governance)

    @staticmethod
    def _score_revenue(path: Dict[str, Any]) -> float:
        """Normalised terminal growth relative to start value."""
        values: List[float] = [float(v) for v in path.get("values", [])]
        if len(values) < 2:
            return 0.5
        start = abs(values[0]) + 1e-9
        terminal = values[-1]
        growth = (terminal - values[0]) / start
        # Map growth onto [0, 1]: 0 → 0.5, +100 % → ~0.87, −100 % → ~0.27
        return 1.0 / (1.0 + pow(2.718281828, -growth))

    @staticmethod
    def _score_treasury_inflow(path: Dict[str, Any]) -> float:
        """Fee capture / treasury inflow carried by the path.

        DeFi-aware paths (Polyclaw drawdowns, vault yield, lending loops)
        carry an explicit ``treasury_inflow`` estimate in absolute units —
        protocol fees + harvestable yield expected over the horizon.  The
        score is a sigmoid of inflow relative to ``inflow_scale`` (default
        1000 units, injectable per path), so:

            inflow 0        → 0.50
            inflow = scale  → ~0.73
            inflow = 3×scale→ ~0.88

        Paths without an inflow estimate score a neutral 0.5 — the objective
        only differentiates when DeFi signal is actually present.
        """
        inflow = path.get("treasury_inflow")
        if inflow is None:
            return 0.5
        scale = abs(float(path.get("inflow_scale", 1000.0))) + 1e-9
        x = float(inflow) / scale
        return 1.0 / (1.0 + pow(2.718281828, -x))

    @staticmethod
    def _score_risk(path: Dict[str, Any]) -> float:
        """Inverse of normalised path volatility (lower volatility → higher score)."""
        values: List[float] = [float(v) for v in path.get("values", [])]
        if len(values) < 2:
            return 0.5
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        std_dev = variance ** 0.5
        scale = (abs(mean) + 1e-9)
        cv = std_dev / scale  # Coefficient of variation
        return max(0.0, 1.0 - min(cv, 1.0))

    @staticmethod
    def _score_timeline(path: Dict[str, Any]) -> float:
        """Score based on how quickly the path reaches its peak value."""
        values: List[float] = [float(v) for v in path.get("values", [])]
        if not values:
            return 0.5
        peak_idx = values.index(max(values))
        # Earlier peak is better (score 1.0 at step 0, decays toward 0.1)
        return max(0.1, 1.0 - (peak_idx / max(len(values), 1)))

    @staticmethod
    def _score_compliance(path: Dict[str, Any]) -> float:
        """Return explicitly injected compliance score or default 0.8."""
        return float(path.get("compliance_score", 0.8))

    @staticmethod
    def _score_governance(path: Dict[str, Any]) -> float:
        """Return explicitly injected governance score or default 0.7."""
        return float(path.get("governance_score", 0.7))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_weights(weights: Dict[str, float]) -> Dict[str, float]:
        """Normalise weight values so they sum to 1.0."""
        total = sum(weights.values())
        if total <= 0:
            return weights
        return {k: v / total for k, v in weights.items()}
