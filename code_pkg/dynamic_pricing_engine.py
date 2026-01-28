"""
SINCOR Dynamic Pricing Engine
Automatically adjusts pricing based on complexity, market demand, and agent availability
"""

import asyncio
import json
import time
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import numpy as np
from enum import Enum

class ComplexityLevel(Enum):
    TRIVIAL = 1
    SIMPLE = 2
    MODERATE = 3
    COMPLEX = 4
    CRITICAL = 5
    ENTERPRISE = 6

class MarketCondition(Enum):
    LOW_DEMAND = 0.5
    NORMAL = 1.0
    HIGH_DEMAND = 1.5
    PEAK_DEMAND = 2.0
    EMERGENCY = 3.0

@dataclass
class TaskMetrics:
    complexity_score: float
    estimated_duration: float
    required_agents: int
    expertise_level: str
    historical_success_rate: float
    market_urgency: float
    client_tier: str

@dataclass
class MarketData:
    current_demand: float
    agent_utilization: float
    competitor_pricing: Dict[str, float]
    market_volatility: float
    seasonal_multiplier: float
    timestamp: datetime

@dataclass
class PricingResult:
    base_price: float
    complexity_multiplier: float
    demand_multiplier: float
    urgency_multiplier: float
    final_price: float
    confidence_score: float
    explanation: str

class DynamicPricingEngine:
    def __init__(self):
        self.base_pricing_matrix = {
            ComplexityLevel.TRIVIAL: 50,
            ComplexityLevel.SIMPLE: 150,
            ComplexityLevel.MODERATE: 500,
            ComplexityLevel.COMPLEX: 1500,
            ComplexityLevel.CRITICAL: 5000,
            ComplexityLevel.ENTERPRISE: 15000
        }
        
        self.market_conditions = MarketCondition.NORMAL
        self.pricing_history: List[Dict] = []
        self.market_intelligence = {}
        self.agent_capacity_tracker = {}
        
        # Advanced pricing parameters
        self.surge_pricing_threshold = 0.85
        self.discount_threshold = 0.3
        self.maximum_surge_multiplier = 4.0
        self.minimum_discount_multiplier = 0.6
        
        # Client tier multipliers
        self.client_tier_multipliers = {
            'startup': 0.7,
            'small_business': 0.85,
            'mid_market': 1.0,
            'enterprise': 1.3,
            'fortune_500': 1.8
        }
        
    async def calculate_dynamic_price(self, task_metrics: TaskMetrics) -> PricingResult:
        """Calculate optimal price based on multiple market factors"""
        
        # Step 1: Determine base price from complexity
        complexity_level = self._assess_complexity_level(task_metrics.complexity_score)
        base_price = self.base_pricing_matrix[complexity_level]
        
        # Step 2: Apply complexity refinement
        complexity_multiplier = self._calculate_complexity_multiplier(task_metrics)
        
        # Step 3: Apply market demand multiplier
        demand_multiplier = await self._calculate_demand_multiplier()
        
        # Step 4: Apply urgency multiplier
        urgency_multiplier = self._calculate_urgency_multiplier(task_metrics.market_urgency)
        
        # Step 5: Apply client tier adjustment
        client_multiplier = self.client_tier_multipliers.get(task_metrics.client_tier, 1.0)
        
        # Step 6: Apply success rate adjustment
        success_rate_multiplier = self._calculate_success_rate_multiplier(task_metrics.historical_success_rate)
        
        # Calculate final price
        final_price = (base_price * 
                      complexity_multiplier * 
                      demand_multiplier * 
                      urgency_multiplier * 
                      client_multiplier * 
                      success_rate_multiplier)
        
        # Generate confidence score
        confidence_score = self._calculate_confidence_score(task_metrics, demand_multiplier)
        
        # Create explanation
        explanation = self._generate_pricing_explanation(
            base_price, complexity_multiplier, demand_multiplier, 
            urgency_multiplier, client_multiplier, success_rate_multiplier
        )
        
        # Log pricing decision
        await self._log_pricing_decision(task_metrics, final_price, confidence_score)
        
        return PricingResult(
            base_price=base_price,
            complexity_multiplier=complexity_multiplier,
            demand_multiplier=demand_multiplier,
            urgency_multiplier=urgency_multiplier,
            final_price=round(final_price, 2),
            confidence_score=confidence_score,
            explanation=explanation
        )
    
    def _assess_complexity_level(self, complexity_score: float) -> ComplexityLevel:
        """Convert numerical complexity to complexity level"""
        if complexity_score <= 1.0:
            return ComplexityLevel.TRIVIAL
        elif complexity_score <= 2.5:
            return ComplexityLevel.SIMPLE
        elif complexity_score <= 4.0:
            return ComplexityLevel.MODERATE
        elif complexity_score <= 6.5:
            return ComplexityLevel.COMPLEX
        elif complexity_score <= 8.5:
            return ComplexityLevel.CRITICAL
        else:
            return ComplexityLevel.ENTERPRISE
    
    def _calculate_complexity_multiplier(self, task_metrics: TaskMetrics) -> float:
        """Fine-tune complexity multiplier based on detailed metrics"""
        base_multiplier = 1.0
        
        # Agent requirement factor
        if task_metrics.required_agents > 5:
            base_multiplier += 0.2 * (task_metrics.required_agents - 5)
        
        # Duration factor
        if task_metrics.estimated_duration > 24:  # Hours
            base_multiplier += 0.1 * (task_metrics.estimated_duration / 24)
        
        # Expertise factor
        expertise_multipliers = {
            'junior': 0.8,
            'mid': 1.0,
            'senior': 1.3,
            'expert': 1.6,
            'specialist': 2.0
        }
        base_multiplier *= expertise_multipliers.get(task_metrics.expertise_level, 1.0)
        
        return min(base_multiplier, 3.0)  # Cap at 3x
    
    async def _calculate_demand_multiplier(self) -> float:
        """Calculate market demand multiplier based on current conditions"""
        
        # Get current agent utilization
        utilization_rate = await self._get_current_utilization()
        
        # Base demand calculation
        if utilization_rate > self.surge_pricing_threshold:
            # High demand - surge pricing
            surge_factor = (utilization_rate - self.surge_pricing_threshold) / (1 - self.surge_pricing_threshold)
            multiplier = 1.0 + (surge_factor * (self.maximum_surge_multiplier - 1.0))
        elif utilization_rate < self.discount_threshold:
            # Low demand - discount pricing
            discount_factor = (self.discount_threshold - utilization_rate) / self.discount_threshold
            multiplier = 1.0 - (discount_factor * (1.0 - self.minimum_discount_multiplier))
        else:
            # Normal demand
            multiplier = 1.0
        
        # Apply market volatility adjustment
        market_data = await self._get_market_data()
        volatility_adjustment = 1.0 + (market_data.market_volatility * 0.1)
        multiplier *= volatility_adjustment
        
        return multiplier
    
    def _calculate_urgency_multiplier(self, market_urgency: float) -> float:
        """Calculate urgency-based pricing multiplier"""
        if market_urgency <= 1.0:
            return 1.0  # Standard timing
        elif market_urgency <= 2.0:
            return 1.5  # Priority
        elif market_urgency <= 3.0:
            return 2.5  # Urgent
        else:
            return 4.0  # Emergency
    
    def _calculate_success_rate_multiplier(self, success_rate: float) -> float:
        """Adjust price based on historical success rate for this type of task"""
        if success_rate >= 0.95:
            return 1.2  # Premium for high reliability
        elif success_rate >= 0.85:
            return 1.0  # Standard pricing
        elif success_rate >= 0.70:
            return 0.9  # Slight discount
        else:
            return 0.8  # Discount for riskier tasks
    
    def _calculate_confidence_score(self, task_metrics: TaskMetrics, demand_multiplier: float) -> float:
        """Calculate confidence in pricing decision"""
        confidence_factors = []
        
        # Historical data availability
        confidence_factors.append(min(len(self.pricing_history) / 100, 1.0))
        
        # Market stability (inverse of volatility)
        confidence_factors.append(max(0.3, 1.0 - abs(demand_multiplier - 1.0)))
        
        # Task definition clarity
        if task_metrics.complexity_score > 0:
            confidence_factors.append(0.9)
        else:
            confidence_factors.append(0.6)
        
        return sum(confidence_factors) / len(confidence_factors)
    
    def _generate_pricing_explanation(self, base_price: float, complexity_mult: float, 
                                    demand_mult: float, urgency_mult: float, 
                                    client_mult: float, success_mult: float) -> str:
        """Generate human-readable explanation of pricing"""
        explanation_parts = [
            f"Base price: ${base_price:,.2f}"
        ]
        
        if complexity_mult != 1.0:
            explanation_parts.append(f"Complexity adjustment: {complexity_mult:.1f}x")
        
        if demand_mult > 1.1:
            explanation_parts.append(f"High demand surge: {demand_mult:.1f}x")
        elif demand_mult < 0.9:
            explanation_parts.append(f"Low demand discount: {demand_mult:.1f}x")
        
        if urgency_mult > 1.0:
            explanation_parts.append(f"Urgency premium: {urgency_mult:.1f}x")
        
        if client_mult != 1.0:
            explanation_parts.append(f"Client tier adjustment: {client_mult:.1f}x")
        
        if success_mult != 1.0:
            explanation_parts.append(f"Success rate adjustment: {success_mult:.1f}x")
        
        return " | ".join(explanation_parts)
    
    async def _get_current_utilization(self) -> float:
        """Get current agent utilization rate"""
        # In a real implementation, this would query the agent management system
        # For now, simulate based on time of day and recent activity
        
        current_hour = datetime.now().hour
        
        # Simulate business hours demand pattern
        if 9 <= current_hour <= 17:  # Business hours
            base_utilization = 0.7
        elif 18 <= current_hour <= 22:  # Evening
            base_utilization = 0.4
        else:  # Night/early morning
            base_utilization = 0.2
        
        # Add some randomness
        import random
        variance = random.uniform(-0.2, 0.2)
        return max(0.1, min(0.95, base_utilization + variance))
    
    async def _get_market_data(self) -> MarketData:
        """Retrieve current market conditions"""
        return MarketData(
            current_demand=1.0,
            agent_utilization=await self._get_current_utilization(),
            competitor_pricing={'competitor_a': 1200, 'competitor_b': 1500},
            market_volatility=0.1,
            seasonal_multiplier=1.0,
            timestamp=datetime.now()
        )
    
    async def _log_pricing_decision(self, task_metrics: TaskMetrics, 
                                  final_price: float, confidence_score: float):
        """Log pricing decision for future analysis"""
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'complexity_score': task_metrics.complexity_score,
            'final_price': final_price,
            'confidence_score': confidence_score,
            'market_conditions': self.market_conditions.value,
            'client_tier': task_metrics.client_tier
        }
        
        self.pricing_history.append(log_entry)
        
        # Keep only last 1000 entries to prevent memory issues
        if len(self.pricing_history) > 1000:
            self.pricing_history = self.pricing_history[-1000:]
    
    async def optimize_pricing_strategy(self) -> Dict[str, Any]:
        """Analyze pricing history and suggest optimizations"""
        if len(self.pricing_history) < 10:
            return {"status": "insufficient_data", "message": "Need more pricing history"}
        
        # Analyze price acceptance rates (would need actual acceptance data)
        # Analyze competitor positioning
        # Identify optimal price points per complexity level
        
        analysis = {
            "total_pricing_decisions": len(self.pricing_history),
            "average_confidence": np.mean([entry['confidence_score'] for entry in self.pricing_history]),
            "price_range_distribution": self._analyze_price_distribution(),
            "recommendations": self._generate_pricing_recommendations()
        }
        
        return analysis
    
    def _analyze_price_distribution(self) -> Dict[str, int]:
        """Analyze distribution of final prices"""
        price_ranges = {
            "under_500": 0,
            "500_1500": 0,
            "1500_5000": 0,
            "5000_15000": 0,
            "over_15000": 0
        }
        
        for entry in self.pricing_history:
            price = entry['final_price']
            if price < 500:
                price_ranges["under_500"] += 1
            elif price < 1500:
                price_ranges["500_1500"] += 1
            elif price < 5000:
                price_ranges["1500_5000"] += 1
            elif price < 15000:
                price_ranges["5000_15000"] += 1
            else:
                price_ranges["over_15000"] += 1
        
        return price_ranges
    
    def _generate_pricing_recommendations(self) -> List[str]:
        """Generate strategic pricing recommendations"""
        recommendations = []
        
        if len(self.pricing_history) > 50:
            avg_confidence = np.mean([entry['confidence_score'] for entry in self.pricing_history[-50:]])
            if avg_confidence < 0.7:
                recommendations.append("Consider gathering more market data to improve pricing confidence")
        
        # Add more sophisticated recommendations based on data analysis
        recommendations.append("Monitor competitor pricing changes weekly")
        recommendations.append("Implement A/B testing for price points in low-risk segments")
        
        return recommendations

# Pricing strategy presets
PRICING_STRATEGIES = {
    "aggressive": {
        "surge_multiplier": 5.0,
        "discount_multiplier": 0.5,
        "confidence_threshold": 0.6
    },
    "conservative": {
        "surge_multiplier": 2.0,
        "discount_multiplier": 0.8,
        "confidence_threshold": 0.8
    },
    "balanced": {
        "surge_multiplier": 3.0,
        "discount_multiplier": 0.7,
        "confidence_threshold": 0.7
    }
}

async def demo_dynamic_pricing():
    """Demonstrate the dynamic pricing engine"""
    engine = DynamicPricingEngine()
    
    # Example task metrics
    task_metrics = TaskMetrics(
        complexity_score=4.5,
        estimated_duration=12.0,
        required_agents=3,
        expertise_level="senior",
        historical_success_rate=0.92,
        market_urgency=1.5,
        client_tier="enterprise"
    )
    
    pricing_result = await engine.calculate_dynamic_price(task_metrics)
    
    print(f"Dynamic Pricing Result:")
    print(f"Base Price: ${pricing_result.base_price:,.2f}")
    print(f"Final Price: ${pricing_result.final_price:,.2f}")
    print(f"Confidence: {pricing_result.confidence_score:.1%}")
    print(f"Explanation: {pricing_result.explanation}")
    
    return pricing_result

if __name__ == "__main__":
    asyncio.run(demo_dynamic_pricing())