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
    # ==== INTELLIGENCE REPORTS ====
    # Industry Average: $8,500-$12,000 | 5% Below: $8,075-$11,400
    # Strategic Positioning: Competitive on quality, premium on speed
    "intelligence_report": {
        # Tiered pricing based on complexity
        "base_price": 9750,  # Mid-market competitive rate (5% below $10,263 average)
        "pricing_tiers": {
            "simple": 4500,      # Quick market overview (5% below $4,737)
            "standard": 9750,    # Standard competitive analysis (5% below $10,263)
            "comprehensive": 22000,  # Full-scope strategic analysis (5% below $23,158)
        },
        "delivery_multipliers": {
            "standard": 1.0,     # 3-4 weeks (reference)
            "expedited": 1.50,   # 1-2 weeks (+50% premium)
            "extended": 0.85,    # 6-8 weeks (-15% discount)
        },
        "min_quality_for_delivery": 0.75,
        "premium_threshold": 0.85,
        "enterprise_threshold": 0.92,
        "target_margin": 0.75,
        "creation_cost_ratio": 0.20,
        "min_page_count": 5,
        "min_data_points": 20,
        "min_citations": 10,
        "value_indicators": {
            "market_insights": 0.30,
            "actionable_recommendations": 0.40,
            "competitive_differentiation": 0.30
        }
    },
    # ==== PREDICTIVE ANALYTICS MODELS ====
    # Industry Average: $18,000-$30,000 | 5% Below: $17,100-$28,500
    # Strategic Positioning: Premium accuracy, lower price point
    "predictive_model": {
        "base_price": 22750,  # Mid-market competitive rate (5% below $23,947 average)
        "pricing_tiers": {
            "simple": 8500,      # Single regression model (5% below $8,947)
            "standard": 22750,   # Multi-variable forecasting (5% below $23,947)
            "complex": 47500,    # Ensemble with real-time pipeline (5% below $50,000)
        },
        "delivery_multipliers": {
            "standard": 1.0,     # 4-8 weeks (reference)
            "expedited": 1.40,   # 2-3 weeks (+40% premium)
            "extended": 0.80,    # 10-12 weeks with on-going optimization (-20%)
        },
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
    # ==== CUSTOM AI AGENTS ====
    # Industry Average: $20,000-$45,000 | 5% Below: $19,000-$42,750
    # Strategic Positioning: Full-featured at competitive prices
    "custom_ai_agent": {
        "base_price": 30875,  # Mid-market competitive rate (5% below $32,500 average)
        "pricing_tiers": {
            "simple": 7125,      # Single-purpose chatbot (5% below $7,500)
            "standard": 30875,   # Multi-purpose agent (5% below $32,500)
            "enterprise": 127500, # Full AI agent suite (5% below $134,210)
        },
        "delivery_multipliers": {
            "standard": 1.0,     # 6-12 weeks (reference)
            "expedited": 1.35,   # 3-4 weeks (+35% premium)
            "extended": 0.75,    # 4-6 months with inc optimization (-25%)
        },
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
    # ==== AUTOMATION SOLUTIONS ====
    # Industry Average: $20,000-$40,000 | 5% Below: $19,000-$38,000
    # Strategic Positioning: Enterprise-grade automation at competitive rates
    "automation_solution": {
        "base_price": 28500,  # Mid-market competitive rate (5% below $30,000 average)
        "pricing_tiers": {
            "simple": 6800,      # Single simple process (5% below $7,158)
            "standard": 28500,   # 3-8 processes, moderate complexity (5% below $30,000)
            "enterprise": 95000, # 20+ processes, complex integrations (5% below $100,000)
        },
        "delivery_multipliers": {
            "standard": 1.0,     # 4-10 weeks (reference)
            "expedited": 1.45,   # 2-3 weeks (+45% premium)
            "extended": 0.80,    # 3-6 months with optimization (-20%)
        },
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
    # ==== MARKET ANALYSIS ====
    # Industry Average: $12,000-$25,000 | 5% Below: $11,400-$23,750
    # Strategic Positioning: Primary research included at competitive price
    "market_analysis": {
        "base_price": 17575,  # Mid-market competitive rate (5% below $18,500 average)
        "pricing_tiers": {
            "simple": 5700,      # Market overview (5% below $6,000)
            "standard": 17575,   # Comprehensive analysis with competitors (5% below $18,500)
            "premium": 47500,    # Deep-dive with primary research (5% below $50,000)
        },
        "delivery_multipliers": {
            "standard": 1.0,     # 3-8 weeks (reference)
            "expedited": 1.50,   # 1-2 weeks (+50% premium)
            "extended": 0.80,    # 8-16 weeks + quarterly updates (-20%)
        },
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
# STRATEGIC PRICING MULTIPLIERS & DISCOUNTS
# ============================================================================
STRATEGIC_PRICING_CONFIG = {
    "bundle_discounts": {
        "2_services": 0.10,      # 10% off when buying 2+ services
        "3_services": 0.15,      # 15% off when buying 3+ services
        "annual_contract": 0.20, # 20% off for annual prepaid contracts
    },
    "volume_discounts": {
        "3_assets": 0.10,        # 10% off for 3+ assets
        "5_assets": 0.15,        # 15% off for 5+ assets
        "10_assets": 0.25,       # 25% off for 10+ assets
    },
    "startup_discount": 0.30,    # 30% off for qualifying startups
    "nonprofit_discount": 0.25,  # 25% off for nonprofits
    "loyalty_multiplier": {
        "repeat_customer": 1.05,  # +5% on premium for loyal clients
        "referral_reward": 0.10,  # 10% credit for referrals
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

    def calculate_tiered_pricing(self, asset_type: str, complexity: str = "standard",
                                 delivery_speed: str = "standard",
                                 bundle_count: int = 1) -> float:
        """
        Calculate pricing with tiered approach + delivery multipliers + volume discounts

        Args:
            asset_type: Type of asset
            complexity: 'simple', 'standard', or 'complex'
            delivery_speed: 'standard', 'expedited', or 'extended'
            bundle_count: Number of services bundled together

        Returns:
            Final price with all multipliers applied
        """
        standard = self.get_standard(asset_type)
        if not standard:
            return 0.0

        # Step 1: Get base price from tier
        pricing_tiers = standard.get("pricing_tiers", {})
        base_price = pricing_tiers.get(complexity, standard["base_price"])

        # Step 2: Apply delivery speed multiplier
        delivery_mults = standard.get("delivery_multipliers", {"standard": 1.0})
        delivery_mult = delivery_mults.get(delivery_speed, 1.0)
        price_with_delivery = base_price * delivery_mult

        # Step 3: Apply bundle discount
        bundle_discount = self._get_bundle_discount(bundle_count)
        final_price = price_with_delivery * (1 - bundle_discount)

        return round(final_price, 2)

    def _get_bundle_discount(self, bundle_count: int) -> float:
        """Get bundle discount percentage based on bundle size"""
        if bundle_count < 2:
            return 0.0
        elif bundle_count == 2:
            return STRATEGIC_PRICING_CONFIG["bundle_discounts"]["2_services"]
        elif bundle_count >= 3:
            return STRATEGIC_PRICING_CONFIG["bundle_discounts"]["3_services"]
        return 0.0

    def calculate_with_multipliers(self, asset_type: str, base_quality_score: float,
                                   market_demand: float = 1.0,
                                   customer_segment: str = "standard") -> Dict[str, float]:
        """
        Calculate final price with ALL multipliers (quality, demand, segment)

        Returns:
            {
                'base_price': float,
                'quality_multiplier': float,
                'demand_multiplier': float,
                'segment_discount': float,
                'final_price': float
            }
        """
        standard = self.get_standard(asset_type)
        if not standard:
            return {}

        base_price = standard["base_price"]

        # Quality premium multiplier
        quality_mult = self.calculate_quality_premium(asset_type, base_quality_score)
        price_with_quality = base_price * quality_mult

        # Market demand multiplier (0.5x to 3.0x)
        demand_mult = max(0.5, min(3.0, market_demand))
        price_with_demand = price_with_quality * demand_mult

        # Customer segment discount (startup, nonprofit, etc.)
        segment_discount = self._get_segment_discount(customer_segment)
        final_price = price_with_demand * (1 - segment_discount)

        return {
            'base_price': base_price,
            'quality_multiplier': quality_mult,
            'quality_score': base_quality_score,
            'demand_multiplier': demand_mult,
            'segment_discount': segment_discount,
            'final_price': round(final_price, 2),
            'margin_at_20_percent_cost': round((final_price - base_price * 0.20), 2)
        }

    def _get_segment_discount(self, customer_segment: str) -> float:
        """Get discount for customer segment"""
        if customer_segment.lower() == "startup":
            return STRATEGIC_PRICING_CONFIG["startup_discount"]
        elif customer_segment.lower() == "nonprofit":
            return STRATEGIC_PRICING_CONFIG["nonprofit_discount"]
        return 0.0

    def get_volume_discount(self, asset_count: int) -> float:
        """Get volume discount for multiple asset purchase"""
        config = STRATEGIC_PRICING_CONFIG["volume_discounts"]
        if asset_count >= 10:
            return config["10_assets"]
        elif asset_count >= 5:
            return config["5_assets"]
        elif asset_count >= 3:
            return config["3_assets"]
        return 0.0


# ============================================================================
# GLOBAL INSTANCE
# ============================================================================

value_attribution = ValueAttributionEngine()
