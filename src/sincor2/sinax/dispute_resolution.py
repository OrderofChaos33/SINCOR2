"""Consensus-driven dispute resolution for execution proofs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .self_healing import ActiveRoutingTable


@dataclass(frozen=True)
class ValidatorVote:
    validator_id: str
    proof_valid: bool


@dataclass(frozen=True)
class DisputeOutcome:
    slash: bool
    quarantine: bool
    votes_for_invalid: int
    votes_total: int
    reason: str


class ConsensusDisputeResolver:
    """Quorum resolver that outputs slash/quarantine actions for invalid proofs."""

    def __init__(self, table: ActiveRoutingTable, *, quorum_bps: int = 6700) -> None:
        self.table = table
        self.quorum_bps = max(1, min(10_000, int(quorum_bps)))

    def resolve(self, *, agent_id: str, votes: Iterable[ValidatorVote]) -> DisputeOutcome:
        votes_list = list(votes)
        total = len(votes_list)
        invalid = sum(1 for v in votes_list if not v.proof_valid)

        if total == 0:
            return DisputeOutcome(
                slash=False,
                quarantine=False,
                votes_for_invalid=0,
                votes_total=0,
                reason="no_votes",
            )

        invalid_bps = (invalid * 10_000) // total
        should_slash = invalid_bps >= self.quorum_bps
        if should_slash:
            self.table.quarantine(agent_id, "invalid_execution_proof")
            return DisputeOutcome(
                slash=True,
                quarantine=True,
                votes_for_invalid=invalid,
                votes_total=total,
                reason="invalid_proof_quorum",
            )

        return DisputeOutcome(
            slash=False,
            quarantine=False,
            votes_for_invalid=invalid,
            votes_total=total,
            reason="quorum_not_met",
        )
