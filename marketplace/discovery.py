from __future__ import annotations

"""Capability matching and lightweight discovery indexing.

Extended to integrate SINC-staked reputation scoring:
- :class:`CapabilityMatcher` accepts an optional :class:`~marketplace.reputation.ReputationEngine`
  and boosts match scores with each agent's composite trust score (which is
  already SINC-stake-weighted inside the engine).
- :func:`match_agents` is the top-level convenience function used by the router.
"""

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Set

from .registry import AgentCardRecord


@dataclass
class MatchResult:
    """Represents a scored match between a task request and an agent."""

    agent: AgentCardRecord
    score: float
    matched_skills: List[str]
    matched_tags: List[str]
    trust_score: float = 0.0
    sinc_staked: float = 0.0


class DiscoveryIndex:
    """Provides full-text and tag search over Agent Card records."""

    def __init__(self) -> None:
        self._records: Dict[str, AgentCardRecord] = {}
        self._token_index: Dict[str, Set[str]] = {}
        self._tag_index: Dict[str, Set[str]] = {}

    def index_cards(self, records: Iterable[AgentCardRecord]) -> None:
        """Index a collection of Agent Card records."""
        for record in records:
            self._records[record.agent_id] = record
            tokens = self._tokenize(record)
            for token in tokens:
                self._token_index.setdefault(token, set()).add(record.agent_id)
            for tag in record.tags:
                self._tag_index.setdefault(tag.lower(), set()).add(record.agent_id)

    def search_full_text(self, query: str) -> List[AgentCardRecord]:
        """Search records by tokenized free text query."""
        tokens = [token for token in query.lower().split() if token]
        if not tokens:
            return list(self._records.values())
        candidate_ids: Optional[Set[str]] = None
        for token in tokens:
            token_matches = self._token_index.get(token, set())
            candidate_ids = token_matches if candidate_ids is None else candidate_ids & token_matches
        return [self._records[agent_id] for agent_id in sorted(candidate_ids or set())]

    def search_by_tag(self, tag: str) -> List[AgentCardRecord]:
        """Return records that advertise a given tag."""
        return [self._records[agent_id] for agent_id in sorted(self._tag_index.get(tag.lower(), set()))]

    def _tokenize(self, record: AgentCardRecord) -> Set[str]:
        fragments = [record.name, record.description, ' '.join(record.tags)]
        for skill in record.skills:
            fragments.extend([skill.get('id', ''), skill.get('name', ''), skill.get('description', '')])
            fragments.extend(skill.get('tags', []))
        tokens = set()
        for fragment in fragments:
            for token in fragment.lower().replace('/', ' ').replace('-', ' ').split():
                if token:
                    tokens.add(token)
        return tokens


class CapabilityMatcher:
    """Ranks agents based on required skills, preferred tags, reputation, and SINC stake.

    When a :class:`~marketplace.reputation.ReputationEngine` is provided,
    each agent's capability match score is multiplied by its composite trust
    score (which already incorporates SINC staking).  This means agents that
    stake more SINC *and* perform well rise to the top of the routing list.
    """

    def __init__(self, reputation_engine=None) -> None:
        # Avoid hard import cycle; accept None and import lazily if needed
        self._reputation_engine = reputation_engine

    def match(
        self,
        records: Sequence[AgentCardRecord],
        required_skills: Sequence[str],
        preferred_tags: Optional[Sequence[str]] = None,
    ) -> List[MatchResult]:
        """Score agents against required skills, optional tags, and reputation.

        Parameters
        ----------
        records:
            Candidate agent records to score.
        required_skills:
            Skills the task requires (all must match).
        preferred_tags:
            Optional tags that boost the score by 0.25 each.

        Returns
        -------
        List[MatchResult]
            Sorted list (highest score first) of matching agents.
        """
        preferred_tags = preferred_tags or []
        required = [skill.lower() for skill in required_skills]
        preferred = [tag.lower() for tag in preferred_tags]
        results: List[MatchResult] = []
        for record in records:
            matched_skill_ids = []
            matched_tags = []
            score = 0.0
            for skill in record.skills:
                skill_blob = ' '.join([
                    skill.get('id', ''),
                    skill.get('name', ''),
                    skill.get('description', ''),
                    ' '.join(skill.get('tags', [])),
                ]).lower()
                for requirement in required:
                    if requirement in skill_blob and requirement not in matched_skill_ids:
                        matched_skill_ids.append(requirement)
                        score += 1.0
                for tag in preferred:
                    if tag in skill.get('tags', []) or tag in record.tags:
                        if tag not in matched_tags:
                            matched_tags.append(tag)
                            score += 0.25
            if required and len(matched_skill_ids) < len(required):
                continue
            score += float(record.capabilities.get('stateTransitionHistory', False)) * 0.1

            # Incorporate SINC-weighted reputation score
            trust_score = 0.0
            sinc_staked = 0.0
            if self._reputation_engine is not None:
                rep = self._reputation_engine.get_reputation(record.agent_id)
                trust_score = float(rep.get('trust_score', 0.0))
                sinc_staked = float(rep.get('sinc_staked', 0.0))
                # Multiply capability score by trust; ensures staking raises rank
                score = round(score * max(trust_score, 0.1), 4)

            results.append(MatchResult(
                agent=record,
                score=round(score, 4),
                matched_skills=matched_skill_ids,
                matched_tags=matched_tags,
                trust_score=trust_score,
                sinc_staked=sinc_staked,
            ))
        return self.rank_agents(results)

    def rank_agents(self, matches: Sequence[MatchResult]) -> List[MatchResult]:
        """Return matches sorted by descending score and name."""
        return sorted(matches, key=lambda match: (-match.score, match.agent.name.lower()))

    def find_best_match(
        self,
        records: Sequence[AgentCardRecord],
        required_skills: Sequence[str],
        preferred_tags: Optional[Sequence[str]] = None,
    ) -> Optional[MatchResult]:
        """Return the highest-scoring agent match, if any."""
        ranked = self.match(records=records, required_skills=required_skills, preferred_tags=preferred_tags)
        return ranked[0] if ranked else None


def match_agents(
    records: Sequence[AgentCardRecord],
    required_skills: Sequence[str],
    preferred_tags: Optional[Sequence[str]] = None,
    reputation_engine=None,
) -> List[MatchResult]:
    """Convenience wrapper: score and rank *records* for the given skill requirements.

    Parameters
    ----------
    records:
        Agent Card records to consider.
    required_skills:
        Skills that a matching agent must support.
    preferred_tags:
        Optional tags for secondary scoring.
    reputation_engine:
        Optional :class:`~marketplace.reputation.ReputationEngine` to enable
        SINC-weighted trust scoring.

    Returns
    -------
    List[MatchResult]
        Ranked matches (best first).
    """
    matcher = CapabilityMatcher(reputation_engine=reputation_engine)
    return matcher.match(records=records, required_skills=required_skills, preferred_tags=preferred_tags)
