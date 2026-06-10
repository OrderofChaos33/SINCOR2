from __future__ import annotations

"""TOA Orchestrator — the main entry point for the Temporal Optimization Agent.

:class:`TOAOrchestrator` coordinates the full forecast → simulate → collapse
pipeline and exposes hooks for:

* Injecting feedback from execution results.
* Registering custom objective functions with the simulator.
* Routing the selected action plans via SINCOR2's :class:`~core.router.TaskRouter`.
* Persisting session state across runs via :class:`~agents.toa.state.TOAStateStore`.

Usage::

    from agents.toa import TOAOrchestrator

    toa = TOAOrchestrator()
    result = toa.run(context={"values": [100, 102, 105, 108, 110]})
    # result["action_plan"] contains ranked, actionable task dispatches.
"""

import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from .base import CollapserAgent, FeedbackAgent, ForecasterAgent, SimulatorAgent
from .collapser import WFCCollapser
from .config import TOAConfig
from .feedback import RollingFeedbackAgent
from .forecaster import KernelForecaster
from .simulator import MonteCarloSimulator
from .state import TOAStateStore

logger = logging.getLogger("sincor.toa.orchestrator")


class TOAOrchestrator:
    """Coordinates the TOA forecast → simulate → collapse pipeline.

    The orchestrator is the single public API that consumers of the TOA
    module interact with.  It wires together the four sub-agent types,
    manages session state, and integrates with the SINCOR2 task router
    when one is provided.

    Parameters
    ----------
    config:
        TOA configuration.  Defaults to environment-driven config.
    forecaster:
        Custom :class:`~agents.toa.base.ForecasterAgent`.  Defaults to
        :class:`~agents.toa.forecaster.KernelForecaster`.
    simulator:
        Custom :class:`~agents.toa.base.SimulatorAgent`.  Defaults to
        :class:`~agents.toa.simulator.MonteCarloSimulator`.
    collapser:
        Custom :class:`~agents.toa.base.CollapserAgent`.  Defaults to
        :class:`~agents.toa.collapser.WFCCollapser`.
    feedback_agent:
        Custom :class:`~agents.toa.base.FeedbackAgent`.  Defaults to
        :class:`~agents.toa.feedback.RollingFeedbackAgent`.
    task_router:
        Optional SINCOR2 :class:`~core.router.TaskRouter` instance.  When
        provided, the orchestrator automatically dispatches the top-ranked
        action plan as a routed task.
    """

    def __init__(
        self,
        config: Optional[TOAConfig] = None,
        forecaster: Optional[ForecasterAgent] = None,
        simulator: Optional[SimulatorAgent] = None,
        collapser: Optional[CollapserAgent] = None,
        feedback_agent: Optional[FeedbackAgent] = None,
        task_router: Optional[Any] = None,
    ) -> None:
        self.config = config or TOAConfig.from_env()
        self.forecaster = forecaster or KernelForecaster(config=self.config)
        self.simulator = simulator or MonteCarloSimulator(config=self.config)
        self.collapser = collapser or WFCCollapser(config=self.config)
        self.feedback_agent = feedback_agent or RollingFeedbackAgent(config=self.config)
        self.task_router = task_router
        self.state = TOAStateStore(path=self.config.state_path or None)
        self._run_count: int = int(self.state.get("run_count", 0))

    # ------------------------------------------------------------------
    # Primary API
    # ------------------------------------------------------------------

    def run(
        self,
        context: Dict[str, Any],
        objectives: Optional[Dict[str, float]] = None,
        top_k: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Execute the full TOA pipeline and return an action plan.

        The pipeline:
        1. Merge feedback summary into *context*.
        2. Run :meth:`~agents.toa.base.ForecasterAgent.forecast`.
        3. Run :meth:`~agents.toa.base.SimulatorAgent.evaluate` on all paths.
        4. Run :meth:`~agents.toa.base.CollapserAgent.collapse` to select top-k.
        5. Optionally dispatch the best action via the task router.
        6. Persist updated state.

        Parameters
        ----------
        context:
            Execution context.  Must include ``"values"`` (list of floats) for
            the default :class:`~agents.toa.forecaster.KernelForecaster`.
        objectives:
            Per-run objective weight overrides.
        top_k:
            Number of action paths to return.

        Returns
        -------
        Dict[str, Any]
            Keys:

            * ``"run_id"`` — unique identifier for this run.
            * ``"run_count"`` — total lifetime runs.
            * ``"elapsed_ms"`` — wall-clock time of the pipeline in ms.
            * ``"forecast_paths"`` — raw forecast path count.
            * ``"evaluated_paths"`` — evaluated path count.
            * ``"action_plan"`` — list of collapsed, ranked action dicts.
            * ``"route_decision"`` — router result for the top action (or
              ``None`` if no router is configured).
            * ``"feedback_summary"`` — current feedback buffer summary.
        """
        run_id = str(uuid.uuid4())
        start_time = time.monotonic()

        logger.info(
            {"event": "toa_run_start", "run_id": run_id, "run_count": self._run_count}
            if self.config.structured_logging
            else f"TOA run start: {run_id}"
        )

        # Step 1 — Merge feedback into context
        feedback_summary = self.feedback_agent.get_feedback_summary()
        enriched_context = {**context, **feedback_summary}

        # Step 2 — Forecast
        try:
            forecast_paths = self.forecaster.forecast(
                context=enriched_context,
                horizon=context.get("horizon"),
            )
        except Exception as exc:
            logger.error("toa_run: forecaster raised: %s", exc)
            forecast_paths = []

        # Step 3 — Simulate / evaluate
        try:
            evaluated_paths = self.simulator.evaluate(
                paths=forecast_paths,
                objectives=objectives,
            ) if forecast_paths else []
        except Exception as exc:
            logger.error("toa_run: simulator raised: %s", exc)
            evaluated_paths = []

        # Step 4 — Collapse
        try:
            action_plan = self.collapser.collapse(
                evaluated_paths=evaluated_paths,
                top_k=top_k,
            )
        except Exception as exc:
            logger.error("toa_run: collapser raised: %s", exc)
            action_plan = []

        # Step 5 — Optional task routing
        route_decision = None
        if self.task_router is not None and action_plan:
            route_decision = self._dispatch_top_action(action_plan[0])

        # Step 6 — Persist state
        self._run_count += 1
        self.state.set("run_count", self._run_count)
        self.state.set("last_run_id", run_id)
        self.state.set("last_action_plan", action_plan)

        elapsed_ms = round((time.monotonic() - start_time) * 1000.0, 1)
        logger.info(
            {
                "event": "toa_run_complete",
                "run_id": run_id,
                "elapsed_ms": elapsed_ms,
                "action_plan_size": len(action_plan),
            }
            if self.config.structured_logging
            else f"TOA run complete: {run_id} ({elapsed_ms} ms)"
        )

        return {
            "run_id": run_id,
            "run_count": self._run_count,
            "elapsed_ms": elapsed_ms,
            "forecast_paths": len(forecast_paths),
            "evaluated_paths": len(evaluated_paths),
            "action_plan": action_plan,
            "route_decision": route_decision,
            "feedback_summary": feedback_summary,
        }

    def ingest_feedback(self, event: Dict[str, Any]) -> None:
        """Ingest an execution result or external signal into the feedback loop.

        Parameters
        ----------
        event:
            Feedback event dict.  See
            :meth:`~agents.toa.feedback.RollingFeedbackAgent.ingest` for
            the expected structure.
        """
        self.feedback_agent.ingest(event)

    def register_objective(self, name: str, fn: Any) -> None:
        """Register a custom objective function with the underlying simulator.

        Parameters
        ----------
        name:
            Objective key (must match a key in the objective weights config to
            be included in scoring).
        fn:
            Callable ``(path: Dict) -> float``.
        """
        if isinstance(self.simulator, MonteCarloSimulator):
            self.simulator.register_objective(name, fn)
        else:
            logger.warning("register_objective: simulator does not support custom objectives")

    def get_stats(self) -> Dict[str, Any]:
        """Return diagnostic statistics about the current orchestrator session."""
        return {
            "run_count": self._run_count,
            "feedback_events": self.feedback_agent.event_count,
            "state_version": self.state.version,
            "config": {
                "simulation_depth": self.config.simulation_depth,
                "collapse_threshold": self.config.collapse_threshold,
                "top_k_paths": self.config.top_k_paths,
                "forecast_horizon": self.config.forecast_horizon,
                "monte_carlo_iterations": self.config.monte_carlo_iterations,
            },
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _dispatch_top_action(self, action: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Route the top action via the SINCOR2 task router."""
        dispatch = action.get("action_dispatch", {})
        task_id = f"toa-{action.get('scenario_id', uuid.uuid4())}"
        required_skills: List[str] = dispatch.get("required_skills", ["execution"])
        try:
            decision = self.task_router.route(
                task_id=task_id,
                required_skills=required_skills,
            )
            if decision is not None:
                logger.debug(
                    {
                        "event": "toa_route",
                        "task_id": task_id,
                        "agent_id": decision.agent_id,
                        "score": decision.score,
                    }
                    if self.config.structured_logging
                    else f"TOA routed {task_id} → {decision.agent_id}"
                )
                return {
                    "task_id": task_id,
                    "agent_id": decision.agent_id,
                    "score": decision.score,
                    "reason": decision.reason,
                }
        except Exception as exc:
            logger.error("toa_dispatch: router raised: %s", exc)
        return None
