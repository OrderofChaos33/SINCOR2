from __future__ import annotations

"""Competitive intelligence and narrative tracking for the SINC ecosystem."""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List


class NarrativeTrend(Enum):
    """Narratives shaping token ecosystem competition."""

    RWA = 'rwa'
    L2_SCALING = 'l2_scaling'
    AI_AGENTS = 'ai_agents'
    DEFI_YIELD = 'defi_yield'
    NFT_UTILITY = 'nft_utility'
    PAYMENTS = 'payments'
    GAMING = 'gaming'
    GOVERNANCE = 'governance'


@dataclass
class CompetitorProfile:
    """Key metrics for a competing token ecosystem."""

    token_symbol: str
    name: str
    market_cap_usd: float
    holder_count: int
    staking_rate: float
    recent_integrations: List[str]
    narratives: List[NarrativeTrend]
    sentiment_score: float


@dataclass
class SentimentDataPoint:
    """A single sentiment observation from a tracked source."""

    source: str
    text: str
    score: float
    timestamp: str


class CompetitiveIntelEngine:
    """Scores competitor threats, narratives, and public sentiment."""

    _POSITIONING: Dict[NarrativeTrend, str] = {
        NarrativeTrend.RWA: 'SINC provides compliant settlement rails for tokenized real-world assets with built-in AML hooks.',
        NarrativeTrend.L2_SCALING: "SINC's lightweight footprint makes it an ideal settlement token for L2 and rollup ecosystems.",
        NarrativeTrend.AI_AGENTS: 'SINC equips autonomous agents with programmable treasury and settlement primitives that keep value flows auditable.',
        NarrativeTrend.DEFI_YIELD: 'SINC pairs sustainable fee routing with staking incentives to support durable DeFi yield products.',
        NarrativeTrend.NFT_UTILITY: 'SINC turns NFT activity into utility-driven commerce with staking, royalties, and cross-app rewards.',
        NarrativeTrend.PAYMENTS: 'SINC reduces payment friction with fast settlement, predictable fee rails, and treasury-aware routing.',
        NarrativeTrend.GAMING: 'SINC supports game-native economies with low-friction rewards, asset movement, and community-aligned incentives.',
        NarrativeTrend.GOVERNANCE: 'SINC links governance participation to transparent treasury execution and measurable ecosystem outcomes.',
    }

    def score_competitor_threat(self, profile: CompetitorProfile) -> float:
        """Score competitor execution threat on a normalized 0-1 scale."""

        threat = (
            ((profile.market_cap_usd / 1e9) * 0.3)
            + ((profile.holder_count / 1e6) * 0.3)
            + (profile.staking_rate * 0.2)
            + ((len(profile.recent_integrations) / 10) * 0.2)
        )
        return round(min(max(threat, 0.0), 1.0), 4)

    def detect_narrative_trends(self, profiles: List[CompetitorProfile]) -> List[NarrativeTrend]:
        """Return narrative trends appearing across multiple competitors."""

        counts: Dict[NarrativeTrend, int] = {}
        for profile in profiles:
            for trend in profile.narratives:
                counts[trend] = counts.get(trend, 0) + 1
        return [
            trend for trend, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].value)) if count > 1
        ]

    def generate_sinc_positioning(self, trend: NarrativeTrend) -> str:
        """Generate a concise SINC positioning statement for a narrative trend."""

        return self._POSITIONING[trend]

    def analyze_sentiment(self, data_points: List[SentimentDataPoint]) -> Dict[str, float | int | str]:
        """Aggregate sentiment observations into a compact summary."""

        if not data_points:
            return {
                'avg_score': 0.0,
                'positive_count': 0,
                'negative_count': 0,
                'neutral_count': 0,
                'dominant_sentiment': 'neutral',
            }

        positive_count = sum(1 for point in data_points if point.score > 0.1)
        negative_count = sum(1 for point in data_points if point.score < -0.1)
        neutral_count = len(data_points) - positive_count - negative_count
        sentiment_counts = {
            'positive': positive_count,
            'negative': negative_count,
            'neutral': neutral_count,
        }
        dominant_sentiment = sorted(sentiment_counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
        avg_score = sum(point.score for point in data_points) / len(data_points)
        return {
            'avg_score': round(avg_score, 4),
            'positive_count': positive_count,
            'negative_count': negative_count,
            'neutral_count': neutral_count,
            'dominant_sentiment': dominant_sentiment,
        }

    def generate_rebuttal_strategy(self, fud_claim: str, on_chain_metrics: Dict) -> Dict[str, object]:
        """Generate a factual response strategy for a negative market narrative."""

        rebuttal_points = []
        for key, value in list(on_chain_metrics.items())[:4]:
            metric_name = str(key).replace('_', ' ')
            rebuttal_points.append(f"{metric_name.title()} currently stands at {value}, providing measurable counter-evidence to the claim.")
        if not rebuttal_points:
            rebuttal_points.append('Share verified on-chain activity, holder retention, and treasury metrics before engaging the claim directly.')
        rebuttal_points.append('Anchor the response in verifiable dashboard links and avoid escalating into speculative debate.')
        return {
            'claim': fud_claim,
            'rebuttal_points': rebuttal_points,
            'recommended_channels': ['X', 'Discord', 'Telegram', 'governance_forum'],
            'tone': 'fact-based and transparent',
        }


__all__ = ['CompetitiveIntelEngine', 'CompetitorProfile', 'NarrativeTrend', 'SentimentDataPoint']
