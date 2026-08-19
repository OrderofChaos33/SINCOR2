"""High-signal public directory for programmatic agent discovery.

Ranking: capability match × reputation/trust × inverse price × inverse latency.
Designed so external agents can query without knowing the SINCOR domain in advance.

Additive to existing DiscoveryIndex and CapabilityMatcher. Does not mutate
legacy ranking behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .discovery import CapabilityMatcher, MatchResult
from .registry import AgentCardRecord, AgentCardRegistry
from .reputation import ReputationEngine


@dataclass
class DirectoryEntry:
    """Public, machine-readable agent listing."""

    agent_id: str
    name: str
    description: str
    endpoint: str
    skills: List[str]
    tags: List[str]
    quality_tier: str = "experimental"  # experimental | verified | production | staked
    trust_score: float = 0.0
    price_per_call: float = 0.0          # in primary settlement token units
    price_currency: str = "AXM"
    sla_latency_ms: Optional[int] = None
    sla_availability: Optional[str] = None
    payment_rails: List[str] = field(default_factory=list)
    ranking_score: float = 0.0
    passport: Optional[Dict[str, Any]] = None
    raw_card: Dict[str, Any] = field(default_factory=dict)


class PublicDirectory:
    """Query surface for external agents and registries.

    Ranking formula (higher is better):
        score = capability_score * max(trust_score, 0.1)
                * (1.0 / (1.0 + normalized_price))
                * (1.0 / (1.0 + normalized_latency))
    """

    def __init__(
        self,
        registry: Optional[AgentCardRegistry] = None,
        reputation: Optional[ReputationEngine] = None,
    ) -> None:
        self.registry = registry or AgentCardRegistry()
        self.reputation = reputation or ReputationEngine()
        self.matcher = CapabilityMatcher(reputation_engine=self.reputation)

    def list(
        self,
        skill_query: Optional[str] = None,
        required_skills: Optional[Sequence[str]] = None,
        tags: Optional[Sequence[str]] = None,
        min_tier: str = "experimental",
        limit: int = 50,
    ) -> List[DirectoryEntry]:
        """Return ranked public directory entries."""
        records = self.registry.list_all()
        if skill_query:
            records = self.registry.search_by_skill(skill_query)

        required = list(required_skills or [])
        preferred = list(tags or [])

        matches: List[MatchResult] = self.matcher.match(
            records=records,
            required_skills=required or ["*"],  # allow broad when empty
            preferred_tags=preferred,
        ) if required else [
            MatchResult(agent=r, score=1.0, matched_skills=[], matched_tags=[])
            for r in records
        ]

        # Re-score with price + latency when available
        entries: List[DirectoryEntry] = []
        tier_order = {"experimental": 0, "verified": 1, "production": 2, "staked": 3}
        min_rank = tier_order.get(min_tier, 0)

        for m in matches:
            rec = m.agent
            rep = self.reputation.get_reputation(rec.agent_id)
            trust = float(rep.get("trust_score", 0.0))

            # Prefer card-level pricing; fall back to legacy sinc fields
            price = float(rec.sinc_price_per_call or 1)
            currency = "AXM"
            card = rec.raw_card or {}
            pricing = card.get("pricing") or card.get("sincPricing") or {}
            if pricing:
                price = float(pricing.get("pricePerCall") or pricing.get("amount") or price)
                currency = str(pricing.get("currency") or pricing.get("token") or "AXM")

            sla = card.get("sla") or {}
            latency = sla.get("maxLatencyMs") or rec.metadata.get("sla_latency_ms")
            availability = sla.get("availability")

            rails = card.get("paymentRails") or card.get("paymentMethodsAccepted") or ["AXM", "x402"]
            if isinstance(rails, dict):
                rails = list(rails.keys()) if rails else ["AXM"]

            tier = str(card.get("qualityTier") or "experimental").lower()
            if tier_order.get(tier, 0) < min_rank:
                continue

            # Ranking components
            cap = max(m.score, 0.1)
            trust_f = max(trust, 0.1)
            price_f = 1.0 / (1.0 + max(price, 0.0))
            lat_f = 1.0 / (1.0 + (float(latency) / 1000.0 if latency else 0.0))

            ranking = round(cap * trust_f * price_f * lat_f, 6)

            entries.append(
                DirectoryEntry(
                    agent_id=rec.agent_id,
                    name=rec.name,
                    description=rec.description,
                    endpoint=rec.endpoint,
                    skills=[s.get("id") or s.get("name") or "" for s in rec.skills],
                    tags=list(rec.tags),
                    quality_tier=tier,
                    trust_score=trust,
                    price_per_call=price,
                    price_currency=currency,
                    sla_latency_ms=int(latency) if latency is not None else None,
                    sla_availability=availability,
                    payment_rails=list(rails),
                    ranking_score=ranking,
                    passport=card.get("passport"),
                    raw_card=dict(card),
                )
            )

        entries.sort(key=lambda e: (-e.ranking_score, e.name.lower()))
        return entries[:limit]

    def to_public_json(self, entries: List[DirectoryEntry]) -> Dict[str, Any]:
        """Serialize for external consumption (registries, MCP, agents)."""
        return {
            "schemaVersion": "1.0",
            "source": "sincor-public-directory",
            "count": len(entries),
            "agents": [
                {
                    "id": e.agent_id,
                    "name": e.name,
                    "description": e.description,
                    "endpoint": e.endpoint,
                    "skills": e.skills,
                    "tags": e.tags,
                    "qualityTier": e.quality_tier,
                    "trustScore": e.trust_score,
                    "pricing": {
                        "pricePerCall": e.price_per_call,
                        "currency": e.price_currency,
                    },
                    "sla": {
                        "maxLatencyMs": e.sla_latency_ms,
                        "availability": e.sla_availability,
                    },
                    "paymentRails": e.payment_rails,
                    "rankingScore": e.ranking_score,
                    "passport": e.passport,
                }
                for e in entries
            ],
        }
