from __future__ import annotations

"""Abstract base classes for all TOA sub-agents.

Each sub-agent in the Temporal Optimization Agent pipeline implements one of
these four interfaces:

* :class:`ForecasterAgent` — produces future-state probability distributions.
* :class:`SimulatorAgent`  — runs scenario simulations and scores each path.
* :class:`CollapserAgent`  — selects the highest-utility paths and emits
  actionable task dispatches.
* :class:`FeedbackAgent`   — ingests execution results and feeds them back
  into the forecasting / simulation loop.

All agents share the :class:`TOASubAgent` mixin which provides structured
logging, basic error handling, and the :pyattr:`config` accessor.
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from .config import TOAConfig


class TOASubAgent(ABC):
    """Mixin providing common infrastructure for all TOA sub-agents."""

    #: Human-readable name used in logs and metrics.
    agent_name: str = "toa_sub_agent"

    def __init__(self, config: Optional[TOAConfig] = None) -> None:
        self.config = config or TOAConfig.from_env()
        self.logger = logging.getLogger(f"sincor.toa.{self.agent_name}")

    def _log(self, level: str, msg: str, **extra: Any) -> None:
        """Emit a structured log entry."""
        record: Dict[str, Any] = {"agent": self.agent_name, "msg": msg, **extra}
        getattr(self.logger, level)(record if self.config.structured_logging else msg)


# ---------------------------------------------------------------------------
# Forecaster interface
# ---------------------------------------------------------------------------

class ForecasterAgent(TOASubAgent, ABC):
    """Produces probability-weighted future-state projections.

    The output is a list of :class:`ForecastPath` dicts, each containing a
    ``scenario_id``, ``probability`` (0–1), ``horizon``, and ``values`` payload.
    Concrete implementations may use statistical models, ML inference, or
    external time-series APIs.
    """

    agent_name: str = "forecaster"

    @abstractmethod
    def forecast(
        self,
        context: Dict[str, Any],
        horizon: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Generate forecast paths given the current context.

        Parameters
        ----------
        context:
            Arbitrary context dict describing the current state (market data,
            operational metrics, governance signals, etc.).
        horizon:
            Override for :pyattr:`TOAConfig.forecast_horizon`.

        Returns
        -------
        List[Dict[str, Any]]
            Ordered list of forecast paths, each a dict with at minimum:
            ``scenario_id``, ``probability`` (float 0–1), ``horizon`` (int),
            and ``values`` (list of floats).
        """


# ---------------------------------------------------------------------------
# Simulator / evaluator interface
# ---------------------------------------------------------------------------

class SimulatorAgent(TOASubAgent, ABC):
    """Scores forecast paths against one or more objective functions.

    Implementations receive raw forecast paths and return augmented dicts
    that include a composite ``utility_score`` for each path.
    """

    agent_name: str = "simulator"

    @abstractmethod
    def evaluate(
        self,
        paths: List[Dict[str, Any]],
        objectives: Optional[Dict[str, float]] = None,
    ) -> List[Dict[str, Any]]:
        """Score each path against the configured objective functions.

        Parameters
        ----------
        paths:
            Forecast paths as returned by :meth:`ForecasterAgent.forecast`.
        objectives:
            Optional per-call objective weight override.

        Returns
        -------
        List[Dict[str, Any]]
            Same paths with an additional ``utility_score`` (float) and
            ``objective_breakdown`` (dict) added to each element.
        """


# ---------------------------------------------------------------------------
# Collapser / selector interface
# ---------------------------------------------------------------------------

class CollapserAgent(TOASubAgent, ABC):
    """Selects the highest-utility paths and converts them to action plans.

    Analogous to the "collapse" step in wave function collapse algorithms:
    the superposition of possible futures is resolved into the concrete,
    ranked set of paths that should be executed.
    """

    agent_name: str = "collapser"

    @abstractmethod
    def collapse(
        self,
        evaluated_paths: List[Dict[str, Any]],
        top_k: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Collapse the evaluated paths into a ranked action plan.

        Parameters
        ----------
        evaluated_paths:
            Paths with ``utility_score`` as returned by
            :meth:`SimulatorAgent.evaluate`.
        top_k:
            Override for :pyattr:`TOAConfig.top_k_paths`.

        Returns
        -------
        List[Dict[str, Any]]
            Top-k collapsed paths, each enriched with a ``rank`` (int),
            ``action_dispatch`` (dict describing the recommended task),
            and ``rationale`` (str) field.
        """


# ---------------------------------------------------------------------------
# Feedback loop interface
# ---------------------------------------------------------------------------

class FeedbackAgent(TOASubAgent, ABC):
    """Ingests execution results and adapts the next forecast/simulation cycle.

    Feedback sources include on-chain events, vertical task outcomes,
    marketplace reputation deltas, and any other execution signals.
    """

    agent_name: str = "feedback"

    @abstractmethod
    def ingest(self, event: Dict[str, Any]) -> None:
        """Record a new execution result or external signal.

        Parameters
        ----------
        event:
            Dict describing the outcome.  Expected keys: ``source``
            (str), ``timestamp`` (ISO-8601 str), ``payload`` (dict).
            Additional keys are accepted and stored.
        """

    @abstractmethod
    def get_feedback_summary(self) -> Dict[str, Any]:
        """Return an aggregated summary of all ingested feedback.

        Returns
        -------
        Dict[str, Any]
            Summary dict suitable for injection into the next forecast context.
        """
