"""SINCOR Framework Adapter Layer.

Provides thin wrappers so agents built with any framework (CrewAI, LangGraph,
BeeAI, or plain Python callables) become first-class SINCOR marketplace
participants with minimal code.

Quick start
-----------
    from adapters import sincor_agent

    @sincor_agent(name="My Agent", skills=[...])
    def my_handler(task):
        return {"status": "success", "result": {"answer": "42"}}

See individual adapter modules for framework-specific wrappers.
"""

from __future__ import annotations

from adapters.generic_adapter import GenericAdapter, sincor_agent
from adapters.crewai_adapter import wrap_crew
from adapters.langgraph_adapter import wrap_graph
from adapters.beeai_adapter import wrap_beeai

__all__ = [
    "GenericAdapter",
    "sincor_agent",
    "wrap_crew",
    "wrap_graph",
    "wrap_beeai",
]
