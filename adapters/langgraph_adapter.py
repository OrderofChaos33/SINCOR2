"""LangGraph → SINCOR A2A adapter.

Wraps a LangGraph ``StateGraph`` (or compiled graph) so it becomes a SINCOR
marketplace participant with a fully compliant A2A Agent Card and JSON-RPC
endpoint.

Usage
-----
``langgraph`` is an **optional** dependency::

    pip install langgraph

Then::

    from langgraph.graph import StateGraph
    from adapters.langgraph_adapter import wrap_graph

    graph = StateGraph(...)
    # ... add nodes, edges, compile ...
    compiled = graph.compile()

    adapter = wrap_graph(
        compiled,
        name="My LangGraph Agent",
        skills=[{"id": "process", "name": "Process Request"}],
        tags=["langgraph"],
    )
    blueprint = adapter.to_flask_blueprint()
    adapter.register(agent_base_url="https://my-lg-agent.example.com")
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from adapters.generic_adapter import GenericAdapter

logger = logging.getLogger("sincor.adapters.langgraph")


def _extract_skills_from_graph(
    graph: Any,
    extra_skills: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Derive A2A skills from a LangGraph graph's node names."""
    skills: List[Dict[str, Any]] = list(extra_skills or [])
    seen = {s["id"] for s in skills}

    # Try to read node names from the compiled graph
    nodes = {}
    # LangGraph compiled graph exposes nodes via .graph.nodes or ._graph.nodes
    for attr in ("graph", "_graph"):
        g = getattr(graph, attr, None)
        if g is not None:
            nodes = getattr(g, "nodes", {})
            break
    # Fallback: the object itself might have .nodes
    if not nodes:
        nodes = getattr(graph, "nodes", {})

    for node_name in nodes:
        if node_name in ("__start__", "__end__", "END", "START"):
            continue
        skill_id = node_name.lower().replace("_", "-")
        if skill_id not in seen:
            seen.add(skill_id)
            skills.append(
                {
                    "id": skill_id,
                    "name": node_name.replace("_", " ").title(),
                    "description": f"LangGraph node: {node_name}",
                    "tags": ["langgraph", skill_id],
                    "examples": [],
                    "inputModes": ["text/plain", "application/json"],
                    "outputModes": ["text/plain", "application/json"],
                }
            )

    if not skills:
        skills.append(
            {
                "id": "graph-invoke",
                "name": "Graph Invoke",
                "description": "Invoke the LangGraph state graph.",
                "tags": ["langgraph"],
                "examples": [],
                "inputModes": ["text/plain", "application/json"],
                "outputModes": ["text/plain", "application/json"],
            }
        )

    return skills


def _build_graph_handler(graph: Any):
    """Return a handler that invokes the compiled LangGraph graph."""

    def handler(task: Dict[str, Any]) -> Dict[str, Any]:
        payload = task.get("payload", {})
        # LangGraph graphs accept an initial state dict
        initial_state = {
            "input": payload.get("input", ""),
            **{k: v for k, v in payload.items() if k != "input"},
        }
        try:
            result = graph.invoke(initial_state)
            # result is a dict (final state); serialize the output key or full dict
            output = result.get("output") or result.get("messages") or result
            return {
                "status": "success",
                "result": {"output": output if isinstance(output, (str, list)) else str(output)},
            }
        except Exception as exc:
            logger.error("LangGraph graph invoke failed: %s", exc, exc_info=True)
            return {"status": "error", "result": {}, "error": str(exc)}

    return handler


def wrap_graph(
    graph: Any,
    name: Optional[str] = None,
    description: str = "",
    version: str = "1.0.0",
    skills: Optional[List[Dict[str, Any]]] = None,
    tags: Optional[List[str]] = None,
    provider_name: Optional[str] = None,
    provider_url: Optional[str] = None,
) -> GenericAdapter:
    """Wrap a compiled LangGraph graph as a SINCOR marketplace participant.

    Parameters
    ----------
    graph:
        A compiled LangGraph graph (``StateGraph.compile()`` output).
    name:
        Override the agent name (default: class name of the graph).
    description:
        Human-readable agent description.
    version:
        Semantic version string.
    skills:
        Explicit skill list.  If omitted, skills are derived from graph nodes.
    tags:
        Discovery tags.
    provider_name / provider_url:
        Provider metadata.

    Returns
    -------
    GenericAdapter
        A fully configured adapter.
    """
    agent_name = name or type(graph).__name__
    derived_skills = _extract_skills_from_graph(graph, extra_skills=skills)
    all_tags = list({"langgraph", *(tags or [])})
    agent_desc = description or f"LangGraph graph with {len(derived_skills)} derived skill(s)."

    return GenericAdapter(
        name=agent_name,
        handler=_build_graph_handler(graph),
        skills=derived_skills,
        description=agent_desc,
        version=version,
        tags=all_tags,
        provider_name=provider_name,
        provider_url=provider_url,
    )
