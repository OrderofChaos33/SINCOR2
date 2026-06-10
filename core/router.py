from __future__ import annotations

"""Task routing helpers for matching A2A work to available agents."""

import logging
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

from marketplace.discovery import CapabilityMatcher, MatchResult
from marketplace.registry import AgentCardRecord, AgentCardRegistry

if TYPE_CHECKING:
    from marketplace.reputation import ReputationEngine

logger = logging.getLogger(__name__)

# Weight applied to a trust score when blending with capability score.
# capability_score * (1 - REPUTATION_WEIGHT) + trust_score * REPUTATION_WEIGHT
REPUTATION_WEIGHT: float = 0.25


@dataclass
class RouteDecision:
    """Represents the selected route for a task."""

    task_id: str
    agent_id: str
    score: float
    matched_skills: List[str] = field(default_factory=list)
    reason: str = ''
    trust_score: float = 0.0


class TaskRouter:
    """Routes tasks according to skill fit, reputation, and lightweight load balancing."""

    DEFAULT_MAX_LOAD: float = 5.0

    def __init__(
        self,
        registry: Optional[AgentCardRegistry] = None,
        reputation: Optional["ReputationEngine"] = None,
    ) -> None:
        self.registry = registry or AgentCardRegistry()
        self.matcher = CapabilityMatcher()
        self.reputation = reputation
        self.agent_loads: Dict[str, float] = {}
        self.route_history: List[RouteDecision] = []

    def route(
        self,
        task_id: str,
        required_skills: Sequence[str],
        preferred_tags: Optional[Sequence[str]] = None,
    ) -> Optional[RouteDecision]:
        """Select the best available agent for a task using capability + reputation scores."""
        candidates = self.find_available_agents(required_skills)
        ranked = self.matcher.match(candidates, required_skills=required_skills, preferred_tags=preferred_tags)
        balanced = self.apply_load_balancing(ranked)
        if not balanced:
            return None
        reputation_boosted = self._apply_reputation(balanced)
        best = reputation_boosted[0]
        trust = self._get_trust(best.agent.agent_id)
        self.agent_loads[best.agent.agent_id] = self.agent_loads.get(best.agent.agent_id, 0.0) + 1.0
        decision = RouteDecision(
            task_id=task_id,
            agent_id=best.agent.agent_id,
            score=best.score,
            matched_skills=best.matched_skills,
            reason='capability_reputation_load_balanced',
            trust_score=trust,
        )
        self.route_history.append(decision)
        logger.debug(
            "route task=%s agent=%s capability_score=%.4f trust=%.4f",
            task_id,
            best.agent.agent_id,
            best.score,
            trust,
        )
        return decision

    def find_available_agents(self, required_skills: Sequence[str], max_load: Optional[float] = None) -> List[AgentCardRecord]:
        """Return agents whose current load does not exceed the threshold."""
        threshold = self.DEFAULT_MAX_LOAD if max_load is None else max_load
        candidates = []
        for record in self.registry.list_all():
            if self.agent_loads.get(record.agent_id, 0.0) >= threshold:
                continue
            if self.matcher.find_best_match([record], required_skills=required_skills):
                candidates.append(record)
        return candidates

    def apply_load_balancing(self, matches: Sequence[MatchResult]) -> List[MatchResult]:
        """Adjust match scores based on current agent load."""
        adjusted = []
        for match in matches:
            load_penalty = self.agent_loads.get(match.agent.agent_id, 0.0) * 0.1
            adjusted.append(MatchResult(
                agent=match.agent,
                score=round(match.score - load_penalty, 4),
                matched_skills=match.matched_skills,
                matched_tags=match.matched_tags,
            ))
        return self.matcher.rank_agents(adjusted)

    def _apply_reputation(self, matches: Sequence[MatchResult]) -> List[MatchResult]:
        """Blend capability scores with reputation trust scores."""
        if self.reputation is None:
            return list(matches)
        boosted = []
        for match in matches:
            trust = self._get_trust(match.agent.agent_id)
            blended = round(
                match.score * (1.0 - REPUTATION_WEIGHT) + trust * REPUTATION_WEIGHT,
                4,
            )
            boosted.append(MatchResult(
                agent=match.agent,
                score=blended,
                matched_skills=match.matched_skills,
                matched_tags=match.matched_tags,
            ))
        return self.matcher.rank_agents(boosted)

    def _get_trust(self, agent_id: str) -> float:
        """Return the current trust score for an agent (0.5 default for new agents)."""
        if self.reputation is None:
            return 0.5
        profile = self.reputation.get_reputation(agent_id)
        return float(profile.get('trust_score', 0.5))

    def release_load(self, agent_id: str, decrement: float = 1.0) -> None:
        """Decrement load counter after a task completes or fails."""
        current = self.agent_loads.get(agent_id, 0.0)
        self.agent_loads[agent_id] = max(0.0, current - decrement)

    def get_routing_stats(self) -> Dict[str, Any]:
        """Return routing statistics and current load distribution."""
        return {
            'routed_tasks': len(self.route_history),
            'agent_loads': dict(self.agent_loads),
            'recent_routes': [asdict(decision) for decision in self.route_history[-10:]],
            'reputation_weight': REPUTATION_WEIGHT,
        }
