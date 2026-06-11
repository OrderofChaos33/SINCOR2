from __future__ import annotations

"""Temporal Optimization Agent (TOA) package.

The TOA module provides a higher-level decision-optimization layer that sits
above SINCOR2's existing task routing and orchestration stack.  It implements
a continuous forecast → simulate → collapse pipeline with recursive
self-improvement via a feedback loop.

Quick start::

    from agents.toa import TOAOrchestrator

    toa = TOAOrchestrator()
    result = toa.run(context={"values": [100, 102, 105, 108, 110]})
    print(result["action_plan"])

See ``docs/architecture/toa.md`` for a full overview.
"""

from .base import CollapserAgent, FeedbackAgent, ForecasterAgent, SimulatorAgent, TOASubAgent
from .collapser import WFCCollapser
from .config import TOAConfig
from .feedback import RollingFeedbackAgent
from .forecaster import KernelForecaster
from .orchestrator import TOAOrchestrator
from .simulator import MonteCarloSimulator
from .state import TOAStateStore

__all__ = [
    "TOAOrchestrator",
    "TOAConfig",
    "TOAStateStore",
    "TOASubAgent",
    "ForecasterAgent",
    "SimulatorAgent",
    "CollapserAgent",
    "FeedbackAgent",
    "KernelForecaster",
    "MonteCarloSimulator",
    "WFCCollapser",
    "RollingFeedbackAgent",
]
