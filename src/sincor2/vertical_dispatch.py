"""Dispatch A2A tasks to vertical agent packs and orchestration core."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, Tuple

from verticals.loader import resolve_vertical_agent

logger = logging.getLogger(__name__)

# Maps A2A skill ids for cross-cutting SINCOR skills to AgencyKernel task types.
_KERNEL_SKILL_MAP: Dict[str, str] = {
    "market-intelligence": "market_analysis",
    "lead-enrichment": "lead_enrichment",
    "contract-negotiation": "contract_review",
    "content-creation": "content_generation",
    "predictive-analytics": "predictive_analysis",
    "quality-audit": "quality_audit",
    "agent-lifecycle": "agent_management",
    "axiom-payment": "payment_verification",
}


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

    # Attempt agency kernel execution for non-vertical skills
    kernel_result = _dispatch_via_agency_kernel(skill_id, input_text)
    if kernel_result:
        return kernel_result

    return (
        json.dumps(
            {
                "status": "routed",
                "agent_id": decision.agent_id,
                "score": decision.score,
                "trust_score": decision.trust_score,
                "matched_skills": decision.matched_skills,
                "message": "Task routed; awaiting agent execution hook.",
            },
            indent=2,
        ),
        None,
    )


def _dispatch_via_agency_kernel(
    skill_id: str,
    input_text: str,
) -> Optional[Tuple[str, None]]:
    """Attempt execution through the AgencyKernel for cross-cutting SINCOR skills."""
    task_type = _KERNEL_SKILL_MAP.get(skill_id)
    if not task_type:
        return None

    try:
        from sincor2.agency_kernel import AgencyKernel

        kernel = AgencyKernel()
        task_context = {
            "task_type": task_type,
            "skill_id": skill_id,
            "input": input_text,
        }
        result = kernel.execute_task(task_context)
        if isinstance(result, dict):
            output = json.dumps(result, indent=2)
        else:
            output = str(result)
        logger.info("Agency kernel dispatch skill=%s task_type=%s", skill_id, task_type)
        return output, None
    except (ImportError, AttributeError):
        logger.debug("AgencyKernel not available for skill=%s", skill_id)
        return None
    except Exception as exc:
        logger.warning("AgencyKernel dispatch error skill=%s: %s", skill_id, exc)
        return None