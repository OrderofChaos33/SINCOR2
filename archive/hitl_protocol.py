"""
Human-in-the-Loop (HITL) as First-Class Design Pattern (Goal #5)

Makes human integration elegant and structured in the ultimate autonomous marketplace:
- Escalation protocols: when agents must stop and ask a human
- Human-as-a-service: humans discoverable as agents ("expert in Singaporean pharma law")
- Collective intelligence: human votes on constitution changes (beyond token-weighted)

Builds on existing:
- constitution/global.md (has basic "escalate")
- agents/archetypes/Auditor.yaml (conflict resolution)
- compliance_guardrails.py + compliance_monitor.py
- swarm_coordination.py (escalation hooks)
- marketplace/registry.py (human agent registration)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Any


class EscalationTrigger(str, Enum):
    LOW_CONFIDENCE = "low_confidence"
    HIGH_RISK_DOMAIN = "high_risk_domain"  # healthcare, legal, financial
    CONFLICT_DETECTED = "conflict_detected"
    BUDGET_EXCEEDED = "budget_exceeded"
    COMPLIANCE_FLAG = "compliance_flag"
    HUMAN_REQUESTED = "human_requested"


@dataclass
class HITLRequest:
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    agent_id: str
    task_id: str
    trigger: EscalationTrigger
    context: Dict[str, Any]
    urgency: str = "medium"  # low, medium, high, critical
    human_expertise_required: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    resolved: bool = False
    resolution: Optional[Dict[str, Any]] = None


class HITLProtocol:
    """Structured Human-in-the-Loop engine.

    Agents call escalate() from AgencyKernel or verticals when triggers fire.
    Humans (or expert agents) claim and resolve.
    Votes on governance collected here for collective intelligence.
    """

    def __init__(self):
        self._requests: Dict[str, HITLRequest] = {}
        self._human_registry: Dict[str, Dict] = {}  # human_did -> profile
        self._governance_votes: List[Dict] = []

    def escalate(
        self,
        agent_id: str,
        task_id: str,
        trigger: EscalationTrigger,
        context: Dict[str, Any],
        urgency: str = "medium",
        required_expertise: Optional[List[str]] = None,
    ) -> HITLRequest:
        """Agent triggers structured escalation. Returns request for human pickup."""
        req = HITLRequest(
            agent_id=agent_id,
            task_id=task_id,
            trigger=trigger,
            context=context,
            urgency=urgency,
            human_expertise_required=required_expertise or [],
        )
        self._requests[req.request_id] = req
        # TODO: notify via webhook / SSE to registered humans or Auditor swarm
        return req

    def register_human_expert(
        self,
        human_did: str,
        expertise: List[str],
        jurisdictions: List[str] = None,
        hourly_rate: Optional[float] = None,
    ) -> Dict:
        """Register a human as discoverable 'agent' in marketplace (Goal #5 + #1 human-as-service)."""
        profile = {
            "did": human_did,
            "type": "human_expert",
            "expertise": expertise,
            "jurisdictions": jurisdictions or [],
            "hourly_rate": hourly_rate,
            "available": True,
            "registered_at": datetime.now(timezone.utc).isoformat(),
        }
        self._human_registry[human_did] = profile
        return profile

    def claim_request(self, request_id: str, human_did: str) -> Optional[HITLRequest]:
        """Human expert claims an escalation request."""
        req = self._requests.get(request_id)
        if req and not req.resolved:
            req.context["claimed_by"] = human_did
            return req
        return None

    def resolve_request(
        self,
        request_id: str,
        human_did: str,
        resolution: Dict[str, Any],
    ) -> bool:
        """Human provides resolution; agent can continue or abort."""
        req = self._requests.get(request_id)
        if not req or req.resolved:
            return False
        req.resolved = True
        req.resolution = {
            **resolution,
            "resolved_by": human_did,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        }
        return True

    def cast_governance_vote(
        self,
        voter_did: str,
        proposal_id: str,
        vote: str,  # yes/no/abstain
        rationale: str = "",
        weight: float = 1.0,  # humans can have equal or expertise-weighted
    ) -> Dict:
        """Collective intelligence vote on constitution / marketplace policy changes."""
        vote_record = {
            "voter": voter_did,
            "proposal_id": proposal_id,
            "vote": vote,
            "rationale": rationale,
            "weight": weight,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._governance_votes.append(vote_record)
        return vote_record

    def get_pending_escalations(self, expertise_filter: Optional[str] = None) -> List[Dict]:
        pending = [asdict(r) for r in self._requests.values() if not r.resolved]
        if expertise_filter:
            pending = [p for p in pending if expertise_filter in str(p.get("human_expertise_required", []))]
        return pending


# Singleton
_hitl: Optional[HITLProtocol] = None


def get_hitl_protocol() -> HITLProtocol:
    global _hitl
    if _hitl is None:
        _hitl = HITLProtocol()
    return _hitl
