from __future__ import annotations

"""Governance workflow primitives for policy and protocol upgrades."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import uuid4


@dataclass
class Vote:
    """Represents a vote cast on a proposal."""

    voter: str
    support: bool
    weight: float = 1.0
    rationale: str = ''
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class Proposal:
    """Represents a governance proposal and its lifecycle state."""

    proposal_id: str
    title: str
    description: str
    author: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: str = 'draft'
    policy_updates: Dict[str, Any] = field(default_factory=dict)
    votes: List[Vote] = field(default_factory=list)
    execution_notes: str = ''


class GovernanceEngine:
    """Manages proposal submission, voting, and execution."""

    def __init__(self, approval_threshold: float = 0.6) -> None:
        self.approval_threshold = approval_threshold
        self.proposals: Dict[str, Proposal] = {}

    def submit_proposal(self, title: str, description: str, author: str, policy_updates: Dict[str, Any]) -> Proposal:
        """Create a new draft proposal."""
        proposal = Proposal(
            proposal_id=f"prop-{uuid4().hex[:10]}",
            title=title,
            description=description,
            author=author,
            policy_updates=policy_updates,
        )
        self.proposals[proposal.proposal_id] = proposal
        return proposal

    def cast_vote(self, proposal_id: str, voter: str, support: bool, weight: float = 1.0, rationale: str = '') -> Proposal:
        """Cast a vote and update proposal status accordingly."""
        proposal = self.proposals[proposal_id]
        if proposal.status == 'draft':
            proposal.status = 'voting'
        proposal.votes.append(Vote(voter=voter, support=support, weight=weight, rationale=rationale))
        proposal.status = self._determine_status(proposal)
        return proposal

    def execute_proposal(self, proposal_id: str) -> Proposal:
        """Execute an approved proposal."""
        proposal = self.proposals[proposal_id]
        if proposal.status != 'approved':
            raise ValueError('Proposal must be approved before execution.')
        proposal.status = 'executed'
        proposal.execution_notes = 'Policy updates accepted for downstream runtime application.'
        return proposal

    def get_proposal_status(self, proposal_id: str) -> Dict[str, Any]:
        """Return a serialized snapshot of a proposal."""
        proposal = self.proposals[proposal_id]
        return asdict(proposal)

    def _determine_status(self, proposal: Proposal) -> str:
        """Determine proposal status from accumulated vote weight."""
        total_weight = sum(vote.weight for vote in proposal.votes)
        if total_weight == 0:
            return 'voting'
        approval_weight = sum(vote.weight for vote in proposal.votes if vote.support)
        approval_ratio = approval_weight / total_weight
        return 'approved' if approval_ratio >= self.approval_threshold else 'voting'
