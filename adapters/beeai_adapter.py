"""BeeAI Framework → SINCOR A2A adapter.

Wraps a BeeAI ``BaseAgent`` (or any agent that implements ``run()``) so it
becomes a SINCOR marketplace participant with an A2A-compliant Agent Card
and JSON-RPC endpoint.

BeeAI Framework is an **optional** dependency::

    pip install beeai-framework

Then::

    from beeai_framework.agents.react import ReActAgent
    from adapters.beeai_adapter import wrap_beeai

    bee_agent = ReActAgent(llm=..., tools=[...])

    adapter = wrap_beeai(
        bee_agent,
        name="BeeAI Research Agent",
        skills=[{"id": "research", "name": "Research"}],
        tags=["beeai", "research"],
    )
    blueprint = adapter.to_flask_blueprint()
    adapter.register(agent_base_url="https://my-bee.example.com")

Works without beeai-framework installed: just pass any object with a
``run(prompt)`` or ``invoke(input)`` method, or provide a plain handler.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from adapters.generic_adapter import GenericAdapter

logger = logging.getLogger("sincor.adapters.beeai")


def _discover_beeai_skills(
    agent: Any,
    extra_skills: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Derive skills from BeeAI agent metadata (tools, capabilities, etc.)."""
    skills: List[Dict[str, Any]] = list(extra_skills or [])
    seen = {s["id"] for s in skills}

    # BeeAI agents may expose .tools or .capabilities
    tools = getattr(agent, "tools", []) or getattr(agent, "_tools", [])
    for tool in tools:
        tool_name = getattr(tool, "name", None) or getattr(tool, "__class__", type(tool)).__name__
        tool_desc = getattr(tool, "description", "") or f"BeeAI tool: {tool_name}"
        skill_id = tool_name.lower().replace(" ", "-")
        if skill_id not in seen:
            seen.add(skill_id)
            skills.append(
                {
                    "id": skill_id,
                    "name": tool_name,
                    "description": tool_desc,
                    "tags": ["beeai", skill_id],
                    "examples": [],
                    "inputModes": ["text/plain", "application/json"],
                    "outputModes": ["text/plain", "application/json"],
                }
            )

    if not skills:
        skills.append(
            {
                "id": "beeai-run",
                "name": "BeeAI Agent Run",
                "description": "Execute the BeeAI agent with a prompt.",
                "tags": ["beeai"],
                "examples": [],
                "inputModes": ["text/plain", "application/json"],
                "outputModes": ["text/plain", "application/json"],
            }
        )

    return skills


def _build_beeai_handler(agent: Any):
    """Return a handler that runs the BeeAI agent with appropriate method."""

    def handler(task: Dict[str, Any]) -> Dict[str, Any]:
        payload = task.get("payload", {})
        prompt = payload.get("input", "")

        try:
            # Try common BeeAI invocation patterns
            result = None
            if hasattr(agent, "run"):
                raw = agent.run(prompt)
                # BeeAI run() may return a coroutine in async-first agents
                import asyncio
                import inspect
                if inspect.isawaitable(raw):
                    raw = asyncio.get_event_loop().run_until_complete(raw)
                result = raw
            elif hasattr(agent, "invoke"):
                result = agent.invoke({"input": prompt})
            else:
                raise RuntimeError(
                    f"BeeAI agent {type(agent).__name__} has no run() or invoke() method."
                )

            # Normalise the result
            if hasattr(result, "result"):
                output = str(result.result)
            elif isinstance(result, dict):
                output = result.get("output") or str(result)
            else:
                output = str(result)

            return {"status": "success", "result": {"output": output}}

        except Exception as exc:
            logger.error("BeeAI agent run failed: %s", exc, exc_info=True)
            return {"status": "error", "result": {}, "error": str(exc)}

    return handler


def wrap_beeai(
    agent: Any,
    name: Optional[str] = None,
    description: str = "",
    version: str = "1.0.0",
    skills: Optional[List[Dict[str, Any]]] = None,
    tags: Optional[List[str]] = None,
    provider_name: Optional[str] = None,
    provider_url: Optional[str] = None,
) -> GenericAdapter:
    """Wrap a BeeAI agent as a SINCOR marketplace participant.

    Parameters
    ----------
    agent:
        A BeeAI agent (or any object with ``run()`` / ``invoke()``).
    name:
        Override the agent name (default: class name).
    description:
        Human-readable agent description.
    version:
        Semantic version string.
    skills:
        Explicit skills list; auto-derived from agent tools if omitted.
    tags:
        Discovery tags.
    provider_name / provider_url:
        Provider metadata.

    Returns
    -------
    GenericAdapter
        A fully configured adapter.
    """
    agent_name = name or type(agent).__name__
    derived_skills = _discover_beeai_skills(agent, extra_skills=skills)
    all_tags = list({"beeai", *(tags or [])})
    agent_desc = description or f"BeeAI agent: {agent_name}."

    return GenericAdapter(
        name=agent_name,
        handler=_build_beeai_handler(agent),
        skills=derived_skills,
        description=agent_desc,
        version=version,
        tags=all_tags,
        provider_name=provider_name,
        provider_url=provider_url,
    )
