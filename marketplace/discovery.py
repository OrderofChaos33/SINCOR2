from __future__ import annotations

"""Capability matching and lightweight discovery indexing."""

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
    """Ranks agents based on required skills, preferred tags, and metadata signals."""

    def match(
        self,
        records: Sequence[AgentCardRecord],
        required_skills: Sequence[str],
        preferred_tags: Optional[Sequence[str]] = None,
    ) -> List[MatchResult]:
        """Score agents against required skills and optional tag preferences."""
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
            results.append(MatchResult(agent=record, score=round(score, 4), matched_skills=matched_skill_ids, matched_tags=matched_tags))
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
