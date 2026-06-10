from __future__ import annotations

"""Reputation engine with exponential moving averages and SINC staking.

Extended from the original EMA-based reputation engine to include:
- SINC token staking/unstaking (off-chain record; on-chain requires separate tx).
- Composite trust score boosted by staked SINC (``score * (1 + log(stake+1))``).
- ``reward_sinc()`` hook called after successful task execution.
- Staking leaderboard alongside reputation leaderboard.
"""

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional


@dataclass
class TaskOutcome:
    """A single task outcome used to update reputation signals."""

    agent_id: str
    success: bool
    quality_rating: float
    latency_ms: Optional[int] = None
    recorded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class ReputationProfile:
    """Aggregated reputation state for an agent."""

    agent_id: str
    tasks_completed: int = 0
    success_rate_ema: float = 0.5
    quality_ema: float = 0.5
    trust_score: float = 0.5
    sinc_staked: float = 0.0
    sinc_earned: float = 0.0
    last_latency_ms: Optional[int] = None
    last_updated: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class SINCRewardEvent:
    """Records a SINC reward issued to an agent after a successful task."""

    agent_id: str
    task_id: str
    amount: float
    recorded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ReputationEngine:
    """Maintains rolling reputation for agents participating in the marketplace.

    SINC staking boosts routing priority through the composite score formula::

        composite = base_trust_score * (1 + log(sinc_staked + 1))

    where ``sinc_staked`` is the number of whole SINC tokens staked by the agent.
    """

    def __init__(self, smoothing: float = 0.2) -> None:
        self.smoothing = smoothing
        self._profiles: Dict[str, ReputationProfile] = {}
        self._outcomes: List[TaskOutcome] = []
        self._reward_events: List[SINCRewardEvent] = []

    # ------------------------------------------------------------------
    # Core reputation
    # ------------------------------------------------------------------

    def record_task_outcome(
        self,
        agent_id: str,
        success: bool,
        quality_rating: float,
        latency_ms: Optional[int] = None,
    ) -> ReputationProfile:
        """Update reputation state using a new task outcome."""
        outcome = TaskOutcome(agent_id=agent_id, success=success, quality_rating=quality_rating, latency_ms=latency_ms)
        self._outcomes.append(outcome)
        profile = self._profiles.setdefault(agent_id, ReputationProfile(agent_id=agent_id))
        observation_success = 1.0 if success else 0.0
        normalized_quality = min(max(quality_rating / 5.0, 0.0), 1.0)
        profile.success_rate_ema = self._ema(profile.success_rate_ema, observation_success)
        profile.quality_ema = self._ema(profile.quality_ema, normalized_quality)
        profile.tasks_completed += 1
        profile.last_latency_ms = latency_ms
        profile.last_updated = outcome.recorded_at
        profile.trust_score = self.calculate_trust_score(agent_id)
        return profile

    def get_reputation(self, agent_id: str) -> Dict[str, object]:
        """Return the serialized reputation profile for an agent."""
        profile = self._profiles.get(agent_id)
        if profile is None:
            return {'agent_id': agent_id, 'trust_score': 0.0, 'tasks_completed': 0,
                    'sinc_staked': 0.0, 'sinc_earned': 0.0}
        return asdict(profile)

    def calculate_trust_score(self, agent_id: str) -> float:
        """Calculate composite trust score boosted by SINC stake.

        Base score formula:
            base = (success_rate_ema * 0.55) + (quality_ema * 0.35)
                   + (min(tasks_completed, 20) / 20 * 0.10)

        SINC boost multiplier:
            composite = base * (1 + log(sinc_staked + 1))
        """
        profile = self._profiles.setdefault(agent_id, ReputationProfile(agent_id=agent_id))
        latency_modifier = 1.0
        if profile.last_latency_ms is not None:
            latency_modifier = max(0.75, min(1.05, 1.0 - (profile.last_latency_ms / 100_000.0)))
        base = (
            (profile.success_rate_ema * 0.55)
            + (profile.quality_ema * 0.35)
            + (min(profile.tasks_completed, 20) / 20.0 * 0.10)
        )
        base = round(base * latency_modifier, 4)
        # SINC staking boost: log-scaled to give diminishing returns and prevent
        # large stakes from dominating routing. Capped at 3× base score.
        sinc_boost = min(1.0 + math.log(profile.sinc_staked + 1.0), 3.0)
        return round(base * sinc_boost, 4)

    def get_leaderboard(self, limit: int = 10) -> List[Dict[str, object]]:
        """Return top agents ordered by composite trust score (SINC-boosted)."""
        ranked = sorted(
            self._profiles.values(),
            key=lambda p: (-p.trust_score, p.agent_id),
        )
        return [asdict(profile) for profile in ranked[:limit]]

    # ------------------------------------------------------------------
    # SINC staking
    # ------------------------------------------------------------------

    def stake_sinc(self, agent_id: str, amount: float) -> ReputationProfile:
        """Record an increase in staked SINC for *agent_id*.

        The stake is stored as an off-chain record.  On-chain staking requires
        a separate transaction via the SINC token contract.

        Parameters
        ----------
        agent_id:
            Agent identifier in the marketplace.
        amount:
            Number of SINC tokens to stake (must be >= 0).

        Returns
        -------
        ReputationProfile
            Updated profile with new ``sinc_staked`` value and refreshed
            ``trust_score``.
        """
        if amount < 0:
            raise ValueError(f"stake_sinc: amount must be >= 0, got {amount}")
        profile = self._profiles.setdefault(agent_id, ReputationProfile(agent_id=agent_id))
        profile.sinc_staked = round(profile.sinc_staked + amount, 6)
        profile.last_updated = datetime.now(timezone.utc).isoformat()
        profile.trust_score = self.calculate_trust_score(agent_id)
        return profile

    def unstake_sinc(self, agent_id: str, amount: Optional[float] = None) -> ReputationProfile:
        """Record a decrease (or full removal) of staked SINC for *agent_id*.

        Parameters
        ----------
        agent_id:
            Agent identifier in the marketplace.
        amount:
            Number of SINC tokens to unstake.  If ``None``, all staked tokens
            are removed.

        Returns
        -------
        ReputationProfile
            Updated profile.
        """
        profile = self._profiles.setdefault(agent_id, ReputationProfile(agent_id=agent_id))
        if amount is None:
            profile.sinc_staked = 0.0
        else:
            if amount < 0:
                raise ValueError(f"unstake_sinc: amount must be >= 0, got {amount}")
            profile.sinc_staked = max(0.0, round(profile.sinc_staked - amount, 6))
        profile.last_updated = datetime.now(timezone.utc).isoformat()
        profile.trust_score = self.calculate_trust_score(agent_id)
        return profile

    # ------------------------------------------------------------------
    # SINC rewards
    # ------------------------------------------------------------------

    def reward_sinc(
        self,
        agent_id: str,
        task_id: str,
        amount: float,
    ) -> SINCRewardEvent:
        """Issue a SINC reward to an agent after a successful task.

        Records the reward event and credits it to the agent's ``sinc_earned``
        balance.  Actual on-chain distribution must be handled separately by
        the treasury.

        Parameters
        ----------
        agent_id:
            Receiving agent identifier.
        task_id:
            The task ID that triggered this reward.
        amount:
            SINC amount (whole tokens) to reward.

        Returns
        -------
        SINCRewardEvent
            The recorded reward event.
        """
        if amount < 0:
            raise ValueError(f"reward_sinc: amount must be >= 0, got {amount}")
        profile = self._profiles.setdefault(agent_id, ReputationProfile(agent_id=agent_id))
        profile.sinc_earned = round(profile.sinc_earned + amount, 6)
        profile.last_updated = datetime.now(timezone.utc).isoformat()
        event = SINCRewardEvent(agent_id=agent_id, task_id=task_id, amount=amount)
        self._reward_events.append(event)
        return event

    def get_reward_history(self, agent_id: Optional[str] = None) -> List[Dict[str, object]]:
        """Return SINC reward events, optionally filtered by agent."""
        events = self._reward_events
        if agent_id:
            events = [e for e in events if e.agent_id == agent_id]
        return [asdict(e) for e in events]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ema(self, current: float, observation: float) -> float:
        """Compute the next EMA value."""
        return (self.smoothing * observation) + ((1 - self.smoothing) * current)
