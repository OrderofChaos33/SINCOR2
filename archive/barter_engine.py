"""
Agent-to-Agent Barter & Non-Monetary Exchange Engine (Goal #3)

Extends SINCOR2 marketplace beyond pure monetary (AXM + fiat) to support:
- Compute-for-data exchanges
- Skill-for-skill barter ("I'll do your lead enrichment if you do my compliance audit")
- Data-for-access trades (subscribe to my real-time feed, get discounted task rates)

This enables emergent specialization that pure pricing cannot capture.
Integrates with:
- marketplace/settlement.py (hybrid monetary + barter settlements)
- swarm_coordination.py (barter bids in contract-net)
- reputation.py (barter reputation tracked separately or composite)
- dae/incentives.py (non-monetary incentive accounting)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Any


class BarterType(str, Enum):
    COMPUTE_FOR_DATA = "compute_for_data"
    SKILL_FOR_SKILL = "skill_for_skill"
    DATA_FOR_ACCESS = "data_for_access"
    SERVICE_FOR_CREDIT = "service_for_credit"  # future: reputation credits


@dataclass
class BarterOffer:
    """A non-monetary offer posted to the barter marketplace."""
    offer_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    offerer_id: str
    offer_type: BarterType
    offered: Dict[str, Any]  # e.g. {"skill": "lead-enrichment", "volume": 100}
    requested: Dict[str, Any]  # e.g. {"skill": "compliance-audit", "volume": 1}
    expires_at: Optional[str] = None
    status: str = "open"  # open, matched, completed, expired
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class BarterMatch:
    """A matched barter exchange between two agents."""
    match_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    offer_id: str
    acceptor_id: str
    terms: Dict[str, Any]
    fulfillment_status: str = "pending"  # pending, partial, completed, disputed
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = None


class BarterEngine:
    """Core engine for non-monetary agent exchanges in the ultimate SINCOR2 ecosystem.

    Agents can post offers, discover matches via capability overlap + reputation,
    and execute barter without AXM/fiat moving (tracked in ledger for reputation/DAE).
    """

    def __init__(self):
        self._offers: Dict[str, BarterOffer] = {}
        self._matches: Dict[str, BarterMatch] = {}
        self._ledgers: Dict[str, List[Dict]] = {}  # agent_id -> barter history

    def post_offer(
        self,
        offerer_id: str,
        offer_type: BarterType,
        offered: Dict[str, Any],
        requested: Dict[str, Any],
        expires_hours: int = 72,
    ) -> BarterOffer:
        """Post a new barter offer."""
        from datetime import timedelta
        expires = (datetime.now(timezone.utc) + timedelta(hours=expires_hours)).isoformat()
        offer = BarterOffer(
            offerer_id=offerer_id,
            offer_type=offer_type,
            offered=offered,
            requested=requested,
            expires_at=expires,
        )
        self._offers[offer.offer_id] = offer
        return offer

    def find_matches(self, agent_id: str, min_reputation: float = 0.6) -> List[Dict[str, Any]]:
        """Semantic + reputation-weighted match discovery for barter opportunities.

        In ultimate version: use capability embeddings + cross-marketplace rep (Goal #4).
        """
        matches = []
        for offer in self._offers.values():
            if offer.status != "open" or offer.offerer_id == agent_id:
                continue
            # Simple overlap scoring (upgrade with real embeddings later)
            score = self._calculate_overlap(offer.requested, {"agent_skills": ["*"]})  # placeholder
            if score > 0.3:
                matches.append({
                    "offer_id": offer.offer_id,
                    "offerer": offer.offerer_id,
                    "type": offer.offer_type.value,
                    "offered": offer.offered,
                    "requested": offer.requested,
                    "match_score": round(score, 3),
                })
        return sorted(matches, key=lambda x: -x["match_score"])[:20]

    def accept_barter(self, offer_id: str, acceptor_id: str) -> Optional[BarterMatch]:
        """Accept an open offer and create a match."""
        offer = self._offers.get(offer_id)
        if not offer or offer.status != "open":
            return None
        match = BarterMatch(
            offer_id=offer_id,
            acceptor_id=acceptor_id,
            terms={
                "offered": offer.offered,
                "requested": offer.requested,
                "type": offer.offer_type.value,
            },
        )
        self._matches[match.match_id] = match
        offer.status = "matched"
        # Record in both ledgers
        self._record_ledger(offer.offerer_id, match)
        self._record_ledger(acceptor_id, match)
        return match

    def complete_barter(self, match_id: str, outcome: Dict[str, Any]) -> bool:
        """Mark barter complete and update reputation/DAE incentives."""
        match = self._matches.get(match_id)
        if not match or match.fulfillment_status != "pending":
            return False
        match.fulfillment_status = "completed"
        match.completed_at = datetime.now(timezone.utc).isoformat()
        # TODO: hook into reputation.py reward_barter_reputation() and dae/incentives.py
        return True

    def get_agent_barter_history(self, agent_id: str) -> List[Dict[str, Any]]:
        return self._ledgers.get(agent_id, [])

    def _calculate_overlap(self, requested: Dict, agent_profile: Dict) -> float:
        """Placeholder semantic overlap. In prod: use sentence-transformers or SINAX embeddings."""
        # Very naive for now
        req_skills = str(requested).lower()
        return 0.5 if any(k in req_skills for k in ["skill", "data", "compute"]) else 0.2

    def _record_ledger(self, agent_id: str, match: BarterMatch):
        if agent_id not in self._ledgers:
            self._ledgers[agent_id] = []
        self._ledgers[agent_id].append(asdict(match))


# Singleton
_barter_engine: Optional[BarterEngine] = None


def get_barter_engine() -> BarterEngine:
    global _barter_engine
    if _barter_engine is None:
        _barter_engine = BarterEngine()
    return _barter_engine
