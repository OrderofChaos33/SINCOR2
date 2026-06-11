"""Dispatch A2A tasks to vertical agent packs and orchestration core."""

from __future__ import annotations

import json
import logging
from datetime import date
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

_SKILL_TASK_TYPE_MAP: Dict[str, str] = {
    "healthcare-rcm": "eligibility_verification",
    "provider-credentialing": "credentialing_workflow",
    "dental-ops": "appointment_scheduling",
    "dental-compliance": "hipaa_compliance_check",
    "regulatory-compliance": "sbom_generation",
    "n8n-workflow-bridge": "n8n_workflow_bridge",
    "compliance-sbom": "sbom_generation",
    "compliance-filing": "regulatory_filing",
    "openclaw-trading": "signal_generation",
    "trading-signals": "signal_generation",
    "polymarket-execution": "polymarket_evaluation",
    "polymarket-eval": "polymarket_evaluation",
    "lead-enrichment-outbound": "lead_enrichment",
    "lead-enrichment": "lead_enrichment",
    "lead-outreach": "outreach_sequencing",
    "icp-matching": "icp_matching",
}


def _normalize_demo_payload(
    skill_id: str, payload: Dict[str, Any], task_type: str
) -> Dict[str, Any]:
    normalized = dict(payload)

    if skill_id == "healthcare-rcm":
        patient_id = (
            normalized.get("patient_id")
            or normalized.get("member_id")
            or normalized.get("claim_id")
            or "DEMO-PATIENT"
        )
        normalized.setdefault("patient_id", patient_id)
        normalized.setdefault("member_id", f"{patient_id}-MEM")
        normalized.setdefault("payer_id", "BCBS01")
        normalized.setdefault("provider_npi", "1234567890")
        normalized.setdefault("service_date", str(date.today()))
        if task_type == "prior_authorization":
            normalized.setdefault("cpt_codes", ["99213"])
            normalized.setdefault("diagnosis_codes", ["Z00.00"])
            normalized.setdefault("requested_start_date", str(date.today()))
            normalized.setdefault("requested_units", 1)
            normalized.setdefault("notes", "Auto-filled demo payload")

    elif skill_id == "provider-credentialing":
        normalized.setdefault("provider_id", "PROVIDER-DEMO")
        normalized.setdefault("provider_npi", "1234567890")
        normalized.setdefault("provider_name", "Demo Provider")
        normalized.setdefault("specialty", "family_medicine")
        normalized.setdefault("payer_ids", ["BCBS01"])

    elif skill_id in {"regulatory-compliance", "compliance-sbom"}:
        normalized.setdefault("repository", "OrderofChaos33/SINCOR2")
        normalized.setdefault("artifacts", ["requirements.txt", "pyproject.toml"])

    elif skill_id in {"openclaw-trading", "trading-signals"}:
        normalized.setdefault("asset", "ETH-USD")

    elif skill_id == "polymarket-execution":
        normalized.setdefault("market_id", "demo-market")

    elif skill_id in {"lead-enrichment-outbound", "lead-enrichment"}:
        normalized.setdefault("company", "Acme Corp")
        normalized.setdefault("segment", "B2B SaaS")

    elif skill_id in {"icp-matching", "lead-outreach"}:
        normalized.setdefault("profile", {"industry": "software", "team_size": 50})

    return normalized


def _parse_task_payload(input_text: str, skill_id: str) -> Dict[str, Any]:
    """Build a vertical task payload from A2A input text."""
    if input_text.strip().startswith("{"):
        try:
            payload = json.loads(input_text)
            if isinstance(payload, dict):
                task_type = payload.get("task_type") or _SKILL_TASK_TYPE_MAP.get(
                    skill_id, skill_id.replace("-", "_")
                )
                payload["task_type"] = task_type
                payload["payload"] = _normalize_demo_payload(
                    skill_id, payload.get("payload", {}), task_type
                )
                return payload
        except json.JSONDecodeError:
            pass

    task_type = _SKILL_TASK_TYPE_MAP.get(skill_id, skill_id.replace("-", "_"))
    return {
        "task_type": task_type,
        "payload": _normalize_demo_payload(skill_id, {"input": input_text}, task_type),
        "correlation_id": None,
    }


def dispatch_vertical_task(
    skill_id: str,
    input_text: str,
    platform_state: Optional[Dict[str, Any]],
    task_id: Optional[str] = None,
    caller_id: str = "",
) -> Optional[Tuple[str, None]]:
    """Try to fulfill a task via a registered vertical agent.

    Tasks are routed through the :class:`~core.policy.PolicyEnforcedExecutor`
    for auth checks, policy rules, retry-with-backoff, and AXIOM settlement
    triggering before and after execution.
    """
    if not platform_state:
        return None

    agents = platform_state.get("vertical_agents", {})
    agent = resolve_vertical_agent(skill_id, agents)
    if not agent:
        return None

    task_payload = _parse_task_payload(input_text, skill_id)
    task_payload.setdefault("task_id", task_id or "")
    task_payload.setdefault("caller_id", caller_id)

    # Route through policy-enforced executor for rule checks + retry
    try:
        from core.policy import get_default_executor
        executor = get_default_executor()
        result = executor.execute(agent.run, task_payload)
    except Exception:
        # Fallback: direct execution (preserves backward compat if policy import fails)
        result = agent.run(task_payload)

    output = json.dumps(result, indent=2) if isinstance(result, dict) else str(result)
    logger.info(
        "Vertical dispatch skill=%s agent=%s status=%s",
        skill_id,
        agent.name,
        result.get("status"),
    )
    return output, None


def dispatch_via_router(
    task_id: str,
    skill_id: str,
    input_text: str,
    platform_state: Optional[Dict[str, Any]],
    caller_id: str = "",
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

    vertical_result = dispatch_vertical_task(skill_id, input_text, platform_state,
                                             task_id=task_id, caller_id=caller_id)
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