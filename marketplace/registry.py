from __future__ import annotations

"""Agent Card registry with in-memory and file-backed persistence."""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union


@dataclass
class AgentCardRecord:
    """Normalized registry record for an A2A Agent Card."""

    agent_id: str
    name: str
    description: str
    version: str
    endpoint: str
    skills: List[Dict[str, Any]] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    provider: Dict[str, Any] = field(default_factory=dict)
    capabilities: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    security: List[Dict[str, Any]] = field(default_factory=list)
    raw_card: Dict[str, Any] = field(default_factory=dict)
    # SINC token pricing
    sinc_price_per_call: int = 1       # SINC per individual agent invocation
    sinc_price_per_minute: int = 0     # SINC per runtime minute (0 = not metered by time)
    sinc_stake_required: int = 250     # Minimum SINC staked to list in marketplace

    @classmethod
    def from_agent_card(cls, card: Dict[str, Any]) -> 'AgentCardRecord':
        """Create a registry record from an A2A Agent Card payload."""
        interfaces = card.get('supportedInterfaces', [])
        endpoint = interfaces[0].get('url', '') if interfaces else ''
        skills = list(card.get('skills', []))
        tags = sorted({tag for skill in skills for tag in skill.get('tags', [])})
        agent_id = card.get('id') or card.get('name', 'agent').lower().replace(' ', '-')

        # Parse optional SINC pricing block from agent card
        sinc_pricing = card.get('sincPricing', {})
        try:
            sinc_price_per_call = int(sinc_pricing.get('pricePerCall', 1))
        except (TypeError, ValueError):
            sinc_price_per_call = 1
        try:
            sinc_price_per_minute = int(sinc_pricing.get('pricePerMinute', 0))
        except (TypeError, ValueError):
            sinc_price_per_minute = 0
        try:
            sinc_stake_required = int(sinc_pricing.get('stakeRequired', 250))
        except (TypeError, ValueError):
            sinc_stake_required = 250

        return cls(
            agent_id=agent_id,
            name=card.get('name', agent_id),
            description=card.get('description', ''),
            version=card.get('version', '0.0.0'),
            endpoint=endpoint,
            skills=skills,
            tags=tags,
            provider=dict(card.get('provider', {})),
            capabilities=dict(card.get('capabilities', {})),
            metadata={
                'documentationUrl': card.get('documentationUrl'),
                'defaultInputModes': card.get('defaultInputModes', []),
                'defaultOutputModes': card.get('defaultOutputModes', []),
            },
            security=list(card.get('security', [])),
            raw_card=dict(card),
            sinc_price_per_call=sinc_price_per_call,
            sinc_price_per_minute=sinc_price_per_minute,
            sinc_stake_required=sinc_stake_required,
        )


class AgentCardRegistry:
    """Stores and queries Agent Cards for marketplace discovery."""

    def __init__(self, storage_path: Optional[Union[str, Path]] = None) -> None:
        self.storage_path = Path(storage_path) if storage_path else Path('marketplace/agent_cards.json')
        self._records: Dict[str, AgentCardRecord] = {}
        self._load()

    def _load(self) -> None:
        """Load persisted registry state if it exists."""
        if not self.storage_path.exists():
            return
        payload = json.loads(self.storage_path.read_text(encoding='utf-8'))
        for record in payload.get('agents', []):
            self._records[record['agent_id']] = AgentCardRecord(**record)

    def _persist(self) -> None:
        """Persist registry contents to disk."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {'agents': [asdict(record) for record in self._records.values()]}
        self.storage_path.write_text(json.dumps(payload, indent=2) + "\n", encoding='utf-8')

    def register(self, card: Union[AgentCardRecord, Dict[str, Any]]) -> AgentCardRecord:
        """Register or update an Agent Card."""
        record = card if isinstance(card, AgentCardRecord) else AgentCardRecord.from_agent_card(card)
        self._records[record.agent_id] = record
        self._persist()
        return record

    def get(self, agent_id: str) -> Optional[AgentCardRecord]:
        """Return a registry record by agent identifier."""
        return self._records.get(agent_id)

    def search_by_skill(self, skill_query: str) -> List[AgentCardRecord]:
        """Search for agents by skill id, name, description, or tags."""
        normalized = skill_query.strip().lower()
        matches = []
        for record in self._records.values():
            for skill in record.skills:
                haystack = ' '.join([
                    skill.get('id', ''),
                    skill.get('name', ''),
                    skill.get('description', ''),
                    ' '.join(skill.get('tags', [])),
                ]).lower()
                if normalized in haystack:
                    matches.append(record)
                    break
        return matches

    def list_all(self) -> List[AgentCardRecord]:
        """Return all registered Agent Cards sorted by name."""
        return sorted(self._records.values(), key=lambda record: record.name.lower())

    def deregister(self, agent_id: str) -> bool:
        """Remove an Agent Card from the registry."""
        removed = self._records.pop(agent_id, None)
        if removed is not None:
            self._persist()
        return removed is not None

    def to_json(self) -> str:
        """Serialize the registry to JSON."""
        return json.dumps({'agents': [asdict(record) for record in self.list_all()]}, indent=2)
