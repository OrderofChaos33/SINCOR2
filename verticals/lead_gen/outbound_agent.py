from __future__ import annotations

"""Lead enrichment, scoring, and outreach orchestration helpers."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List
from uuid import uuid4

# Scoring configuration constants — adjust to match ICP and market segment.
_REVENUE_SCALE: float = 1_000_000    # Normalise annual revenue to a 0–10 range
_SIZE_SCALE: int = 25                 # Normalise headcount to a 0–10 range
_REVENUE_WEIGHT: float = 0.4
_SIZE_WEIGHT: float = 0.3
_INDUSTRY_BONUS_MATCH: float = 3.0
_INDUSTRY_BONUS_DEFAULT: float = 1.0
_ENRICHMENT_BONUS: float = 2.0
_PRIORITY_INDUSTRIES = frozenset({'healthcare', 'finance', 'software'})


@dataclass
class Lead:
    """Represents a prospect with enrichment and scoring attributes."""

    lead_id: str
    company_name: str
    contact_name: str
    email: str
    industry: str
    employee_count: int
    annual_revenue: float
    enrichment: Dict[str, object] = field(default_factory=dict)
    score: float = 0.0


@dataclass
class OutreachSequence:
    """Represents a multi-step outbound sequence."""

    sequence_id: str
    channel_steps: List[Dict[str, str]]
    objective: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class LeadGenAgent:
    """Automates enrichment, scoring, messaging, and engagement tracking."""

    def enrich_lead(self, lead: Lead) -> Lead:
        """Attach basic firmographic and intent metadata to a lead."""
        lead.enrichment.update({
            'tech_stack': ['HubSpot', 'Slack'],
            'hiring_signal': lead.employee_count > 25,
            'intent_topic': f'{lead.industry} automation',
        })
        return lead

    def score_lead(self, lead: Lead) -> float:
        """Score a lead using simple fit and buying-capacity heuristics."""
        revenue_score = min(lead.annual_revenue / _REVENUE_SCALE, 10)
        size_score = min(lead.employee_count / _SIZE_SCALE, 10)
        industry_bonus = _INDUSTRY_BONUS_MATCH if lead.industry.lower() in _PRIORITY_INDUSTRIES else _INDUSTRY_BONUS_DEFAULT
        lead.score = round(
            (revenue_score * _REVENUE_WEIGHT)
            + (size_score * _SIZE_WEIGHT)
            + industry_bonus
            + (_ENRICHMENT_BONUS if lead.enrichment else 0),
            2,
        )
        return lead.score

    def generate_outreach(self, lead: Lead, value_prop: str) -> OutreachSequence:
        """Create a personalized multichannel outreach sequence."""
        sequence = OutreachSequence(
            sequence_id=f"seq-{uuid4().hex[:10]}",
            objective='book_discovery_call',
            channel_steps=[
                {
                    'day': '1',
                    'channel': 'email',
                    'message': (
                        f"Subject: {lead.company_name} and {value_prop}\n"
                        f"Hi {lead.contact_name}, I noticed {lead.company_name} is active in {lead.industry}. "
                        f"We help teams accelerate {value_prop}."
                    )
                },
                {
                    'day': '3',
                    'channel': 'linkedin',
                    'message': f"Shared a short note on how teams like {lead.company_name} reduce manual work using SINCOR2 orchestration."
                },
                {
                    'day': '6',
                    'channel': 'email',
                    'message': f"Following up with a concrete workflow idea for {lead.company_name}: {value_prop}."
                },
            ],
        )
        return sequence

    def track_engagement(self, lead_id: str, opened: bool, replied: bool, clicked: bool) -> Dict[str, object]:
        """Return a normalized engagement summary for campaign monitoring."""
        score = (2 if opened else 0) + (5 if clicked else 0) + (10 if replied else 0)
        return {
            'lead_id': lead_id,
            'engagement_score': score,
            'stage': 'engaged' if score >= 7 else 'nurture',
            'signals': {'opened': opened, 'replied': replied, 'clicked': clicked},
        }
