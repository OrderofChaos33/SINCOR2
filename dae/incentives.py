from __future__ import annotations

"""Incentive design helpers for contribution and staking rewards."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List


@dataclass
class RewardEvent:
    """Represents a contributor reward or incentive distribution."""

    contributor_id: str
    category: str
    merit_points: int
    token_amount: str
    reason: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class IncentiveDesigner:
    """Calculates rewards and ranks contributors based on merit and stake."""

    def __init__(self) -> None:
        self.merit_points: Dict[str, int] = {}
        self.reward_history: List[RewardEvent] = []

    def calculate_reward(self, contribution_type: str, impact_score: float, stake_amount: Decimal = Decimal('0')) -> RewardEvent:
        """Calculate a reward event from a contribution and optional stake."""
        base_points = {
            'code': 40,
            'review': 25,
            'governance': 30,
            'ops': 20,
            'referral': 15,
        }.get(contribution_type, 10)
        merit = int(round(base_points * max(impact_score, 0.1)))
        token_amount = (Decimal(merit) / Decimal('10')) + (stake_amount * Decimal('0.02'))
        return RewardEvent(
            contributor_id='',
            category=contribution_type,
            merit_points=merit,
            token_amount=str(token_amount.quantize(Decimal('0.0001'))),
            reason=f'Reward for {contribution_type} contribution with impact score {impact_score:.2f}.',
        )

    def distribute_incentives(
        self,
        contributor_id: str,
        contribution_type: str,
        impact_score: float,
        stake_amount: Decimal = Decimal('0'),
    ) -> RewardEvent:
        """Persist a reward event and update contributor rank state."""
        reward = self.calculate_reward(contribution_type, impact_score, stake_amount)
        reward.contributor_id = contributor_id
        self.merit_points[contributor_id] = self.merit_points.get(contributor_id, 0) + reward.merit_points
        self.reward_history.append(reward)
        return reward

    def get_contributor_rank(self, contributor_id: str) -> Dict[str, object]:
        """Return rank and merit summary for a contributor."""
        points = self.merit_points.get(contributor_id, 0)
        if points >= 500:
            rank = 'legendary'
        elif points >= 250:
            rank = 'advanced'
        elif points >= 100:
            rank = 'established'
        else:
            rank = 'emerging'
        return {'contributor_id': contributor_id, 'rank': rank, 'merit_points': points}
