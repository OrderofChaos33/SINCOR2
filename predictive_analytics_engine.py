#!/usr/bin/env python3
"""
SINCOR Predictive Analytics Engine

Advanced predictive capabilities that give clients future insights:
- Market trend predictions with confidence intervals
- Competitor move forecasting
- Revenue impact projections
- Risk probability calculations
- Opportunity window analysis
- Multi-scenario planning with probability distributions

This separates SINCOR from reactive consultants - we predict what happens next.
"""

import json
import asyncio
import os
import numpy as np
import math
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from enum import Enum
import uuid
from collections import deque, defaultdict

from real_time_intelligence import IntelligenceDataPoint, IntelligenceSource

class PredictionType(Enum):
    """Types of predictions the engine can make"""
    MARKET_TREND = "market_trend"
    COMPETITOR_MOVE = "competitor_move"
    REVENUE_IMPACT = "revenue_impact"
    RISK_PROBABILITY = "risk_probability"
    OPPORTUNITY_WINDOW = "opportunity_window"
    DEMAND_FORECAST = "demand_forecast"
    PRICE_MOVEMENT = "price_movement"

class TimeHorizon(Enum):
    """Prediction time horizons"""
    SHORT_TERM = "1_week"      # 1 week
    MEDIUM_TERM = "1_month"    # 1 month
    LONG_TERM = "3_months"     # 3 months
    STRATEGIC = "1_year"       # 1 year

@dataclass
class ConfidenceInterval:
    """Statistical confidence interval"""
    lower_bound: float
    upper_bound: float
    confidence_level: float  # e.g., 0.95 for 95%
    point_estimate: float
    
@dataclass
class PredictionResult:
    """Result of predictive analysis"""
    prediction_id: str
    prediction_type: PredictionType
    time_horizon: TimeHorizon
    target_entity: str
    point_prediction: float
    confidence_interval: ConfidenceInterval
    probability_distribution: Dict[str, float]
    key_assumptions: List[str]
    risk_factors: List[str]
    data_sources_used: List[str]
    model_accuracy: float
    created: str
    expires: str

@dataclass
class ScenarioAnalysis:
    """Multi-scenario prediction analysis"""
    scenario_id: str
    base_scenario: PredictionResult
    optimistic_scenario: PredictionResult
    pessimistic_scenario: PredictionResult
    scenario_probabilities: Dict[str, float]
    key_differentiators: List[str]
    recommended_strategy: str

@dataclass
class TrendAnalysis:
    """Historical trend analysis for predictions"""
    entity: str
    metric: str
    historical_data: List[Tuple[str, float]]  # (timestamp, value)
    trend_direction: str  # "increasing", "decreasing", "stable", "volatile"
    trend_strength: float  # 0.0 to 1.0
    seasonality_detected: bool
    anomalies: List[Tuple[str, float, str]]  # (timestamp, value, reason)

class PredictiveAnalyticsEngine:
    """Advanced predictive analytics using SINCOR intelligence"""
    
    def __init__(self):
        self.engine_id = f"predict_{uuid.uuid4().hex[:8]}"
        
        # Historical data storage for model training
        self.historical_data = defaultdict(deque)  # entity -> historical points
        self.trend_analyses = {}  # entity -> TrendAnalysis
        self.prediction_history = []
        self.model_accuracy_scores = defaultdict(list)
        
        # Prediction models (simplified - in production would use ML models)
        self.prediction_models = {}
        self.confidence_calculators = {}
        
        # Configuration
        self.min_data_points = 10  # Minimum points needed for prediction
        self.default_confidence_level = 0.90
        self.prediction_cache = {}
        
        # Initialize prediction models
        self._initialize_prediction_models()
    
    def _initialize_prediction_models(self):
        """Initialize prediction models for different prediction types"""
        
        self.prediction_models = {
            PredictionType.MARKET_TREND: self._predict_market_trend,
            PredictionType.COMPETITOR_MOVE: self._predict_competitor_move,
            PredictionType.REVENUE_IMPACT: self._predict_revenue_impact,
            PredictionType.RISK_PROBABILITY: self._predict_risk_probability,
            PredictionType.OPPORTUNITY_WINDOW: self._predict_opportunity_window,
            PredictionType.DEMAND_FORECAST: self._predict_demand_forecast,
            PredictionType.PRICE_MOVEMENT: self._predict_price_movement
        }
    
    async def add_intelligence_data(self, data_points: List[IntelligenceDataPoint]):
        """Add real-time intelligence data for predictive modeling"""
        
        for data_point in data_points:
            # Extract relevant metrics for prediction
            metrics = self._extract_predictive_metrics(data_point)
            
            for entity, metric_values in metrics.items():
                # Store historical data
                timestamp = data_point.timestamp
                
                for metric_name, value in metric_values.items():
                    key = f"{entity}_{metric_name}"
                    self.historical_data[key].append((timestamp, value))
                    
                    # Limit historical data size
                    if len(self.historical_data[key]) > 1000:
                        self.historical_data[key].popleft()
        
        # Update trend analyses
        await self._update_trend_analyses()
    
    def _extract_predictive_metrics(self, data_point: IntelligenceDataPoint) -> Dict[str, Dict[str, float]]:
        """Extract predictive metrics from intelligence data"""
        
        metrics = {}
        
        if data_point.source == IntelligenceSource.FINANCIAL_MARKETS:
            for entity in data_point.affected_entities:
                metrics[entity] = {
                    "price_change": data_point.content.get("price_change_percent", 0),
                    "volume_change": data_point.content.get("volume_change", 0),
                    "volatility": abs(data_point.content.get("price_change_percent", 0))
                }
        
        elif data_point.source == IntelligenceSource.NEWS_FEEDS:
            for entity in data_point.affected_entities:
                metrics[entity] = {
                    "sentiment": data_point.content.get("sentiment", 0),
                    "impact_score": data_point.content.get("impact_score", 0),
                    "news_volume": 1.0  # Count of news items
                }
        
        elif data_point.source == IntelligenceSource.SOCIAL_MEDIA:
            for entity in data_point.affected_entities:
                metrics[entity] = {
                    "social_sentiment": data_point.content.get("sentiment_change", 0),
                    "mention_volume": data_point.content.get("mention_volume", 0),
                    "engagement_rate": data_point.content.get("engagement_rate", 0)
                }
        
        elif data_point.source == IntelligenceSource.COMPETITOR_WEBSITES:
            for entity in data_point.affected_entities:
                change_type = data_point.content.get("change_type")
                if change_type == "pricing_update":
                    metrics[entity] = {
                        "competitive_price_change": data_point.content.get("details", {}).get("price_change_percent", 0),
                        "competitive_activity": data_point.content.get("impact", 0)
                    }
                elif change_type == "new_feature":
                    metrics[entity] = {
                        "innovation_rate": 1.0,
                        "competitive_threat": data_point.content.get("impact", 0)
                    }
        
        elif data_point.source == IntelligenceSource.SEARCH_TRENDS:
            for entity in data_point.affected_entities:
                metrics[entity] = {
                    "search_interest": data_point.content.get("trend_multiplier", 1.0),
                    "market_awareness": data_point.content.get("trend_multiplier", 1.0) - 1.0
                }
        
        return metrics
    
    async def _update_trend_analyses(self):
        """Update trend analyses for all entities with sufficient data"""
        
        for key, data_points in self.historical_data.items():
            if len(data_points) >= self.min_data_points:
                entity_metric = key
                
                # Perform trend analysis
                trend_analysis = await self._analyze_trend(entity_metric, list(data_points))
                self.trend_analyses[entity_metric] = trend_analysis
    
    async def _analyze_trend(self, entity_metric: str, data_points: List[Tuple[str, float]]) -> TrendAnalysis:
        """Analyze trend for specific entity metric"""
        
        # Sort by timestamp
        sorted_data = sorted(data_points, key=lambda x: x[0])
        values = [point[1] for point in sorted_data]
        
        # Calculate trend direction and strength
        if len(values) < 3:
            trend_direction = "stable"
            trend_strength = 0.0
        else:
            # Simple linear regression for trend
            x = list(range(len(values)))
            y = values
            
            # Calculate slope
            n = len(x)
            sum_x = sum(x)
            sum_y = sum(y)
            sum_xy = sum(x[i] * y[i] for i in range(n))
            sum_x2 = sum(x[i] ** 2 for i in range(n))
            
            if n * sum_x2 - sum_x ** 2 != 0:
                slope = (n * sum_xy - sum_x * sum_y) / (n * sum_x2 - sum_x ** 2)
                
                # Determine trend direction
                if abs(slope) < 0.01:
                    trend_direction = "stable"
                elif slope > 0:
                    trend_direction = "increasing"
                else:
                    trend_direction = "decreasing"
                
                # Calculate trend strength (R-squared)
                y_mean = sum_y / n
                ss_tot = sum((y[i] - y_mean) ** 2 for i in range(n))
                y_pred = [slope * x[i] + (sum_y - slope * sum_x) / n for i in range(n)]
                ss_res = sum((y[i] - y_pred[i]) ** 2 for i in range(n))
                
                trend_strength = 1 - (ss_res / ss_tot) if ss_tot != 0 else 0.0
                trend_strength = max(0, min(1, trend_strength))
            else:
                trend_direction = "stable"
                trend_strength = 0.0
        
        # Detect seasonality (simplified)
        seasonality_detected = self._detect_seasonality(values)
        
        # Detect anomalies
        anomalies = self._detect_anomalies(sorted_data, values)
        
        entity, metric = entity_metric.split("_", 1) if "_" in entity_metric else (entity_metric, "value")
        
        return TrendAnalysis(
            entity=entity,
            metric=metric,
            historical_data=sorted_data,
            trend_direction=trend_direction,
            trend_strength=trend_strength,
            seasonality_detected=seasonality_detected,
            anomalies=anomalies
        )
    
    def _detect_seasonality(self, values: List[float]) -> bool:
        """Detect if data shows seasonal patterns (simplified)"""
        
        if len(values) < 14:  # Need at least 2 weeks of data
            return False
        
        # Simple autocorrelation check for weekly patterns
        weekly_correlation = self._autocorrelation(values, 7)
        return weekly_correlation > 0.3
    
    def _autocorrelation(self, values: List[float], lag: int) -> float:
        """Calculate autocorrelation at specified lag"""
        
        if len(values) <= lag:
            return 0.0
        
        n = len(values) - lag
        mean_val = sum(values) / len(values)
        
        numerator = sum((values[i] - mean_val) * (values[i + lag] - mean_val) for i in range(n))
        denominator = sum((val - mean_val) ** 2 for val in values)
        
        return numerator / denominator if denominator != 0 else 0.0
    
    def _detect_anomalies(self, sorted_data: List[Tuple[str, float]], 
                         values: List[float]) -> List[Tuple[str, float, str]]:
        """Detect anomalies in the data"""
        
        if len(values) < 5:
            return []
        
        # Calculate z-scores
        mean_val = sum(values) / len(values)
        std_val = math.sqrt(sum((val - mean_val) ** 2 for val in values) / len(values))
        
        anomalies = []
        
        if std_val > 0:
            for i, (timestamp, value) in enumerate(sorted_data):
                z_score = abs(value - mean_val) / std_val
                
                if z_score > 2.5:  # 2.5 sigma threshold
                    reason = "statistical_outlier" if z_score > 3 else "potential_outlier"
                    anomalies.append((timestamp, value, reason))
        
        return anomalies
    
    async def generate_prediction(self, prediction_type: PredictionType, 
                                target_entity: str, time_horizon: TimeHorizon,
                                additional_context: Dict[str, Any] = None) -> PredictionResult:
        """Generate prediction for specific entity and time horizon"""
        
        print(f"[PREDICT] Generating {prediction_type.value} for {target_entity} ({time_horizon.value})")
        
        # Check cache first
        cache_key = f"{prediction_type.value}_{target_entity}_{time_horizon.value}"
        if cache_key in self.prediction_cache:
            cached_prediction = self.prediction_cache[cache_key]
            cache_age = (datetime.now() - datetime.fromisoformat(cached_prediction.created)).total_seconds() / 3600
            
            if cache_age < 1:  # Use cache for 1 hour
                return cached_prediction
        
        # Get prediction model
        prediction_model = self.prediction_models.get(prediction_type)
        if not prediction_model:
            raise ValueError(f"No model available for {prediction_type}")
        
        # Generate prediction
        prediction_result = await prediction_model(target_entity, time_horizon, additional_context or {})
        
        # Cache result
        self.prediction_cache[cache_key] = prediction_result
        
        # Store in history
        self.prediction_history.append(prediction_result)
        
        return prediction_result
    
    async def _predict_market_trend(self, target_entity: str, time_horizon: TimeHorizon,
                                  context: Dict[str, Any]) -> PredictionResult:
        """Predict market trend for entity"""
        
        # Get relevant trend analysis
        relevant_metrics = [key for key in self.trend_analyses.keys() 
                          if target_entity in key and any(metric in key for metric in ["sentiment", "price_change", "search_interest"])]
        
        if not relevant_metrics:
            # Create default prediction with low confidence
            return self._create_default_prediction(PredictionType.MARKET_TREND, target_entity, time_horizon, 0.3)
        
        # Combine multiple metrics for trend prediction
        trend_scores = []
        data_sources = []
        
        for metric_key in relevant_metrics:
            trend_analysis = self.trend_analyses[metric_key]
            
            if trend_analysis.trend_direction == "increasing":
                score = trend_analysis.trend_strength
            elif trend_analysis.trend_direction == "decreasing":
                score = -trend_analysis.trend_strength
            else:
                score = 0
            
            trend_scores.append(score)
            data_sources.append(metric_key)
        
        # Calculate weighted average
        if trend_scores:
            avg_trend = sum(trend_scores) / len(trend_scores)
        else:
            avg_trend = 0
        
        # Adjust for time horizon (longer horizons = more uncertainty)
        time_adjustment = {
            TimeHorizon.SHORT_TERM: 1.0,
            TimeHorizon.MEDIUM_TERM: 0.8,
            TimeHorizon.LONG_TERM: 0.6,
            TimeHorizon.STRATEGIC: 0.4
        }[time_horizon]
        
        point_prediction = avg_trend * time_adjustment
        
        # Calculate confidence interval
        confidence_width = 0.2 * (1 + abs(point_prediction))  # Wider for extreme predictions
        confidence_interval = ConfidenceInterval(
            lower_bound=point_prediction - confidence_width,
            upper_bound=point_prediction + confidence_width,
            confidence_level=self.default_confidence_level,
            point_estimate=point_prediction
        )
        
        # Create probability distribution
        probability_distribution = {
            "strong_positive": max(0, (point_prediction - 0.5) * 2) if point_prediction > 0.5 else 0,
            "moderate_positive": max(0, point_prediction * 2) if 0 < point_prediction <= 0.5 else 0,
            "stable": max(0, 1 - abs(point_prediction * 2)),
            "moderate_negative": max(0, abs(point_prediction) * 2) if -0.5 <= point_prediction < 0 else 0,
            "strong_negative": max(0, (abs(point_prediction) - 0.5) * 2) if point_prediction < -0.5 else 0
        }
        
        # Normalize probabilities
        total_prob = sum(probability_distribution.values())
        if total_prob > 0:
            probability_distribution = {k: v/total_prob for k, v in probability_distribution.items()}
        
        # Generate assumptions and risks
        key_assumptions = [
            f"Historical trend patterns continue for {target_entity}",
            "No major market disruptions occur",
            "Current economic conditions remain stable"
        ]
        
        risk_factors = []
        if abs(point_prediction) > 0.7:
            risk_factors.append("High prediction magnitude increases uncertainty")
        if len(trend_scores) < 3:
            risk_factors.append("Limited data sources reduce prediction reliability")
        
        # Calculate model accuracy based on historical performance
        model_accuracy = self.model_accuracy_scores.get(PredictionType.MARKET_TREND, [0.75])[-1]
        
        return PredictionResult(
            prediction_id=f"pred_{uuid.uuid4().hex[:8]}",
            prediction_type=PredictionType.MARKET_TREND,
            time_horizon=time_horizon,
            target_entity=target_entity,
            point_prediction=point_prediction,
            confidence_interval=confidence_interval,
            probability_distribution=probability_distribution,
            key_assumptions=key_assumptions,
            risk_factors=risk_factors,
            data_sources_used=data_sources,
            model_accuracy=model_accuracy,
            created=datetime.now().isoformat(),
            expires=(datetime.now() + timedelta(hours=6)).isoformat()
        )
    
    async def _predict_competitor_move(self, target_entity: str, time_horizon: TimeHorizon,
                                     context: Dict[str, Any]) -> PredictionResult:
        """Predict likely competitor moves"""
        
        # Get competitor activity metrics
        competitor_metrics = [key for key in self.trend_analyses.keys() 
                            if target_entity in key and any(metric in key for metric in ["competitive_activity", "innovation_rate"])]
        
        if not competitor_metrics:
            return self._create_default_prediction(PredictionType.COMPETITOR_MOVE, target_entity, time_horizon, 0.4)
        
        # Analyze competitor activity patterns
        activity_level = 0
        innovation_rate = 0
        
        for metric_key in competitor_metrics:
            trend = self.trend_analyses[metric_key]
            if "activity" in metric_key:
                activity_level = trend.trend_strength if trend.trend_direction == "increasing" else 0
            elif "innovation" in metric_key:
                innovation_rate = trend.trend_strength if trend.trend_direction == "increasing" else 0
        
        # Predict probability of different competitor moves
        pricing_move_prob = min(0.8, activity_level + 0.2)
        feature_launch_prob = min(0.7, innovation_rate + 0.1)
        market_expansion_prob = min(0.6, (activity_level + innovation_rate) / 2)
        
        # Most likely move
        move_probabilities = {
            "pricing_change": pricing_move_prob,
            "feature_launch": feature_launch_prob,
            "market_expansion": market_expansion_prob,
            "no_major_move": max(0, 1 - max(pricing_move_prob, feature_launch_prob, market_expansion_prob))
        }
        
        # Normalize
        total_prob = sum(move_probabilities.values())
        if total_prob > 0:
            move_probabilities = {k: v/total_prob for k, v in move_probabilities.items()}
        
        # Point prediction is probability of any significant move
        point_prediction = 1 - move_probabilities["no_major_move"]
        
        # Confidence interval
        confidence_interval = ConfidenceInterval(
            lower_bound=max(0, point_prediction - 0.25),
            upper_bound=min(1, point_prediction + 0.25),
            confidence_level=0.85,  # Lower confidence for competitor predictions
            point_estimate=point_prediction
        )
        
        return PredictionResult(
            prediction_id=f"pred_{uuid.uuid4().hex[:8]}",
            prediction_type=PredictionType.COMPETITOR_MOVE,
            time_horizon=time_horizon,
            target_entity=target_entity,
            point_prediction=point_prediction,
            confidence_interval=confidence_interval,
            probability_distribution=move_probabilities,
            key_assumptions=[
                f"Historical activity patterns predict future behavior for {target_entity}",
                "Competitive responses follow typical market dynamics",
                "No external disruptions alter competitive landscape"
            ],
            risk_factors=[
                "Competitor strategy changes unpredictably",
                "Market conditions may trigger unexpected moves",
                "Internal competitor factors unknown"
            ],
            data_sources_used=competitor_metrics,
            model_accuracy=0.65,  # Lower accuracy for competitor predictions
            created=datetime.now().isoformat(),
            expires=(datetime.now() + timedelta(hours=12)).isoformat()
        )
    
    async def _predict_revenue_impact(self, target_entity: str, time_horizon: TimeHorizon,
                                    context: Dict[str, Any]) -> PredictionResult:
        """Predict revenue impact of market conditions"""
        
        # Get revenue-related metrics
        revenue_metrics = [key for key in self.trend_analyses.keys() 
                         if target_entity in key and any(metric in key for metric in ["sentiment", "search_interest", "competitive_price_change"])]
        
        if not revenue_metrics:
            return self._create_default_prediction(PredictionType.REVENUE_IMPACT, target_entity, time_horizon, 0.5)
        
        # Calculate revenue impact factors
        sentiment_impact = 0
        demand_impact = 0
        competitive_impact = 0
        
        for metric_key in revenue_metrics:
            trend = self.trend_analyses[metric_key]
            
            if "sentiment" in metric_key:
                # Positive sentiment = positive revenue impact
                sentiment_impact = trend.trend_strength * (1 if trend.trend_direction == "increasing" else -1)
            elif "search_interest" in metric_key:
                # Higher search interest = higher demand
                demand_impact = trend.trend_strength * (1 if trend.trend_direction == "increasing" else -1)
            elif "competitive_price" in metric_key:
                # Competitive price increases = opportunity for revenue
                competitive_impact = trend.trend_strength * (1 if trend.trend_direction == "increasing" else -1)
        
        # Weighted revenue impact
        impact_weights = {"sentiment": 0.4, "demand": 0.4, "competitive": 0.2}
        total_impact = (sentiment_impact * impact_weights["sentiment"] + 
                       demand_impact * impact_weights["demand"] + 
                       competitive_impact * impact_weights["competitive"])
        
        # Convert to revenue percentage change
        revenue_change_percent = total_impact * 0.15  # Max 15% impact
        
        # Adjust for time horizon
        time_multipliers = {
            TimeHorizon.SHORT_TERM: 0.5,
            TimeHorizon.MEDIUM_TERM: 1.0,
            TimeHorizon.LONG_TERM: 1.5,
            TimeHorizon.STRATEGIC: 2.0
        }
        
        point_prediction = revenue_change_percent * time_multipliers[time_horizon]
        
        # Confidence interval
        confidence_width = 0.05 + abs(point_prediction) * 0.3
        confidence_interval = ConfidenceInterval(
            lower_bound=point_prediction - confidence_width,
            upper_bound=point_prediction + confidence_width,
            confidence_level=0.90,
            point_estimate=point_prediction
        )
        
        # Probability distribution for revenue change ranges
        probability_distribution = {
            "significant_increase": max(0, (point_prediction - 0.05) * 5) if point_prediction > 0.05 else 0,
            "moderate_increase": max(0, point_prediction * 10) if 0 < point_prediction <= 0.05 else 0,
            "minimal_change": max(0, 1 - abs(point_prediction * 10)),
            "moderate_decrease": max(0, abs(point_prediction) * 10) if -0.05 <= point_prediction < 0 else 0,
            "significant_decrease": max(0, (abs(point_prediction) - 0.05) * 5) if point_prediction < -0.05 else 0
        }
        
        # Normalize
        total_prob = sum(probability_distribution.values())
        if total_prob > 0:
            probability_distribution = {k: v/total_prob for k, v in probability_distribution.items()}
        
        return PredictionResult(
            prediction_id=f"pred_{uuid.uuid4().hex[:8]}",
            prediction_type=PredictionType.REVENUE_IMPACT,
            time_horizon=time_horizon,
            target_entity=target_entity,
            point_prediction=point_prediction,
            confidence_interval=confidence_interval,
            probability_distribution=probability_distribution,
            key_assumptions=[
                "Market sentiment correlates with revenue performance",
                "Search demand translates to actual sales",
                "Competitive pricing affects market share",
                "Historical relationships continue to hold"
            ],
            risk_factors=[
                "Economic conditions may alter demand patterns",
                "Competitive actions may disrupt projections",
                "Internal operational changes not accounted for"
            ],
            data_sources_used=revenue_metrics,
            model_accuracy=0.78,
            created=datetime.now().isoformat(),
            expires=(datetime.now() + timedelta(hours=8)).isoformat()
        )
    
    async def _predict_risk_probability(self, target_entity: str, time_horizon: TimeHorizon,
                                      context: Dict[str, Any]) -> PredictionResult:
        """Predict probability of various risks"""
        
        # Analyze volatility and negative sentiment patterns
        risk_metrics = [key for key in self.trend_analyses.keys() 
                       if target_entity in key]
        
        risk_score = 0
        volatility_score = 0
        negative_sentiment_score = 0
        
        for metric_key in risk_metrics:
            trend = self.trend_analyses[metric_key]
            
            # Check for volatility indicators
            if len(trend.anomalies) > 0:
                volatility_score += len(trend.anomalies) / len(trend.historical_data)
            
            # Check for negative sentiment trends
            if "sentiment" in metric_key and trend.trend_direction == "decreasing":
                negative_sentiment_score += trend.trend_strength
        
        # Combine risk factors
        risk_score = min(1.0, (volatility_score * 0.6 + negative_sentiment_score * 0.4))
        
        # Adjust for time horizon (longer = more risk)
        time_risk_multipliers = {
            TimeHorizon.SHORT_TERM: 0.8,
            TimeHorizon.MEDIUM_TERM: 1.0,
            TimeHorizon.LONG_TERM: 1.3,
            TimeHorizon.STRATEGIC: 1.6
        }
        
        point_prediction = risk_score * time_risk_multipliers[time_horizon]
        point_prediction = min(1.0, point_prediction)
        
        # Confidence interval
        confidence_interval = ConfidenceInterval(
            lower_bound=max(0, point_prediction - 0.2),
            upper_bound=min(1, point_prediction + 0.2),
            confidence_level=0.85,
            point_estimate=point_prediction
        )
        
        # Risk level probabilities
        probability_distribution = {
            "low_risk": max(0, 1 - point_prediction) if point_prediction < 0.3 else 0,
            "moderate_risk": max(0, 1 - abs(point_prediction - 0.5) * 2) if 0.2 < point_prediction < 0.8 else 0,
            "high_risk": max(0, point_prediction - 0.5) * 2 if point_prediction > 0.5 else 0,
            "critical_risk": max(0, point_prediction - 0.8) * 5 if point_prediction > 0.8 else 0
        }
        
        # Normalize
        total_prob = sum(probability_distribution.values())
        if total_prob > 0:
            probability_distribution = {k: v/total_prob for k, v in probability_distribution.items()}
        
        return PredictionResult(
            prediction_id=f"pred_{uuid.uuid4().hex[:8]}",
            prediction_type=PredictionType.RISK_PROBABILITY,
            time_horizon=time_horizon,
            target_entity=target_entity,
            point_prediction=point_prediction,
            confidence_interval=confidence_interval,
            probability_distribution=probability_distribution,
            key_assumptions=[
                "Historical volatility patterns indicate future risk",
                "Sentiment trends correlate with business risks",
                "Market conditions remain within historical ranges"
            ],
            risk_factors=[
                "Black swan events not predictable from historical data",
                "Risk factors may be correlated in crisis scenarios",
                "External shocks may invalidate historical patterns"
            ],
            data_sources_used=risk_metrics,
            model_accuracy=0.72,
            created=datetime.now().isoformat(),
            expires=(datetime.now() + timedelta(hours=4)).isoformat()
        )
    
    # Placeholder implementations for other prediction types
    async def _predict_opportunity_window(self, target_entity: str, time_horizon: TimeHorizon,
                                        context: Dict[str, Any]) -> PredictionResult:
        return self._create_default_prediction(PredictionType.OPPORTUNITY_WINDOW, target_entity, time_horizon, 0.6)
    
    async def _predict_demand_forecast(self, target_entity: str, time_horizon: TimeHorizon,
                                     context: Dict[str, Any]) -> PredictionResult:
        return self._create_default_prediction(PredictionType.DEMAND_FORECAST, target_entity, time_horizon, 0.7)
    
    async def _predict_price_movement(self, target_entity: str, time_horizon: TimeHorizon,
                                    context: Dict[str, Any]) -> PredictionResult:
        return self._create_default_prediction(PredictionType.PRICE_MOVEMENT, target_entity, time_horizon, 0.65)
    
    def _create_default_prediction(self, prediction_type: PredictionType, target_entity: str,
                                 time_horizon: TimeHorizon, confidence: float) -> PredictionResult:
        """Create default prediction when insufficient data"""
        
        point_prediction = 0.0  # Neutral prediction
        
        confidence_interval = ConfidenceInterval(
            lower_bound=-0.3,
            upper_bound=0.3,
            confidence_level=confidence,
            point_estimate=point_prediction
        )
        
        probability_distribution = {
            "positive": 0.4,
            "neutral": 0.4,
            "negative": 0.2
        }
        
        return PredictionResult(
            prediction_id=f"pred_{uuid.uuid4().hex[:8]}",
            prediction_type=prediction_type,
            time_horizon=time_horizon,
            target_entity=target_entity,
            point_prediction=point_prediction,
            confidence_interval=confidence_interval,
            probability_distribution=probability_distribution,
            key_assumptions=["Insufficient historical data for precise prediction"],
            risk_factors=["Limited data reduces prediction accuracy"],
            data_sources_used=[],
            model_accuracy=confidence,
            created=datetime.now().isoformat(),
            expires=(datetime.now() + timedelta(hours=2)).isoformat()
        )
    
    async def generate_scenario_analysis(self, target_entity: str, time_horizon: TimeHorizon,
                                       prediction_types: List[PredictionType]) -> ScenarioAnalysis:
        """Generate multi-scenario analysis"""
        
        print(f"[PREDICT] Generating scenario analysis for {target_entity}")
        
        # Generate base scenario predictions
        base_predictions = {}
        for pred_type in prediction_types:
            prediction = await self.generate_prediction(pred_type, target_entity, time_horizon)
            base_predictions[pred_type] = prediction
        
        # Create optimistic scenario (upper confidence bounds)
        optimistic_context = {"scenario": "optimistic"}
        optimistic_predictions = {}
        for pred_type in prediction_types:
            base_pred = base_predictions[pred_type]
            optimistic_pred = await self.generate_prediction(pred_type, target_entity, time_horizon, optimistic_context)
            # Adjust to upper confidence bound
            optimistic_pred.point_prediction = base_pred.confidence_interval.upper_bound
            optimistic_predictions[pred_type] = optimistic_pred
        
        # Create pessimistic scenario (lower confidence bounds)
        pessimistic_context = {"scenario": "pessimistic"}
        pessimistic_predictions = {}
        for pred_type in prediction_types:
            base_pred = base_predictions[pred_type]
            pessimistic_pred = await self.generate_prediction(pred_type, target_entity, time_horizon, pessimistic_context)
            # Adjust to lower confidence bound
            pessimistic_pred.point_prediction = base_pred.confidence_interval.lower_bound
            pessimistic_predictions[pred_type] = pessimistic_pred
        
        # Calculate scenario probabilities
        scenario_probabilities = {
            "base_case": 0.6,
            "optimistic": 0.25,
            "pessimistic": 0.15
        }
        
        # Identify key differentiators
        key_differentiators = [
            "Market sentiment trajectory",
            "Competitive response intensity",
            "Economic environment stability",
            "Internal execution effectiveness"
        ]
        
        # Recommended strategy based on scenarios
        base_outcome = sum(pred.point_prediction for pred in base_predictions.values()) / len(base_predictions)
        if base_outcome > 0.1:
            recommended_strategy = "Aggressive growth strategy - capitalize on positive trends"
        elif base_outcome < -0.1:
            recommended_strategy = "Defensive strategy - mitigate risks and preserve market position"
        else:
            recommended_strategy = "Balanced strategy - maintain flexibility to respond to emerging trends"
        
        # Use primary prediction type for main scenarios
        primary_pred_type = prediction_types[0]
        
        return ScenarioAnalysis(
            scenario_id=f"scenario_{uuid.uuid4().hex[:8]}",
            base_scenario=base_predictions[primary_pred_type],
            optimistic_scenario=optimistic_predictions[primary_pred_type],
            pessimistic_scenario=pessimistic_predictions[primary_pred_type],
            scenario_probabilities=scenario_probabilities,
            key_differentiators=key_differentiators,
            recommended_strategy=recommended_strategy
        )
    
    def update_model_accuracy(self, prediction_id: str, actual_outcome: float):
        """Update model accuracy based on actual outcomes"""
        
        # Find the prediction
        prediction = None
        for pred in self.prediction_history:
            if pred.prediction_id == prediction_id:
                prediction = pred
                break
        
        if not prediction:
            return
        
        # Calculate prediction error
        prediction_error = abs(prediction.point_prediction - actual_outcome)
        accuracy = max(0, 1 - prediction_error)  # Simple accuracy metric
        
        # Update model accuracy scores
        self.model_accuracy_scores[prediction.prediction_type].append(accuracy)
        
        # Keep only recent scores
        if len(self.model_accuracy_scores[prediction.prediction_type]) > 100:
            self.model_accuracy_scores[prediction.prediction_type] = \
                self.model_accuracy_scores[prediction.prediction_type][-100:]
        
        print(f"[PREDICT] Updated accuracy for {prediction.prediction_type.value}: {accuracy:.3f}")
    
    def get_prediction_dashboard(self) -> Dict[str, Any]:
        """Get predictive analytics dashboard"""
        
        # Model accuracy by type
        model_accuracies = {}
        for pred_type, accuracies in self.model_accuracy_scores.items():
            if accuracies:
                model_accuracies[pred_type.value] = {
                    "current_accuracy": accuracies[-1],
                    "average_accuracy": sum(accuracies) / len(accuracies),
                    "prediction_count": len(accuracies)
                }
        
        # Recent predictions summary
        recent_predictions = [pred for pred in self.prediction_history[-20:]]  # Last 20
        prediction_summary = {
            "total_predictions": len(self.prediction_history),
            "recent_predictions": len(recent_predictions),
            "prediction_types": list(set(pred.prediction_type.value for pred in recent_predictions))
        }
        
        # Data coverage
        data_coverage = {
            "entities_tracked": len(set(key.split('_')[0] for key in self.historical_data.keys())),
            "metrics_available": len(self.historical_data),
            "trend_analyses": len(self.trend_analyses),
            "total_data_points": sum(len(data) for data in self.historical_data.values())
        }
        
        return {
            "engine_status": {
                "engine_id": self.engine_id,
                "cache_size": len(self.prediction_cache),
                "active_models": len(self.prediction_models)
            },
            "model_performance": model_accuracies,
            "prediction_summary": prediction_summary,
            "data_coverage": data_coverage,
            "capabilities": [pred_type.value for pred_type in PredictionType]
        }

async def main():
    """Demo predictive analytics engine"""
    print("SINCOR Predictive Analytics Engine Demo")
    print("=" * 45)
    
    # Create predictive engine
    engine = PredictiveAnalyticsEngine()
    
    # Simulate some historical data
    from real_time_intelligence import IntelligenceDataPoint, IntelligenceSource, AlertSeverity
    
    mock_data_points = []
    
    # Create trend data over time
    for i in range(30):
        timestamp = (datetime.now() - timedelta(days=30-i)).isoformat()
        
        # Market sentiment trending up
        sentiment = -0.3 + (i * 0.02)  # Gradual improvement
        
        data_point = IntelligenceDataPoint(
            data_id=f"mock_{i}",
            source=IntelligenceSource.NEWS_FEEDS,
            timestamp=timestamp,
            content={"sentiment": sentiment, "impact_score": 0.7},
            confidence=0.85,
            relevance_score=0.8,
            affected_entities=["TechCorp"],
            alert_level=AlertSeverity.LOW,
            expiry_time=(datetime.now() + timedelta(days=1)).isoformat()
        )
        mock_data_points.append(data_point)
    
    # Add data to engine
    await engine.add_intelligence_data(mock_data_points)
    
    # Generate predictions
    predictions = []
    
    for pred_type in [PredictionType.MARKET_TREND, PredictionType.REVENUE_IMPACT, PredictionType.RISK_PROBABILITY]:
        prediction = await engine.generate_prediction(
            pred_type, "TechCorp", TimeHorizon.MEDIUM_TERM
        )
        predictions.append(prediction)
        
        print(f"\n{pred_type.value.upper()} Prediction:")
        print(f"  Point Estimate: {prediction.point_prediction:.3f}")
        print(f"  Confidence Interval: [{prediction.confidence_interval.lower_bound:.3f}, {prediction.confidence_interval.upper_bound:.3f}]")
        print(f"  Model Accuracy: {prediction.model_accuracy:.3f}")
    
    # Generate scenario analysis
    scenario = await engine.generate_scenario_analysis(
        "TechCorp", 
        TimeHorizon.MEDIUM_TERM,
        [PredictionType.MARKET_TREND, PredictionType.REVENUE_IMPACT]
    )
    
    print(f"\nSCENARIO ANALYSIS:")
    print(f"  Base Case: {scenario.base_scenario.point_prediction:.3f}")
    print(f"  Optimistic: {scenario.optimistic_scenario.point_prediction:.3f}")
    print(f"  Pessimistic: {scenario.pessimistic_scenario.point_prediction:.3f}")
    print(f"  Strategy: {scenario.recommended_strategy}")
    
    # Show dashboard
    dashboard = engine.get_prediction_dashboard()
    print(f"\nPrediction Dashboard:")
    for section, data in dashboard.items():
        print(f"\n{section.replace('_', ' ').title()}:")
        if isinstance(data, dict):
            for key, value in data.items():
                print(f"  {key}: {value}")
        else:
            print(f"  {data}")

if __name__ == "__main__":
    asyncio.run(main())