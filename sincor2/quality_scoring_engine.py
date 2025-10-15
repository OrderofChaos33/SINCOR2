#!/usr/bin/env python3
"""
SINCOR Self-Improving Quality Scoring System

Continuously learns what constitutes high-quality deliverables:
- Multi-dimensional quality assessment 
- Client satisfaction feedback integration
- Peer agent validation scoring
- Outcome-based quality measurement
- Self-correcting quality thresholds
- Agent performance optimization recommendations

The system gets smarter about quality with every deliverable.
"""

import json
import asyncio
import os
import numpy as np
from typing import Dict, List, Optional, Any, Tuple, Callable
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from enum import Enum
import uuid
import statistics
from collections import defaultdict, deque

class QualityDimension(Enum):
    """Dimensions of quality assessment"""
    ACCURACY = "accuracy"                    # Factual correctness
    COMPLETENESS = "completeness"            # Coverage of requirements
    RELEVANCE = "relevance"                  # Applicability to client needs
    TIMELINESS = "timeliness"                # Delivery speed vs deadline
    CLARITY = "clarity"                      # Communication effectiveness
    ACTIONABILITY = "actionability"          # Practical implementability
    INNOVATION = "innovation"                # Creative insights provided
    DEPTH = "depth"                         # Analysis thoroughness
    CREDIBILITY = "credibility"             # Source reliability & citations

class FeedbackSource(Enum):
    """Sources of quality feedback"""
    CLIENT_DIRECT = "client_direct"          # Direct client feedback
    CLIENT_USAGE = "client_usage"            # Client behavior/usage patterns
    PEER_AGENTS = "peer_agents"              # Other agent assessments  
    OUTCOME_TRACKING = "outcome_tracking"    # Business outcome results
    AUTOMATED_CHECKS = "automated_checks"    # System validation
    EXPERT_REVIEW = "expert_review"          # Human expert validation

@dataclass
class QualityScore:
    """Comprehensive quality score for a deliverable"""
    deliverable_id: str
    overall_score: float                     # 0.0 to 1.0
    dimension_scores: Dict[QualityDimension, float]
    feedback_sources: Dict[FeedbackSource, float]
    confidence: float                        # Confidence in score accuracy
    improvement_areas: List[str]
    strengths: List[str]
    benchmark_comparison: Dict[str, float]   # vs similar deliverables
    created: str
    
@dataclass
class QualityFeedback:
    """Feedback on deliverable quality"""
    feedback_id: str
    deliverable_id: str
    source: FeedbackSource
    dimension_scores: Dict[QualityDimension, float]
    overall_rating: float
    specific_comments: List[str]
    improvement_suggestions: List[str]
    timestamp: str
    feedback_reliability: float              # How reliable this source is
    
@dataclass
class QualityBenchmark:
    """Quality benchmarks for different deliverable types"""
    benchmark_id: str
    deliverable_type: str
    dimension_thresholds: Dict[QualityDimension, Dict[str, float]]  # min/good/excellent
    industry_standards: Dict[str, float]
    client_segment_expectations: Dict[str, Dict[QualityDimension, float]]
    last_updated: str

@dataclass
class AgentQualityProfile:
    """Quality performance profile for individual agents"""
    agent_id: str
    quality_history: List[float]
    dimension_strengths: Dict[QualityDimension, float]
    dimension_weaknesses: Dict[QualityDimension, float]  
    improvement_trajectory: Dict[QualityDimension, List[float]]
    specialization_quality: Dict[str, float]             # Quality by task type
    peer_validation_score: float
    client_satisfaction_average: float
    quality_consistency: float                           # Standard deviation of scores

class SelfImprovingQualityEngine:
    """Quality scoring system that learns and improves over time"""
    
    def __init__(self):
        self.engine_id = f"quality_{uuid.uuid4().hex[:8]}"
        
        # Quality assessment data
        self.quality_scores = {}                         # deliverable_id -> QualityScore
        self.feedback_history = defaultdict(list)       # deliverable_id -> [QualityFeedback]
        self.agent_profiles = {}                         # agent_id -> AgentQualityProfile
        self.quality_benchmarks = {}                     # deliverable_type -> QualityBenchmark
        
        # Learning mechanisms
        self.dimension_weight_evolution = defaultdict(list)  # How dimension weights change over time
        self.feedback_source_reliability = defaultdict(list) # Track reliability of feedback sources
        self.quality_model_accuracy = []                     # Track prediction accuracy
        
        # Self-improvement parameters
        self.learning_rate = 0.1
        self.min_feedback_for_learning = 5
        self.benchmark_update_frequency = 100  # Update benchmarks every 100 assessments
        
        # Initialize default benchmarks
        self._initialize_quality_benchmarks()
        
        # Quality prediction models
        self.quality_predictors = {}
    
    def _initialize_quality_benchmarks(self):
        """Initialize default quality benchmarks for different deliverable types"""
        
        deliverable_types = [
            "market_analysis", "competitor_intelligence", "revenue_optimization",
            "customer_insights", "growth_strategy", "risk_assessment", "investment_recommendation"
        ]
        
        for deliverable_type in deliverable_types:
            # Default dimension thresholds
            dimension_thresholds = {}
            
            for dimension in QualityDimension:
                dimension_thresholds[dimension] = {
                    "minimum": 0.6,    # Below this is unacceptable
                    "good": 0.75,      # Good quality threshold
                    "excellent": 0.90   # Excellent quality threshold
                }
            
            # Adjust thresholds based on deliverable type
            if deliverable_type in ["market_analysis", "competitor_intelligence"]:
                # These require high accuracy and completeness
                dimension_thresholds[QualityDimension.ACCURACY]["minimum"] = 0.8
                dimension_thresholds[QualityDimension.COMPLETENESS]["minimum"] = 0.75
                
            elif deliverable_type in ["growth_strategy", "investment_recommendation"]:
                # These require high actionability and depth
                dimension_thresholds[QualityDimension.ACTIONABILITY]["minimum"] = 0.75
                dimension_thresholds[QualityDimension.DEPTH]["minimum"] = 0.7
            
            benchmark = QualityBenchmark(
                benchmark_id=f"bench_{deliverable_type}",
                deliverable_type=deliverable_type,
                dimension_thresholds=dimension_thresholds,
                industry_standards={"consulting": 0.8, "AI_services": 0.75, "market_research": 0.85},
                client_segment_expectations={
                    "enterprise": {dim: 0.85 for dim in QualityDimension},
                    "mid_market": {dim: 0.75 for dim in QualityDimension},
                    "startup": {dim: 0.70 for dim in QualityDimension}
                },
                last_updated=datetime.now().isoformat()
            )
            
            self.quality_benchmarks[deliverable_type] = benchmark
    
    async def assess_deliverable_quality(self, deliverable_id: str, deliverable_content: Dict[str, Any],
                                       deliverable_type: str, agent_id: str,
                                       client_context: Dict[str, Any] = None) -> QualityScore:
        """Perform comprehensive quality assessment of a deliverable"""
        
        print(f"[QUALITY] Assessing quality for deliverable {deliverable_id}")
        
        # Get benchmark for this deliverable type
        benchmark = self.quality_benchmarks.get(deliverable_type)
        if not benchmark:
            # Create default benchmark
            await self._create_default_benchmark(deliverable_type)
            benchmark = self.quality_benchmarks[deliverable_type]
        
        # Assess each quality dimension
        dimension_scores = {}
        
        for dimension in QualityDimension:
            score = await self._assess_dimension(
                dimension, deliverable_content, deliverable_type, 
                agent_id, client_context or {}
            )
            dimension_scores[dimension] = score
        
        # Calculate weighted overall score
        dimension_weights = self._get_current_dimension_weights(deliverable_type)
        overall_score = sum(dimension_scores[dim] * dimension_weights[dim] 
                          for dim in QualityDimension) / sum(dimension_weights.values())
        
        # Get feedback from automated checks
        automated_feedback = await self._generate_automated_feedback(
            deliverable_content, dimension_scores, benchmark
        )
        
        # Calculate confidence based on available data
        confidence = self._calculate_assessment_confidence(deliverable_id, agent_id)
        
        # Identify improvement areas and strengths
        improvement_areas, strengths = self._analyze_quality_profile(
            dimension_scores, benchmark, deliverable_type
        )
        
        # Benchmark comparison
        benchmark_comparison = await self._compare_to_benchmarks(
            dimension_scores, deliverable_type, agent_id
        )
        
        # Create quality score
        quality_score = QualityScore(
            deliverable_id=deliverable_id,
            overall_score=overall_score,
            dimension_scores=dimension_scores,
            feedback_sources={FeedbackSource.AUTOMATED_CHECKS: automated_feedback},
            confidence=confidence,
            improvement_areas=improvement_areas,
            strengths=strengths,
            benchmark_comparison=benchmark_comparison,
            created=datetime.now().isoformat()
        )
        
        # Store quality score
        self.quality_scores[deliverable_id] = quality_score
        
        # Update agent quality profile
        await self._update_agent_quality_profile(agent_id, quality_score)
        
        print(f"[QUALITY] Overall score: {overall_score:.3f}, Confidence: {confidence:.3f}")
        
        return quality_score
    
    async def _assess_dimension(self, dimension: QualityDimension, 
                              deliverable_content: Dict[str, Any],
                              deliverable_type: str, agent_id: str,
                              client_context: Dict[str, Any]) -> float:
        """Assess quality for a specific dimension"""
        
        if dimension == QualityDimension.ACCURACY:
            return await self._assess_accuracy(deliverable_content, deliverable_type)
        elif dimension == QualityDimension.COMPLETENESS:
            return await self._assess_completeness(deliverable_content, deliverable_type)
        elif dimension == QualityDimension.RELEVANCE:
            return await self._assess_relevance(deliverable_content, client_context)
        elif dimension == QualityDimension.TIMELINESS:
            return await self._assess_timeliness(deliverable_content)
        elif dimension == QualityDimension.CLARITY:
            return await self._assess_clarity(deliverable_content)
        elif dimension == QualityDimension.ACTIONABILITY:
            return await self._assess_actionability(deliverable_content)
        elif dimension == QualityDimension.INNOVATION:
            return await self._assess_innovation(deliverable_content, deliverable_type)
        elif dimension == QualityDimension.DEPTH:
            return await self._assess_depth(deliverable_content, deliverable_type)
        elif dimension == QualityDimension.CREDIBILITY:
            return await self._assess_credibility(deliverable_content)
        else:
            return 0.7  # Default score
    
    async def _assess_accuracy(self, content: Dict[str, Any], deliverable_type: str) -> float:
        """Assess factual accuracy of deliverable"""
        
        # Check for data validation
        data_quality_score = 0.8  # Default
        
        if "supporting_data" in content:
            supporting_data = content["supporting_data"]
            
            # Check data source quality
            if "quality_scores" in supporting_data:
                quality_scores = supporting_data["quality_scores"]
                if quality_scores:
                    data_quality_score = sum(quality_scores) / len(quality_scores)
            
            # Check completion rate
            if "completion_rate" in supporting_data:
                completion_rate = supporting_data["completion_rate"]
                data_quality_score = (data_quality_score + completion_rate) / 2
        
        # Check for citations and sources
        citation_score = 0.7
        if "methodology" in content:
            methodology = content["methodology"]
            if "cross-agent validation" in methodology.lower():
                citation_score += 0.1
            if "recursive learning" in methodology.lower():
                citation_score += 0.1
            if "validation" in methodology.lower():
                citation_score += 0.1
        
        # Combine scores
        accuracy_score = (data_quality_score * 0.7 + citation_score * 0.3)
        
        return min(1.0, max(0.0, accuracy_score))
    
    async def _assess_completeness(self, content: Dict[str, Any], deliverable_type: str) -> float:
        """Assess completeness of deliverable"""
        
        expected_components = {
            "market_analysis": ["executive_summary", "key_findings", "recommendations"],
            "competitor_intelligence": ["executive_summary", "key_findings", "recommendations", "supporting_data"],
            "revenue_optimization": ["executive_summary", "key_findings", "recommendations"],
            "growth_strategy": ["executive_summary", "key_findings", "recommendations"]
        }
        
        required_components = expected_components.get(deliverable_type, ["executive_summary", "key_findings"])
        
        # Check component presence
        present_components = 0
        for component in required_components:
            if component in content and content[component]:
                present_components += 1
        
        component_completeness = present_components / len(required_components)
        
        # Check content depth within components
        depth_score = 0.8  # Default
        
        if "key_findings" in content:
            findings = content["key_findings"]
            if isinstance(findings, list) and len(findings) >= 3:
                depth_score = min(1.0, len(findings) / 5)  # Reward more findings
        
        if "recommendations" in content:
            recommendations = content["recommendations"]
            if isinstance(recommendations, list) and len(recommendations) >= 2:
                depth_score = max(depth_score, min(1.0, len(recommendations) / 4))
        
        completeness_score = (component_completeness * 0.7 + depth_score * 0.3)
        
        return min(1.0, max(0.0, completeness_score))
    
    async def _assess_relevance(self, content: Dict[str, Any], client_context: Dict[str, Any]) -> float:
        """Assess relevance to client needs"""
        
        relevance_score = 0.75  # Default
        
        # Check industry alignment
        client_industry = client_context.get("industry", "")
        if client_industry:
            # Check if industry mentioned in content
            content_text = str(content).lower()
            if client_industry.lower() in content_text:
                relevance_score += 0.1
        
        # Check company size considerations
        company_size = client_context.get("company_size", "")
        if company_size:
            content_text = str(content).lower()
            size_keywords = {
                "startup": ["startup", "early-stage", "growth"],
                "mid_market": ["mid-market", "established", "scaling"],
                "enterprise": ["enterprise", "large-scale", "corporate"]
            }
            
            if company_size in size_keywords:
                for keyword in size_keywords[company_size]:
                    if keyword in content_text:
                        relevance_score += 0.05
                        break
        
        # Check specific requirements addressed
        if "specific_questions" in client_context:
            questions_addressed = 0
            specific_questions = client_context["specific_questions"]
            
            for question in specific_questions:
                # Simple keyword matching (in real system would use NLP)
                question_keywords = question.lower().split()
                content_text = str(content).lower()
                
                keyword_matches = sum(1 for keyword in question_keywords if keyword in content_text)
                if keyword_matches >= len(question_keywords) * 0.5:  # 50% keyword match
                    questions_addressed += 1
            
            if specific_questions:
                question_relevance = questions_addressed / len(specific_questions)
                relevance_score = (relevance_score + question_relevance) / 2
        
        return min(1.0, max(0.0, relevance_score))
    
    async def _assess_timeliness(self, content: Dict[str, Any]) -> float:
        """Assess delivery timeliness"""
        
        # Check if completion time info is available
        if "completion_time_minutes" in content:
            completion_time = content["completion_time_minutes"]
            
            # Compare to expected delivery times
            expected_times = {
                "emergency": 120,    # 2 hours
                "priority": 240,     # 4 hours
                "standard": 480      # 8 hours
            }
            
            # Determine urgency (simplified)
            urgency = "standard"  # Default
            if completion_time <= 120:
                urgency = "emergency"
            elif completion_time <= 240:
                urgency = "priority"
            
            expected_time = expected_times[urgency]
            
            if completion_time <= expected_time:
                # Early or on-time delivery
                timeliness_score = min(1.0, 1.2 - (completion_time / expected_time))
            else:
                # Late delivery
                delay_factor = completion_time / expected_time
                timeliness_score = max(0.3, 1.0 / delay_factor)
            
            return timeliness_score
        
        return 0.8  # Default if no timing info
    
    async def _assess_clarity(self, content: Dict[str, Any]) -> float:
        """Assess communication clarity"""
        
        clarity_score = 0.8  # Default
        
        # Check executive summary clarity
        if "executive_summary" in content:
            summary = content["executive_summary"]
            if isinstance(summary, str) and len(summary) > 50:
                # Check for clear structure
                if "analysis" in summary.lower() and "recommendation" in summary.lower():
                    clarity_score += 0.1
                
                # Penalize excessive length (over 500 words)
                word_count = len(summary.split())
                if word_count > 500:
                    clarity_score -= 0.1
                elif word_count < 100:
                    clarity_score -= 0.05
        
        # Check recommendations clarity
        if "recommendations" in content:
            recommendations = content["recommendations"]
            if isinstance(recommendations, list):
                clear_recommendations = 0
                for rec in recommendations:
                    if isinstance(rec, dict):
                        # Check for key elements
                        has_action = "recommendation" in rec or "action" in rec
                        has_rationale = "rationale" in rec or "reasoning" in rec
                        has_impact = "impact" in rec or "expected" in rec
                        
                        if has_action and has_rationale:
                            clear_recommendations += 1
                
                if recommendations:
                    recommendation_clarity = clear_recommendations / len(recommendations)
                    clarity_score = (clarity_score + recommendation_clarity) / 2
        
        return min(1.0, max(0.0, clarity_score))
    
    async def _assess_actionability(self, content: Dict[str, Any]) -> float:
        """Assess how actionable the recommendations are"""
        
        actionability_score = 0.7  # Default
        
        if "recommendations" in content:
            recommendations = content["recommendations"]
            if isinstance(recommendations, list):
                actionable_count = 0
                
                for rec in recommendations:
                    if isinstance(rec, dict):
                        actionability_points = 0
                        
                        # Check for timeline
                        if any(key in rec for key in ["timeline", "implementation_timeline", "timeframe"]):
                            actionability_points += 1
                        
                        # Check for resource requirements
                        if any(key in rec for key in ["resources", "resource_requirements", "requirements"]):
                            actionability_points += 1
                        
                        # Check for specific steps
                        if any(key in rec for key in ["steps", "implementation_steps", "actions"]):
                            actionability_points += 1
                        
                        # Check for measurable outcomes
                        if any(key in rec for key in ["impact", "expected_impact", "roi", "metrics"]):
                            actionability_points += 1
                        
                        if actionability_points >= 2:  # At least 2 actionability features
                            actionable_count += 1
                
                if recommendations:
                    actionability_score = actionable_count / len(recommendations)
        
        return min(1.0, max(0.0, actionability_score))
    
    async def _assess_innovation(self, content: Dict[str, Any], deliverable_type: str) -> float:
        """Assess innovation and unique insights"""
        
        innovation_score = 0.6  # Default
        
        # Check for unique value proposition
        if "unique_value" in str(content).lower():
            innovation_score += 0.1
        
        # Check for novel approaches or insights
        innovation_keywords = ["innovative", "novel", "unique", "breakthrough", "emerging", "cutting-edge", 
                              "advanced", "next-generation", "disruptive", "game-changing"]
        
        content_text = str(content).lower()
        innovation_mentions = sum(1 for keyword in innovation_keywords if keyword in content_text)
        
        if innovation_mentions > 0:
            innovation_score += min(0.2, innovation_mentions * 0.05)
        
        # Check methodology sophistication
        if "methodology" in content:
            methodology = content["methodology"]
            if "advanced" in methodology.lower() or "ai" in methodology.lower():
                innovation_score += 0.1
            if "swarm" in methodology.lower() or "recursive" in methodology.lower():
                innovation_score += 0.15
        
        # Penalize generic content
        generic_phrases = ["industry standard", "best practice", "conventional wisdom", "typical approach"]
        generic_count = sum(1 for phrase in generic_phrases if phrase in content_text)
        if generic_count > 2:
            innovation_score -= 0.1
        
        return min(1.0, max(0.0, innovation_score))
    
    async def _assess_depth(self, content: Dict[str, Any], deliverable_type: str) -> float:
        """Assess analysis depth and thoroughness"""
        
        depth_score = 0.7  # Default
        
        # Check number of key findings
        if "key_findings" in content:
            findings = content["key_findings"]
            if isinstance(findings, list):
                findings_depth = min(1.0, len(findings) / 5)  # Up to 5 findings for full score
                depth_score = max(depth_score, findings_depth)
        
        # Check supporting data breadth
        if "supporting_data" in content:
            supporting_data = content["supporting_data"]
            if isinstance(supporting_data, dict):
                data_sources = supporting_data.get("data_sources", 0)
                if isinstance(data_sources, int) and data_sources > 0:
                    source_depth = min(1.0, data_sources / 10)  # Up to 10 sources for full score
                    depth_score = (depth_score + source_depth) / 2
        
        # Check methodology complexity
        if "methodology" in content:
            methodology = content["methodology"]
            complexity_indicators = ["multi-agent", "parallel", "cross-validation", "recursive", "nested"]
            complexity_count = sum(1 for indicator in complexity_indicators if indicator in methodology.lower())
            
            if complexity_count > 0:
                methodology_depth = min(1.0, complexity_count / 3)
                depth_score = (depth_score + methodology_depth) / 2
        
        return min(1.0, max(0.0, depth_score))
    
    async def _assess_credibility(self, content: Dict[str, Any]) -> float:
        """Assess credibility through sources and validation"""
        
        credibility_score = 0.75  # Default
        
        # Check for confidence scores
        if "confidence_score" in content:
            confidence = content["confidence_score"]
            if isinstance(confidence, (int, float)):
                credibility_score = max(credibility_score, confidence)
        
        # Check methodology credibility
        if "methodology" in content:
            methodology = content["methodology"]
            credibility_indicators = ["validation", "cross-agent", "verified", "multiple sources", "peer review"]
            
            credibility_mentions = sum(1 for indicator in credibility_indicators if indicator in methodology.lower())
            if credibility_mentions > 0:
                methodology_credibility = min(1.0, 0.7 + (credibility_mentions * 0.1))
                credibility_score = (credibility_score + methodology_credibility) / 2
        
        # Check for agent contributors (implies peer validation)
        if "agent_contributors" in content:
            contributors = content["agent_contributors"]
            if isinstance(contributors, list) and len(contributors) > 1:
                peer_validation = min(1.0, 0.8 + (len(contributors) - 1) * 0.05)
                credibility_score = (credibility_score + peer_validation) / 2
        
        return min(1.0, max(0.0, credibility_score))
    
    def _get_current_dimension_weights(self, deliverable_type: str) -> Dict[QualityDimension, float]:
        """Get current dimension weights (evolve over time based on learning)"""
        
        # Base weights
        base_weights = {
            QualityDimension.ACCURACY: 0.20,
            QualityDimension.COMPLETENESS: 0.15,
            QualityDimension.RELEVANCE: 0.15,
            QualityDimension.TIMELINESS: 0.10,
            QualityDimension.CLARITY: 0.10,
            QualityDimension.ACTIONABILITY: 0.15,
            QualityDimension.INNOVATION: 0.05,
            QualityDimension.DEPTH: 0.08,
            QualityDimension.CREDIBILITY: 0.02
        }
        
        # Adjust weights based on deliverable type
        if deliverable_type in ["market_analysis", "competitor_intelligence"]:
            base_weights[QualityDimension.ACCURACY] = 0.25
            base_weights[QualityDimension.CREDIBILITY] = 0.05
        elif deliverable_type in ["growth_strategy", "investment_recommendation"]:
            base_weights[QualityDimension.ACTIONABILITY] = 0.20
            base_weights[QualityDimension.DEPTH] = 0.15
        
        # Apply learning-based adjustments (simplified)
        if deliverable_type in self.dimension_weight_evolution:
            weight_history = self.dimension_weight_evolution[deliverable_type]
            if weight_history:
                # Simple adjustment based on recent learning
                latest_adjustments = weight_history[-1] if weight_history else {}
                for dimension, adjustment in latest_adjustments.items():
                    if dimension in base_weights:
                        base_weights[dimension] *= (1 + adjustment * 0.1)
        
        # Normalize weights
        total_weight = sum(base_weights.values())
        normalized_weights = {dim: weight / total_weight for dim, weight in base_weights.items()}
        
        return normalized_weights
    
    async def _generate_automated_feedback(self, content: Dict[str, Any], 
                                         dimension_scores: Dict[QualityDimension, float],
                                         benchmark: QualityBenchmark) -> float:
        """Generate automated feedback score"""
        
        # Compare against benchmarks
        benchmark_adherence = 0
        benchmark_violations = 0
        
        for dimension, score in dimension_scores.items():
            thresholds = benchmark.dimension_thresholds[dimension]
            
            if score >= thresholds["excellent"]:
                benchmark_adherence += 1
            elif score >= thresholds["good"]:
                benchmark_adherence += 0.8
            elif score >= thresholds["minimum"]:
                benchmark_adherence += 0.6
            else:
                benchmark_violations += 1
        
        total_dimensions = len(dimension_scores)
        automated_score = (benchmark_adherence / total_dimensions) * (1 - benchmark_violations / total_dimensions)
        
        return max(0.0, min(1.0, automated_score))
    
    def _calculate_assessment_confidence(self, deliverable_id: str, agent_id: str) -> float:
        """Calculate confidence in quality assessment"""
        
        confidence = 0.7  # Base confidence
        
        # Increase confidence based on agent history
        if agent_id in self.agent_profiles:
            profile = self.agent_profiles[agent_id]
            if len(profile.quality_history) > 10:
                confidence += 0.1  # More history = higher confidence
            
            # Consistent quality = higher confidence in assessment
            if profile.quality_consistency < 0.1:  # Low standard deviation
                confidence += 0.1
        
        # Increase confidence based on feedback availability
        if deliverable_id in self.feedback_history:
            feedback_count = len(self.feedback_history[deliverable_id])
            confidence += min(0.2, feedback_count * 0.05)
        
        return min(1.0, confidence)
    
    def _analyze_quality_profile(self, dimension_scores: Dict[QualityDimension, float],
                               benchmark: QualityBenchmark, deliverable_type: str) -> Tuple[List[str], List[str]]:
        """Analyze quality profile to identify improvements and strengths"""
        
        improvement_areas = []
        strengths = []
        
        for dimension, score in dimension_scores.items():
            thresholds = benchmark.dimension_thresholds[dimension]
            
            if score < thresholds["minimum"]:
                improvement_areas.append(f"{dimension.value}: Critical improvement needed ({score:.2f})")
            elif score < thresholds["good"]:
                improvement_areas.append(f"{dimension.value}: Below good threshold ({score:.2f})")
            elif score >= thresholds["excellent"]:
                strengths.append(f"{dimension.value}: Excellent performance ({score:.2f})")
            elif score >= thresholds["good"]:
                strengths.append(f"{dimension.value}: Good quality achieved ({score:.2f})")
        
        return improvement_areas, strengths
    
    async def _compare_to_benchmarks(self, dimension_scores: Dict[QualityDimension, float],
                                   deliverable_type: str, agent_id: str) -> Dict[str, float]:
        """Compare quality scores to various benchmarks"""
        
        benchmark_comparison = {}
        
        # Compare to deliverable type benchmark
        if deliverable_type in self.quality_benchmarks:
            benchmark = self.quality_benchmarks[deliverable_type]
            
            # Calculate overall benchmark comparison
            total_benchmark_score = 0
            for dimension, score in dimension_scores.items():
                thresholds = benchmark.dimension_thresholds[dimension]
                # Compare to "good" threshold
                benchmark_ratio = score / thresholds["good"]
                total_benchmark_score += benchmark_ratio
            
            benchmark_comparison["deliverable_type"] = total_benchmark_score / len(dimension_scores)
        
        # Compare to agent's historical performance
        if agent_id in self.agent_profiles:
            profile = self.agent_profiles[agent_id]
            if profile.quality_history:
                agent_average = sum(profile.quality_history) / len(profile.quality_history)
                current_overall = sum(dimension_scores.values()) / len(dimension_scores)
                benchmark_comparison["agent_historical"] = current_overall / agent_average if agent_average > 0 else 1.0
        
        # Compare to industry standards
        benchmark = self.quality_benchmarks.get(deliverable_type)
        if benchmark:
            industry_standard = benchmark.industry_standards.get("AI_services", 0.75)
            current_overall = sum(dimension_scores.values()) / len(dimension_scores)
            benchmark_comparison["industry_standard"] = current_overall / industry_standard
        
        return benchmark_comparison
    
    async def _update_agent_quality_profile(self, agent_id: str, quality_score: QualityScore):
        """Update agent's quality performance profile"""
        
        if agent_id not in self.agent_profiles:
            self.agent_profiles[agent_id] = AgentQualityProfile(
                agent_id=agent_id,
                quality_history=[],
                dimension_strengths={},
                dimension_weaknesses={},
                improvement_trajectory={},
                specialization_quality={},
                peer_validation_score=0.7,
                client_satisfaction_average=0.75,
                quality_consistency=0.0
            )
        
        profile = self.agent_profiles[agent_id]
        
        # Update quality history
        profile.quality_history.append(quality_score.overall_score)
        if len(profile.quality_history) > 100:  # Keep last 100 scores
            profile.quality_history = profile.quality_history[-100:]
        
        # Update dimension performance tracking
        for dimension, score in quality_score.dimension_scores.items():
            # Track improvement trajectory
            if dimension not in profile.improvement_trajectory:
                profile.improvement_trajectory[dimension] = []
            
            profile.improvement_trajectory[dimension].append(score)
            if len(profile.improvement_trajectory[dimension]) > 20:  # Keep last 20
                profile.improvement_trajectory[dimension] = profile.improvement_trajectory[dimension][-20:]
            
            # Update strengths and weaknesses
            recent_scores = profile.improvement_trajectory[dimension]
            avg_score = sum(recent_scores) / len(recent_scores)
            
            if avg_score >= 0.85:
                profile.dimension_strengths[dimension] = avg_score
            elif avg_score <= 0.65:
                profile.dimension_weaknesses[dimension] = avg_score
        
        # Calculate quality consistency
        if len(profile.quality_history) > 5:
            profile.quality_consistency = statistics.stdev(profile.quality_history[-10:])
        
        print(f"[QUALITY] Updated profile for {agent_id}: avg={sum(profile.quality_history)/len(profile.quality_history):.3f}, consistency={profile.quality_consistency:.3f}")
    
    async def add_external_feedback(self, deliverable_id: str, feedback: QualityFeedback):
        """Add external feedback (client, peer agents, experts) for learning"""
        
        print(f"[QUALITY] Adding {feedback.source.value} feedback for {deliverable_id}")
        
        # Store feedback
        self.feedback_history[deliverable_id].append(feedback)
        
        # Update feedback source reliability tracking
        self._update_feedback_source_reliability(feedback)
        
        # Trigger learning from feedback
        await self._learn_from_feedback(deliverable_id, feedback)
        
        # Update quality score if we have one
        if deliverable_id in self.quality_scores:
            await self._incorporate_feedback_into_score(deliverable_id, feedback)
    
    def _update_feedback_source_reliability(self, feedback: QualityFeedback):
        """Track reliability of different feedback sources"""
        
        self.feedback_source_reliability[feedback.source].append(feedback.feedback_reliability)
        
        # Keep limited history
        if len(self.feedback_source_reliability[feedback.source]) > 50:
            self.feedback_source_reliability[feedback.source] = self.feedback_source_reliability[feedback.source][-50:]
    
    async def _learn_from_feedback(self, deliverable_id: str, feedback: QualityFeedback):
        """Learn and adapt from feedback"""
        
        if len(self.feedback_history[deliverable_id]) < self.min_feedback_for_learning:
            return  # Need more feedback to learn
        
        # Compare feedback to our automated assessment
        if deliverable_id in self.quality_scores:
            our_assessment = self.quality_scores[deliverable_id]
            
            # Calculate assessment accuracy
            accuracy = 1 - abs(our_assessment.overall_score - feedback.overall_rating)
            self.quality_model_accuracy.append(accuracy)
            
            # Learn from dimension score differences
            for dimension, feedback_score in feedback.dimension_scores.items():
                our_score = our_assessment.dimension_scores.get(dimension, 0.7)
                score_diff = feedback_score - our_score
                
                # Adjust future assessments based on this feedback
                await self._adjust_dimension_assessment(dimension, score_diff, feedback.source)
        
        print(f"[QUALITY] Learning from feedback - model accuracy: {self.quality_model_accuracy[-1]:.3f}")
    
    async def _adjust_dimension_assessment(self, dimension: QualityDimension, 
                                         score_diff: float, feedback_source: FeedbackSource):
        """Adjust future dimension assessments based on feedback"""
        
        # Weight the adjustment based on feedback source reliability
        source_reliability = 0.7  # Default
        if feedback_source in self.feedback_source_reliability:
            reliabilities = self.feedback_source_reliability[feedback_source]
            if reliabilities:
                source_reliability = sum(reliabilities) / len(reliabilities)
        
        # Apply learning rate and source reliability
        adjustment = score_diff * self.learning_rate * source_reliability
        
        # Store dimension weight evolution (simplified)
        dimension_key = f"dimension_{dimension.value}_adjustment"
        if dimension_key not in self.dimension_weight_evolution:
            self.dimension_weight_evolution[dimension_key] = []
        
        self.dimension_weight_evolution[dimension_key].append(adjustment)
        
        print(f"[LEARNING] Adjusted {dimension.value} assessment by {adjustment:.3f}")
    
    async def _incorporate_feedback_into_score(self, deliverable_id: str, feedback: QualityFeedback):
        """Incorporate new feedback into existing quality score"""
        
        quality_score = self.quality_scores[deliverable_id]
        
        # Update feedback sources
        quality_score.feedback_sources[feedback.source] = feedback.overall_rating
        
        # Recalculate overall score incorporating feedback
        feedback_weight = feedback.feedback_reliability
        automated_weight = 1.0 - feedback_weight
        
        # Weighted combination of automated assessment and feedback
        new_overall_score = (quality_score.overall_score * automated_weight + 
                           feedback.overall_rating * feedback_weight)
        
        quality_score.overall_score = new_overall_score
        
        # Update confidence (more feedback = higher confidence)
        feedback_count = len(self.feedback_history[deliverable_id])
        quality_score.confidence = min(1.0, quality_score.confidence + feedback_count * 0.05)
    
    async def _create_default_benchmark(self, deliverable_type: str):
        """Create default benchmark for new deliverable type"""
        
        # Use similar deliverable type as template
        template_benchmark = None
        similar_types = {
            "market_analysis": ["competitor_intelligence", "industry_analysis"],
            "growth_strategy": ["investment_recommendation", "strategic_planning"]
        }
        
        for base_type, similar_list in similar_types.items():
            if deliverable_type in similar_list and base_type in self.quality_benchmarks:
                template_benchmark = self.quality_benchmarks[base_type]
                break
        
        if not template_benchmark:
            # Use market_analysis as default template
            template_benchmark = self.quality_benchmarks.get("market_analysis")
        
        if template_benchmark:
            # Create new benchmark based on template
            new_benchmark = QualityBenchmark(
                benchmark_id=f"bench_{deliverable_type}",
                deliverable_type=deliverable_type,
                dimension_thresholds=template_benchmark.dimension_thresholds.copy(),
                industry_standards=template_benchmark.industry_standards.copy(),
                client_segment_expectations=template_benchmark.client_segment_expectations.copy(),
                last_updated=datetime.now().isoformat()
            )
            
            self.quality_benchmarks[deliverable_type] = new_benchmark
    
    def get_quality_dashboard(self) -> Dict[str, Any]:
        """Get comprehensive quality system dashboard"""
        
        # Overall quality metrics
        if self.quality_scores:
            all_scores = [qs.overall_score for qs in self.quality_scores.values()]
            quality_metrics = {
                "average_quality": sum(all_scores) / len(all_scores),
                "quality_range": f"{min(all_scores):.2f} - {max(all_scores):.2f}",
                "high_quality_rate": len([s for s in all_scores if s >= 0.8]) / len(all_scores),
                "total_assessments": len(all_scores)
            }
        else:
            quality_metrics = {"status": "No assessments yet"}
        
        # Model performance
        model_performance = {}
        if self.quality_model_accuracy:
            model_performance = {
                "prediction_accuracy": sum(self.quality_model_accuracy) / len(self.quality_model_accuracy),
                "accuracy_trend": "improving" if len(self.quality_model_accuracy) > 5 and 
                                self.quality_model_accuracy[-5:] > self.quality_model_accuracy[-10:-5] else "stable",
                "total_predictions": len(self.quality_model_accuracy)
            }
        
        # Agent quality summary
        agent_summary = {}
        if self.agent_profiles:
            agent_averages = {agent_id: sum(profile.quality_history) / len(profile.quality_history) 
                            if profile.quality_history else 0
                            for agent_id, profile in self.agent_profiles.items()}
            
            agent_summary = {
                "agents_tracked": len(self.agent_profiles),
                "top_performer": max(agent_averages.items(), key=lambda x: x[1]) if agent_averages else None,
                "average_agent_quality": sum(agent_averages.values()) / len(agent_averages) if agent_averages else 0
            }
        
        # Learning progress
        learning_progress = {
            "feedback_sources_active": len(self.feedback_source_reliability),
            "dimension_adjustments": len(self.dimension_weight_evolution),
            "total_feedback": sum(len(feedback_list) for feedback_list in self.feedback_history.values()),
            "benchmarks_available": len(self.quality_benchmarks)
        }
        
        return {
            "engine_status": {
                "engine_id": self.engine_id,
                "learning_rate": self.learning_rate
            },
            "quality_metrics": quality_metrics,
            "model_performance": model_performance,
            "agent_performance": agent_summary,
            "learning_progress": learning_progress,
            "quality_dimensions": [dim.value for dim in QualityDimension],
            "feedback_sources": [source.value for source in FeedbackSource]
        }

async def main():
    """Demo quality scoring engine"""
    print("SINCOR Self-Improving Quality Scoring Engine Demo")
    print("=" * 52)
    
    # Create quality engine
    engine = SelfImprovingQualityEngine()
    
    # Mock deliverable content
    mock_deliverable = {
        "deliverable_id": "del_001",
        "executive_summary": "Comprehensive analysis of the SaaS market reveals strong growth potential with emerging opportunities in AI-powered solutions.",
        "key_findings": [
            {"finding": "Market growing at 15% CAGR", "confidence": 0.9},
            {"finding": "AI integration is key differentiator", "confidence": 0.85},
            {"finding": "Customer retention improved with advanced analytics", "confidence": 0.8}
        ],
        "recommendations": [
            {
                "recommendation": "Invest in AI-powered features",
                "rationale": "Market analysis shows 40% premium for AI-enabled solutions",
                "implementation_timeline": "6 months",
                "expected_impact": "20-30% revenue increase",
                "resource_requirements": "Medium"
            }
        ],
        "supporting_data": {
            "data_sources": 8,
            "quality_scores": [0.85, 0.8, 0.9, 0.75],
            "completion_rate": 0.9
        },
        "methodology": "Advanced AI swarm intelligence with cross-agent validation and recursive learning",
        "confidence_score": 0.87,
        "completion_time_minutes": 180,
        "agent_contributors": ["E-auriga-01", "E-vega-02", "E-rigel-03"]
    }
    
    # Assess quality
    quality_score = await engine.assess_deliverable_quality(
        deliverable_id="del_001",
        deliverable_content=mock_deliverable,
        deliverable_type="market_analysis",
        agent_id="E-auriga-01",
        client_context={
            "industry": "SaaS",
            "company_size": "mid_market",
            "specific_questions": ["What is market growth rate?", "Which features drive premium pricing?"]
        }
    )
    
    print(f"\nQuality Assessment Results:")
    print(f"Overall Score: {quality_score.overall_score:.3f}")
    print(f"Confidence: {quality_score.confidence:.3f}")
    
    print(f"\nDimension Scores:")
    for dimension, score in quality_score.dimension_scores.items():
        print(f"  {dimension.value}: {score:.3f}")
    
    print(f"\nStrengths: {quality_score.strengths[:2]}")
    print(f"Improvement Areas: {quality_score.improvement_areas[:2]}")
    
    # Simulate client feedback
    client_feedback = QualityFeedback(
        feedback_id="fb_001",
        deliverable_id="del_001",
        source=FeedbackSource.CLIENT_DIRECT,
        dimension_scores={
            QualityDimension.RELEVANCE: 0.95,
            QualityDimension.ACTIONABILITY: 0.90,
            QualityDimension.CLARITY: 0.85
        },
        overall_rating=0.88,
        specific_comments=["Very relevant to our needs", "Clear recommendations"],
        improvement_suggestions=["Could use more industry-specific examples"],
        timestamp=datetime.now().isoformat(),
        feedback_reliability=0.9
    )
    
    await engine.add_external_feedback("del_001", client_feedback)
    
    # Show dashboard
    dashboard = engine.get_quality_dashboard()
    print(f"\nQuality System Dashboard:")
    for section, data in dashboard.items():
        print(f"\n{section.replace('_', ' ').title()}:")
        if isinstance(data, dict):
            for key, value in data.items():
                print(f"  {key}: {value}")
        else:
            print(f"  {data}")

if __name__ == "__main__":
    asyncio.run(main())