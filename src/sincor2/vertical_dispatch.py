"""Dispatch A2A tasks to vertical agent packs and orchestration core."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, Tuple

from verticals.loader import resolve_vertical_agent

logger = logging.getLogger(__name__)


def _parse_task_payload(input_text: str, skill_id: str) -> Dict[str, Any]:
    """Build a vertical task payload from A2A input text."""
    if input_text.strip().startswith("{"):
        try:
            payload = json.loads(input_text)
            if isinstance(payload, dict):
                payload.setdefault("task_type", payload.get("task_type") or skill_id.replace("-", "_"))
                return payload
        except json.JSONDecodeError:
            pass

    task_type = skill_id.replace("-", "_")
    return {
        "task_type": task_type,
        "payload": {"input": input_text},
        "correlation_id": None,
    }


def dispatch_vertical_task(
    skill_id: str,
    input_text: str,
    platform_state: Optional[Dict[str, Any]],
) -> Optional[Tuple[str, None]]:
    """Try to fulfill a task via a registered vertical agent."""
    if not platform_state:
        return None

    agents = platform_state.get("vertical_agents", {})
    agent = resolve_vertical_agent(skill_id, agents)
    if not agent:
        return None

    task_payload = _parse_task_payload(input_text, skill_id)
    result = agent.run(task_payload)
    output = json.dumps(result, indent=2) if isinstance(result, dict) else str(result)
    logger.info("Vertical dispatch skill=%s agent=%s status=%s", skill_id, agent.name, result.get("status"))
    return output, None


def dispatch_via_router(
    task_id: str,
    skill_id: str,
    input_text: str,
    platform_state: Optional[Dict[str, Any]],
) -> Optional[Tuple[str, None]]:
    """Route through the orchestration core when a matching agent card exists."""
    if not platform_state:
        return None

    router = platform_state.get("router")
    if router is None:
        return None

    decision = router.route(task_id=task_id, required_skills=[skill_id])
    if not decision:
        return None

    vertical_result = dispatch_vertical_task(skill_id, input_text, platform_state)
    if vertical_result:
        return vertical_result

    return (
        json.dumps(
            {
                "status": "routed",
                "agent_id": decision.agent_id,
                "score": decision.score,
                "matched_skills": decision.matched_skills,
                "message": "Task routed; awaiting agent execution hook.",
            },
            indent=2,
        ),
        None,
    )