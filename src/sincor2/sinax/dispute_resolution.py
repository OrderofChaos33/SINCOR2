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

        # Quorum as a vote-count threshold: the number of invalid votes needed
        # is floor(quorum_bps * total / 10_000). A classic 2/3 supermajority
        # (e.g. 2 of 3 validators at quorum_bps=6700) must satisfy the
        # quorum — ratio-then-floor arithmetic ((2*10000)//3 = 6666 < 6700)
        # wrongly made it unreachable. Zero invalid votes can never form a
        # slash quorum.
        required_votes = (self.quorum_bps * total) // 10_000
        should_slash = invalid > 0 and invalid >= required_votes
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
