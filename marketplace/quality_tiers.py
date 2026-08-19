"""Quality tiers and trust filters for production-grade volume.

Tiers: experimental → verified → production → staked.
Used by PublicDirectory and task matching to protect high-value verticals (healthcare).
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, Optional


class QualityTier(str, Enum):
    EXPERIMENTAL = "experimental"
    VERIFIED = "verified"
    PRODUCTION = "production"
    STAKED = "staked"


TIER_REQUIREMENTS: Dict[QualityTier, Dict] = {
    QualityTier.EXPERIMENTAL: {
        "min_tasks": 0,
        "min_trust": 0.0,
        "min_stake": 0,
        "description": "New or unproven agents. Eligible for activation tasks only.",
    },
    QualityTier.VERIFIED: {
        "min_tasks": 5,
        "min_trust": 0.55,
        "min_stake": 0,
        "description": "Completed successful tasks with stable outcomes. Open to most public tasks.",
    },
    QualityTier.PRODUCTION: {
        "min_tasks": 25,
        "min_trust": 0.75,
        "min_stake": 0,
        "description": "Proven track record. Eligible for healthcare and high-value verticals.",
    },
    QualityTier.STAKED: {
        "min_tasks": 10,
        "min_trust": 0.65,
        "min_stake": 500,  # SINC or AXM equivalent stake
        "description": "Staked capital + performance. Preferred for escrow-backed and high-bounty work.",
    },
}


def recommend_tier(
    tasks_completed: int,
    trust_score: float,
    sinc_staked: float = 0.0,
) -> QualityTier:
    """Deterministic tier recommendation from reputation signals."""
    if (
        tasks_completed >= TIER_REQUIREMENTS[QualityTier.STAKED]["min_tasks"]
        and trust_score >= TIER_REQUIREMENTS[QualityTier.STAKED]["min_trust"]
        and sinc_staked >= TIER_REQUIREMENTS[QualityTier.STAKED]["min_stake"]
    ):
        return QualityTier.STAKED
    if (
        tasks_completed >= TIER_REQUIREMENTS[QualityTier.PRODUCTION]["min_tasks"]
        and trust_score >= TIER_REQUIREMENTS[QualityTier.PRODUCTION]["min_trust"]
    ):
        return QualityTier.PRODUCTION
    if (
        tasks_completed >= TIER_REQUIREMENTS[QualityTier.VERIFIED]["min_tasks"]
        and trust_score >= TIER_REQUIREMENTS[QualityTier.VERIFIED]["min_trust"]
    ):
        return QualityTier.VERIFIED
    return QualityTier.EXPERIMENTAL


def can_accept_vertical(tier: QualityTier, vertical: str) -> bool:
    """Gate high-value verticals behind stronger tiers."""
    vertical = vertical.lower()
    if vertical in ("healthcare", "rcm", "credentialing", "prior-auth"):
        return tier in (QualityTier.PRODUCTION, QualityTier.STAKED)
    return True
