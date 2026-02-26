"""
Value Creation Standards & Attribution Framework
Defines value standards, tracks attribution, and optimizes pricing
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional
import logging

logger = logging.getLogger('sincor2.value_standards')

# ============================================================================
# VALUE STANDARDS BY ASSET TYPE
# ============================================================================

VALUE_STANDARDS = {
    "intelligence_report": {
        "base_price": 2500,
        "min_quality_for_delivery": 0.75,
        "premium_threshold": 0.85,
        "enterprise_threshold": 0.92,
        "target_margin": 0.75,
        "creation_cost_ratio": 0.20,
        "min_page_count": 5,
        "min_data_points": 20,
        "min_citations": 10,
        "value_indicators": {
            "market_insights": 0.30,        # 30% of value
            "actionable_recommendations": 0.40,
            "competitive_differentiation": 0.30
        }
    },
    "predictive_model": {
        "base_price": 5000,
        "min_quality_for_delivery": 0.80,
        "premium_threshold": 0.88,
        "enterprise_threshold": 0.95,
        "target_margin": 0.70,
        "creation_cost_ratio": 0.20,
        "min_accuracy": 0.75,
        "min_data_samples": 1000,
        "min_features": 20,
        "value_indicators": {
            "prediction_accuracy": 0.40,
            "roi_impact": 0.35,
            "ease_of_implementation": 0.25
        }
    },
    "custom_ai_agent": {
        "base_price": 25000,
        "min_quality_for_delivery": 0.85,
        "premium_threshold": 0.92,
        "enterprise_threshold": 0.97,
        "target_margin": 0.60,
        "creation_cost_ratio": 0.20,
        "min_capabilities": 5,
        "min_training_hours": 20,
        "min_test_scenarios": 50,
        "value_indicators": {
            "automation_hours_saved": 0.35,
            "accuracy_rate": 0.35,
            "scalability": 0.30
        }
    },
    "automation_solution": {
        "base_price": 15000,
        "min_quality_for_delivery": 0.75,
        "premium_threshold": 0.85,
        "enterprise_threshold": 0.93,
        "target_margin": 0.65,
        "creation_cost_ratio": 0.20,
        "min_processes_automated": 3,
        "min_time_saved_percent": 0.30,
        "value_indicators": {
            "time_savings": 0.40,
            "cost_reduction": 0.35,
            "error_reduction": 0.25
        }
    },
    "market_analysis": {
        "base_price": 3500,
        "min_quality_for_delivery": 0.75,
        "premium_threshold": 0.85,
        "enterprise_threshold": 0.92,
        "target_margin": 0.70,
        "creation_cost_ratio": 0.20,
        "min_markets_analyzed": 5,
        "min_competitors": 10,
        "value_indicators": {
            "market_insights": 0.40,
            "opportunity_identification": 0.35,
            "competitive_positioning": 0.25
        }
    }
}

# ============================================================================
# VALUE ATTRIBUTION TRACKING
# ============================================================================

@dataclass
class AgentContribution:
    """Agent's contribution to asset creation"""
    agent_id: str
    archetype: str  # Scout, Synthesizer, Builder, etc.
    tasks_completed: List[str] = field(default_factory=list)
    token_usage: float = 0.0
    time_contributed_minutes: float = 0.0
    insight_quality_score: float = 0.0  # 0.0-1.0 how much this agent contributed to quality
    earned_merit: float = 0.0
    revenue_attribution: float = 0.0  # $ earned from this asset

@dataclass
class ValueCreationStandard:
    """Standard for asset acceptance and value measurement"""
    asset_type: str
    min_quality_threshold: float
    required_dimensions: Dict[str, float]  # dimension -> minimum score
    value_metrics: Dict[str, float]        # metric -> weight
    pricing_model: Dict[str, float]        # multiplier config
    acceptance_criteria: Dict[str, any]    # specific requirements

class ValueAttributionEngine:
    """Tracks value creation and assigns credit to agents"""

    def __init__(self):
        self.standards = VALUE_STANDARDS
        logger.info("Value Attribution Engine initialized")

    def get_standard(self, asset_type: str) -> Optional[Dict]:
        """Retrieve value standard for asset type"""
        return self.standards.get(asset_type)

    def calculate_agent_contribution(self, asset_type: str, agents: List[AgentContribution]) -> Dict[str, float]:
        """Calculate revenue attribution per agent"""
        # Weight by: token usage (30%), time (20%), quality (50%)
        weights = {'token_usage': 0.30, 'time': 0.20, 'quality': 0.50}

        total_tokens = sum(a.token_usage for a in agents) or 1
        total_time = sum(a.time_contributed_minutes for a in agents) or 1
        total_quality = sum(a.insight_quality_score for a in agents) or 1

        attribution = {}
        for agent in agents:
            token_contrib = (agent.token_usage / total_tokens) * weights['token_usage']
            time_contrib = (agent.time_contributed_minutes / total_time) * weights['time']
            quality_contrib = (agent.insight_quality_score / total_quality) * weights['quality']

            total_contrib = token_contrib + time_contrib + quality_contrib
            attribution[agent.agent_id] = total_contrib

        return attribution

    def validate_asset_value(self, asset_type: str, quality_score: float,
                            actual_metrics: Dict[str, any]) -> tuple[bool, List[str]]:
        """Validate that asset meets value standards"""
        standard = self.get_standard(asset_type)
        if not standard:
            return True, ["No standard defined for type"]

        issues = []

        # Check quality
        if quality_score < standard["min_quality_for_delivery"]:
            issues.append(f"Quality {quality_score:.2f} below minimum {standard['min_quality_for_delivery']}")

        # Check specific metrics
        for metric, required_val in standard.get("value_indicators", {}).items():
            actual_val = actual_metrics.get(metric, 0)
            if actual_val < required_val:
                issues.append(f"{metric} {actual_val:.2f} below required {required_val}")

        return len(issues) == 0, issues

    def calculate_quality_premium(self, asset_type: str, quality_score: float) -> float:
        """Calculate price premium based on quality tier"""
        standard = self.get_standard(asset_type)
        if not standard:
            return 1.0

        base_mult = 1.0
        if quality_score >= standard["enterprise_threshold"]:
            base_mult = 1.35  # 35% premium for enterprise
        elif quality_score >= standard["premium_threshold"]:
            base_mult = 1.20  # 20% premium for premium
        elif quality_score >= standard["min_quality_for_delivery"]:
            base_mult = 1.0   # Base price
        else:
            base_mult = 0.7   # Below minimum, must be reworked

        return base_mult

    def get_required_quality_standards(self, asset_type: str) -> Dict[str, float]:
        """Get quality dimension requirements for asset type"""
        standard = self.get_standard(asset_type)
        if not standard:
            return {}

        return standard.get("required_dimensions", {})


# ============================================================================
# GLOBAL INSTANCE
# ============================================================================

value_attribution = ValueAttributionEngine()
