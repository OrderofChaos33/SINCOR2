"""CrewAI → SINCOR A2A adapter.

Wraps a CrewAI ``Crew`` object so it becomes a full SINCOR marketplace
participant — discoverable via /.well-known/agent-card.json and callable
over JSON-RPC A2A.

Usage
-----
``crewai`` is an **optional** dependency. Install it separately::

    pip install crewai

Then::

    from crewai import Agent, Crew, Task
    from adapters.crewai_adapter import wrap_crew

    researcher = Agent(role="Researcher", goal="Find facts",
                       backstory="Expert at finding information.")
    writer     = Agent(role="Writer",     goal="Summarise findings",
                       backstory="Concise technical writer.")

    crew = Crew(
        agents=[researcher, writer],
        tasks=[Task(description="Research {topic} and write a summary.",
                    agent=researcher)],
        verbose=False,
    )

    # Wrap as SINCOR participant (≤5 lines from here):
    adapter = wrap_crew(crew, name="Research Crew", tags=["research"])
    blueprint = adapter.to_flask_blueprint()      # mount in your Flask app
    adapter.register(agent_base_url="https://my-crew.example.com")
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from adapters.generic_adapter import GenericAdapter

logger = logging.getLogger("sincor.adapters.crewai")

_CREWAI_MISSING = (
    "crewai is not installed. Install it with: pip install crewai\n"
    "The SINCOR CrewAI adapter works without crewai installed — you can still "
    "build Agent Cards from plain dicts using wrap_crew_dict()."
)


def _extract_skills_from_crew(crew: Any) -> List[Dict[str, Any]]:
    """Derive A2A skills from CrewAI Crew agents and tasks."""
    skills: List[Dict[str, Any]] = []
    seen: set = set()

    # Agents → each role becomes a discoverable skill
    for agent in getattr(crew, "agents", []):
        role: str = getattr(agent, "role", "agent")
        goal: str = getattr(agent, "goal", "")
        skill_id = role.lower().replace(" ", "-")
        if skill_id not in seen:
            seen.add(skill_id)
            skills.append(
                {
                    "id": skill_id,
                    "name": role,
                    "description": goal or f"Handled by the {role} agent.",
                    "tags": ["crewai", skill_id],
                    "examples": [],
                    "inputModes": ["text/plain", "application/json"],
                    "outputModes": ["text/plain", "application/json"],
                }
            )

    # Tasks → each task description becomes a skill if not already covered
    for task in getattr(crew, "tasks", []):
        desc: str = getattr(task, "description", "")
        if not desc:
            continue
        # Use first few words as skill id
        words = desc.lower().split()[:4]
        skill_id = "-".join(w.strip(".,;") for w in words if w.strip(".,;"))
        if skill_id and skill_id not in seen:
            seen.add(skill_id)
            skills.append(
                {
                    "id": skill_id,
                    "name": desc[:60],
                    "description": desc,
                    "tags": ["crewai", "task"],
                    "examples": [],
                    "inputModes": ["text/plain", "application/json"],
                    "outputModes": ["text/plain", "application/json"],
                }
            )

    if not skills:
        skills.append(
            {
                "id": "crew-task",
                "name": "Crew Task",
                "description": "Execute a task via the CrewAI crew.",
                "tags": ["crewai"],
                "examples": [],
                "inputModes": ["text/plain", "application/json"],
                "outputModes": ["text/plain", "application/json"],
            }
        )

    return skills


def _build_crew_handler(crew: Any):
    """Return a task handler that runs the CrewAI crew synchronously."""

    def handler(task: Dict[str, Any]) -> Dict[str, Any]:
        payload = task.get("payload", {})
        inputs = {k: v for k, v in payload.items() if k != "input"}
        if payload.get("input"):
            inputs.setdefault("input", payload["input"])
        try:
            result = crew.kickoff(inputs=inputs)
            return {
                "status": "success",
                "result": {
                    "output": str(result),
                    "crew": getattr(crew, "id", None),
                },
            }
        except Exception as exc:
            logger.error("CrewAI crew kickoff failed: %s", exc, exc_info=True)
            return {
                "status": "error",
                "result": {},
                "error": str(exc),
            }

    return handler


def wrap_crew(
    crew: Any,
    name: Optional[str] = None,
    description: str = "",
    version: str = "1.0.0",
    tags: Optional[List[str]] = None,
    provider_name: Optional[str] = None,
    provider_url: Optional[str] = None,
) -> GenericAdapter:
    """Wrap a CrewAI ``Crew`` as a SINCOR :class:`~adapters.generic_adapter.GenericAdapter`.

    Parameters
    ----------
    crew:
        A ``crewai.Crew`` instance.
    name:
        Override the agent name in the Agent Card (default: crew class name).
    description:
        Override the agent description.
    version:
        Semantic version string.
    tags:
        Discovery tags applied to every derived skill.
    provider_name / provider_url:
        Provider metadata for the Agent Card.

    Returns
    -------
    GenericAdapter
        A fully configured adapter.  Call ``.to_flask_blueprint()`` to get a
        Flask Blueprint or ``.register()`` to publish to the SINCOR marketplace.
    """
    agent_name = name or getattr(crew, "__class__", type(crew)).__name__
    skills = _extract_skills_from_crew(crew)
    all_tags = list({"crewai", *(tags or [])})
    agent_desc = description or (
        f"CrewAI crew with {len(getattr(crew, 'agents', []))} agents "
        f"and {len(getattr(crew, 'tasks', []))} tasks."
    )

    return GenericAdapter(
        name=agent_name,
        handler=_build_crew_handler(crew),
        skills=skills,
        description=agent_desc,
        version=version,
        tags=all_tags,
        provider_name=provider_name,
        provider_url=provider_url,
    )


def wrap_crew_dict(
    crew_spec: Dict[str, Any],
    name: str,
    skills: List[Dict[str, Any]],
    handler: Any,
    description: str = "",
    version: str = "1.0.0",
    tags: Optional[List[str]] = None,
) -> GenericAdapter:
    """Create a CrewAI-style adapter from a plain dict spec (no crewai install required).

    Useful when you want to describe a crew in metadata without the crewai
    library installed in the SINCOR runtime.

    Parameters
    ----------
    crew_spec:
        Arbitrary metadata dict (stored in the Agent Card metadata).
    name:
        Agent name.
    skills:
        Explicitly provided skill list.
    handler:
        Callable that executes the crew logic.
    """
    all_tags = list({"crewai", *(tags or [])})
    return GenericAdapter(
        name=name,
        handler=handler,
        skills=skills,
        description=description or f"CrewAI crew: {name}",
        version=version,
        tags=all_tags,
    )
