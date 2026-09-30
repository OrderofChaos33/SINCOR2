"""
Adversarial Resilience & Game Theory Module (Goal #6)

Provides foundational protection against:
- Colluding agents / sybil attacks in bidding and barter
- Reputation gaming
- Coordinated low-quality task execution

Current implementation: DID-based behavior clustering + simple collusion scoring.
Future: Full game-theoretic auction mechanisms and zero-knowledge reputation proofs.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

logger = logging.getLogger("sincor.adversarial")


@dataclass
class AgentBehaviorProfile:
    agent_id: str
    did: str
    bid_win_rate: float = 0.0
    avg_quality: float = 0.5
    collusion_score: float = 0.0
    last_seen: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    peer_interactions: Set[str] = field(default_factory=set)


class AdversarialResilience:
    """Detects coordinated behavior and collusion patterns."""

    def __init__(self):
        self._profiles: Dict[str, AgentBehaviorProfile] = {}
        self._did_clusters: Dict[str, List[str]] = defaultdict(list)  # did -> list of agent_ids

    def record_interaction(self, agent_id: str, did: str, peer_id: Optional[str] = None,
                           quality: float = 0.5, won_bid: bool = False):
        profile = self._profiles.setdefault(agent_id, AgentBehaviorProfile(agent_id=agent_id, did=did))
        profile.last_seen = datetime.now(timezone.utc).isoformat()

        if peer_id:
            profile.peer_interactions.add(peer_id)

        # Update simple metrics
        if won_bid:
            profile.bid_win_rate = min(1.0, profile.bid_win_rate + 0.05)
        profile.avg_quality = (profile.avg_quality * 0.9) + (quality * 0.1)

        # Cluster by DID (basic sybil detection)
        if did not in self._did_clusters:
            self._did_clusters[did] = []
        if agent_id not in self._did_clusters[did]:
            self._did_clusters[did].append(agent_id)

    def get_collusion_score(self, agent_id: str) -> float:
        profile = self._profiles.get(agent_id)
        if not profile:
            return 0.0

        # Simple heuristic: high interaction with same DID cluster + high win rate
        cluster_size = len(self._did_clusters.get(profile.did, []))
        collusion = 0.0
        if cluster_size > 2:
            collusion += 0.3 * min(cluster_size / 5, 1.0)
        if profile.bid_win_rate > 0.7 and len(profile.peer_interactions) < 5:
            collusion += 0.4
        return min(1.0, collusion)

    def should_penalize(self, agent_id: str, threshold: float = 0.6) -> bool:
        return self.get_collusion_score(agent_id) > threshold

    def get_cluster_for_did(self, did: str) -> List[str]:
        return self._did_clusters.get(did, [])


# Singleton
_adversarial_engine: Optional[AdversarialResilience] = None


def get_adversarial_engine() -> AdversarialResilience:
    global _adversarial_engine
    if _adversarial_engine is None:
        _adversarial_engine = AdversarialResilience()
    return _adversarial_engine
