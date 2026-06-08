from __future__ import annotations

"""Reputation engine with exponential moving averages for trust signals."""

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
    last_latency_ms: Optional[int] = None
    last_updated: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ReputationEngine:
    """Maintains rolling reputation for agents participating in the marketplace."""

    def __init__(self, smoothing: float = 0.2) -> None:
        self.smoothing = smoothing
        self._profiles: Dict[str, ReputationProfile] = {}
        self._outcomes: List[TaskOutcome] = []

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
            return {'agent_id': agent_id, 'trust_score': 0.0, 'tasks_completed': 0}
        return asdict(profile)

    def calculate_trust_score(self, agent_id: str) -> float:
        """Calculate a composite trust score from success and quality signals."""
        profile = self._profiles.setdefault(agent_id, ReputationProfile(agent_id=agent_id))
        latency_modifier = 1.0
        if profile.last_latency_ms is not None:
            latency_modifier = max(0.75, min(1.05, 1.0 - (profile.last_latency_ms / 100_000.0)))
        score = (profile.success_rate_ema * 0.55) + (profile.quality_ema * 0.35) + (min(profile.tasks_completed, 20) / 20.0 * 0.10)
        return round(score * latency_modifier, 4)

    def get_leaderboard(self, limit: int = 10) -> List[Dict[str, object]]:
        """Return top agents ordered by trust score."""
        ranked = sorted(self._profiles.values(), key=lambda profile: (-profile.trust_score, profile.agent_id))
        return [asdict(profile) for profile in ranked[:limit]]

    def _ema(self, current: float, observation: float) -> float:
        """Compute the next EMA value."""
        return (self.smoothing * observation) + ((1 - self.smoothing) * current)
