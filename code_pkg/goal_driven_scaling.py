#!/usr/bin/env python3
"""
SINCOR Goal-Driven Auto-Scaling Engine
Intelligent scaling based on goal progress and swarm consensus
"""

import asyncio
import time
import math
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum
from datetime import datetime, timedelta
import json
import sqlite3
import uuid

from swarm_intelligence_lifecycle import (
    swarm_manager, SwarmDecisionType, VoteType, GoalTracker
)
from permission_manager import permission_manager, ResourceType

class ScalingStrategy(Enum):
    CONSERVATIVE = "conservative"  # Gradual scaling
    AGGRESSIVE = "aggressive"      # Fast scaling
    PREDICTIVE = "predictive"      # ML-based scaling
    EMERGENCY = "emergency"        # Immediate maximum scaling

class ScalingTrigger(Enum):
    GOAL_BEHIND_SCHEDULE = "goal_behind_schedule"
    GOAL_AHEAD_SCHEDULE = "goal_ahead_schedule" 
    RESOURCE_UTILIZATION = "resource_utilization"
    QUALITY_DEGRADATION = "quality_degradation"
    DEADLINE_PRESSURE = "deadline_pressure"
    SWARM_INEFFICIENCY = "swarm_inefficiency"

@dataclass
class ScalingRecommendation:
    goal_id: str
    trigger: ScalingTrigger
    strategy: ScalingStrategy
    recommended_agents: int
    confidence: float
    reasoning: str
    urgency: str
    estimated_cost: float
    expected_improvement: Dict[str, float]

@dataclass
class GoalMetrics:
    goal_id: str
    current_velocity: float  # progress units per hour
    required_velocity: float
    efficiency_score: float  # 0-1
    quality_score: float     # 0-1
    time_utilization: float  # 0-1
    predicted_completion: datetime
    confidence_interval: float

class GoalDrivenScalingEngine:
    """Advanced goal-driven auto-scaling with swarm intelligence"""
    
    def __init__(self, db_path: str = "goal_scaling.db"):
        self.db_path = db_path
        self.active_recommendations: Dict[str, ScalingRecommendation] = {}
        self.goal_metrics: Dict[str, GoalMetrics] = {}
        self.scaling_history: List[Dict[str, Any]] = []
        self.running = False
        
        self._setup_database()
        
    def _setup_database(self):
        """Initialize goal scaling database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Goal metrics table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS goal_metrics (
            goal_id TEXT PRIMARY KEY,
            timestamp REAL,
            current_velocity REAL,
            required_velocity REAL,
            efficiency_score REAL,
            quality_score REAL,
            time_utilization REAL,
            predicted_completion TEXT,
            confidence_interval REAL
        )
        ''')
        
        # Scaling history table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS scaling_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id TEXT,
            timestamp REAL,
            trigger_type TEXT,
            strategy TEXT,
            agents_before INTEGER,
            agents_after INTEGER,
            decision_id TEXT,
            success BOOLEAN,
            impact_metrics TEXT
        )
        ''')
        
        # Performance tracking table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS scaling_performance (
            goal_id TEXT,
            scaling_action_id INTEGER,
            timestamp REAL,
            velocity_improvement REAL,
            efficiency_change REAL,
            quality_change REAL,
            cost_effectiveness REAL,
            PRIMARY KEY (goal_id, scaling_action_id, timestamp)
        )
        ''')
        
        conn.commit()
        conn.close()
    
    async def start_goal_monitoring(self):
        """Start continuous goal monitoring and scaling"""
        self.running = True
        
        print(">> Goal-Driven Auto-Scaling: ACTIVE")
        
        while self.running:
            try:
                # Analyze all active goals
                for goal_id, goal in swarm_manager.goal_trackers.items():
                    await self._analyze_goal_performance(goal)
                    
                    # Generate scaling recommendations
                    recommendation = await self._generate_scaling_recommendation(goal)
                    
                    if recommendation:
                        await self._process_scaling_recommendation(recommendation)
                
                # Clean up old recommendations
                await self._cleanup_old_recommendations()
                
                await asyncio.sleep(10)  # Check every 10 seconds
                
            except Exception as e:
                print(f"Goal monitoring error: {e}")
                await asyncio.sleep(30)
    
    async def _analyze_goal_performance(self, goal: GoalTracker):
        """Analyze goal performance metrics"""
        
        # Calculate time remaining
        time_remaining = (goal.deadline - datetime.now()).total_seconds()
        progress_percentage = goal.current_progress / goal.target_value
        
        if time_remaining <= 0:
            return  # Goal expired
        
        # Estimate current velocity (progress per hour)
        # This would integrate with actual agent performance data
        current_velocity = self._estimate_current_velocity(goal)
        
        # Calculate required velocity to meet deadline
        remaining_work = goal.target_value - goal.current_progress
        required_velocity = remaining_work / (time_remaining / 3600)
        
        # Calculate efficiency metrics
        assigned_agents = len(goal.assigned_agents)
        efficiency_score = min(1.0, current_velocity / (assigned_agents * 2.0)) if assigned_agents > 0 else 0.0
        
        # Quality score (would integrate with actual quality metrics)
        quality_score = self._estimate_quality_score(goal)
        
        # Time utilization (how well we're using available time)
        time_utilization = progress_percentage / (1.0 - (time_remaining / ((goal.deadline - datetime.now()).total_seconds() + time_remaining)))
        
        # Predict completion time
        if current_velocity > 0:
            hours_to_completion = remaining_work / current_velocity
            predicted_completion = datetime.now() + timedelta(hours=hours_to_completion)
        else:
            predicted_completion = goal.deadline + timedelta(days=999)  # Far future if no progress
        
        # Confidence interval based on velocity consistency
        confidence_interval = self._calculate_confidence_interval(goal)
        
        metrics = GoalMetrics(
            goal_id=goal.goal_id,
            current_velocity=current_velocity,
            required_velocity=required_velocity,
            efficiency_score=efficiency_score,
            quality_score=quality_score,
            time_utilization=time_utilization,
            predicted_completion=predicted_completion,
            confidence_interval=confidence_interval
        )
        
        self.goal_metrics[goal.goal_id] = metrics
        await self._store_goal_metrics(metrics)
    
    async def _generate_scaling_recommendation(self, goal: GoalTracker) -> Optional[ScalingRecommendation]:
        """Generate intelligent scaling recommendation"""
        
        if goal.goal_id not in self.goal_metrics:
            return None
        
        metrics = self.goal_metrics[goal.goal_id]
        
        # Check various scaling triggers
        trigger = None
        strategy = ScalingStrategy.CONSERVATIVE
        recommended_agents = 0
        urgency = "low"
        
        # 1. Goal behind schedule
        if metrics.current_velocity < metrics.required_velocity * 0.8:
            trigger = ScalingTrigger.GOAL_BEHIND_SCHEDULE
            velocity_gap = metrics.required_velocity - metrics.current_velocity
            recommended_agents = max(1, int(velocity_gap / 2.0))  # Rough estimate
            
            time_remaining = (goal.deadline - datetime.now()).total_seconds() / 3600
            if time_remaining < 24:  # Less than 24 hours
                strategy = ScalingStrategy.EMERGENCY
                urgency = "critical"
                recommended_agents *= 3
            elif time_remaining < 72:  # Less than 3 days
                strategy = ScalingStrategy.AGGRESSIVE
                urgency = "high"
                recommended_agents *= 2
        
        # 2. Efficiency degradation
        elif metrics.efficiency_score < 0.6:
            trigger = ScalingTrigger.SWARM_INEFFICIENCY
            # Sometimes adding more agents reduces efficiency, so be careful
            if len(goal.assigned_agents) > 10:
                recommended_agents = -2  # Scale down
            else:
                recommended_agents = 3  # Scale up moderately
        
        # 3. Quality issues
        elif metrics.quality_score < 0.7:
            trigger = ScalingTrigger.QUALITY_DEGRADATION
            recommended_agents = 2  # Add specialized agents
            strategy = ScalingStrategy.PREDICTIVE
        
        # 4. Ahead of schedule - scale down to save resources
        elif metrics.current_velocity > metrics.required_velocity * 1.5:
            trigger = ScalingTrigger.GOAL_AHEAD_SCHEDULE
            recommended_agents = -1  # Scale down
            urgency = "low"
        
        if trigger is None:
            return None
        
        # Check user permissions for scaling
        user_permissions = permission_manager.users.get(goal.user_id)
        if user_permissions:
            max_allowed_agents = user_permissions.max_agents
            current_agents = len(goal.assigned_agents)
            
            if current_agents + recommended_agents > max_allowed_agents:
                if user_permissions.permission_level.value != "god_mode":
                    recommended_agents = max_allowed_agents - current_agents
        
        # Calculate estimated cost
        estimated_cost = self._calculate_scaling_cost(goal, recommended_agents)
        
        # Expected improvement metrics
        expected_improvement = self._calculate_expected_improvement(goal, recommended_agents, strategy)
        
        # Generate reasoning
        reasoning = self._generate_scaling_reasoning(trigger, metrics, recommended_agents, strategy)
        
        recommendation = ScalingRecommendation(
            goal_id=goal.goal_id,
            trigger=trigger,
            strategy=strategy,
            recommended_agents=recommended_agents,
            confidence=metrics.confidence_interval,
            reasoning=reasoning,
            urgency=urgency,
            estimated_cost=estimated_cost,
            expected_improvement=expected_improvement
        )
        
        return recommendation
    
    async def _process_scaling_recommendation(self, recommendation: ScalingRecommendation):
        """Process scaling recommendation through swarm voting or god mode"""
        
        goal = swarm_manager.goal_trackers[recommendation.goal_id]
        
        print(f">> SCALING RECOMMENDATION: {recommendation.goal_id}")
        print(f"   Trigger: {recommendation.trigger.value}")
        print(f"   Strategy: {recommendation.strategy.value}")
        print(f"   Recommended Agents: {recommendation.recommended_agents}")
        print(f"   Urgency: {recommendation.urgency}")
        print(f"   Reasoning: {recommendation.reasoning}")
        
        # Create swarm decision for scaling
        scaling_proposal = {
            "goal_id": recommendation.goal_id,
            "goal_description": goal.description,
            "agent_count": abs(recommendation.recommended_agents),
            "action": "scale_up" if recommendation.recommended_agents > 0 else "scale_down",
            "strategy": recommendation.strategy.value,
            "trigger": recommendation.trigger.value,
            "urgency": recommendation.urgency,
            "estimated_cost": recommendation.estimated_cost,
            "expected_improvement": recommendation.expected_improvement,
            "reasoning": recommendation.reasoning,
            "confidence": recommendation.confidence
        }
        
        decision_type = SwarmDecisionType.SCALE_UP if recommendation.recommended_agents > 0 else SwarmDecisionType.SCALE_DOWN
        
        # Submit to swarm for voting (or immediate execution if god mode)
        decision_id = await swarm_manager.create_swarm_decision(
            decision_type=decision_type,
            proposal=scaling_proposal,
            user_id=goal.user_id
        )
        
        # Store recommendation with decision ID
        self.active_recommendations[recommendation.goal_id] = recommendation
        
        # Log scaling attempt
        await self._log_scaling_attempt(recommendation, decision_id)
    
    def _estimate_current_velocity(self, goal: GoalTracker) -> float:
        """Estimate current progress velocity"""
        # This would integrate with actual agent performance data
        # For now, simulate based on assigned agents and time
        
        base_velocity = len(goal.assigned_agents) * 2.0  # 2 units per agent per hour
        
        # Apply efficiency modifiers based on goal type and time
        if "lead" in goal.description.lower():
            base_velocity *= 1.2  # Lead generation is faster
        elif "analysis" in goal.description.lower():
            base_velocity *= 0.8  # Analysis takes longer
        
        # Random variation to simulate real-world performance
        import random
        variation = random.uniform(0.7, 1.3)
        
        return base_velocity * variation
    
    def _estimate_quality_score(self, goal: GoalTracker) -> float:
        """Estimate current quality score"""
        # This would integrate with actual quality metrics
        # For now, simulate based on agent count and time pressure
        
        time_remaining = (goal.deadline - datetime.now()).total_seconds() / 3600
        agent_count = len(goal.assigned_agents)
        
        # Quality decreases with too many agents or too much time pressure
        quality = 0.9
        
        if agent_count > 20:
            quality -= (agent_count - 20) * 0.02  # Coordination overhead
        
        if time_remaining < 12:
            quality -= 0.3  # Rush penalty
        elif time_remaining < 48:
            quality -= 0.1  # Moderate time pressure
        
        return max(0.1, min(1.0, quality))
    
    def _calculate_confidence_interval(self, goal: GoalTracker) -> float:
        """Calculate confidence interval for predictions"""
        # This would analyze historical velocity data
        # For now, return a reasonable estimate
        
        agent_count = len(goal.assigned_agents)
        time_remaining = (goal.deadline - datetime.now()).total_seconds() / 3600
        
        confidence = 0.8
        
        # More agents = more predictable
        if agent_count > 5:
            confidence += 0.1
        
        # More time = less predictable
        if time_remaining > 168:  # > 1 week
            confidence -= 0.2
        
        return max(0.3, min(0.95, confidence))
    
    def _calculate_scaling_cost(self, goal: GoalTracker, agent_count: int) -> float:
        """Calculate estimated cost for scaling"""
        
        user_permissions = permission_manager.users.get(goal.user_id)
        if not user_permissions:
            return 0.0
        
        cost_per_agent_hour = user_permissions.cost_per_agent_hour
        time_remaining = (goal.deadline - datetime.now()).total_seconds() / 3600
        
        return abs(agent_count) * cost_per_agent_hour * min(time_remaining, 24)  # Max 24 hours
    
    def _calculate_expected_improvement(self, goal: GoalTracker, agent_count: int, 
                                      strategy: ScalingStrategy) -> Dict[str, float]:
        """Calculate expected improvement metrics"""
        
        if agent_count > 0:
            velocity_improvement = agent_count * 1.8  # Conservative estimate
            efficiency_change = -0.05 if agent_count > 10 else 0.02  # Coordination overhead
            quality_change = 0.1 if strategy == ScalingStrategy.PREDICTIVE else 0.0
        else:
            velocity_improvement = agent_count * 2.0  # Negative for scale-down
            efficiency_change = 0.1  # Less coordination overhead
            quality_change = 0.05  # Better focus
        
        completion_time_reduction = velocity_improvement / max(1, self.goal_metrics[goal.goal_id].required_velocity) * 24  # hours
        
        return {
            "velocity_improvement_pct": velocity_improvement,
            "efficiency_change_pct": efficiency_change * 100,
            "quality_change_pct": quality_change * 100,
            "completion_time_reduction_hours": completion_time_reduction
        }
    
    def _generate_scaling_reasoning(self, trigger: ScalingTrigger, metrics: GoalMetrics, 
                                  agent_count: int, strategy: ScalingStrategy) -> str:
        """Generate human-readable scaling reasoning"""
        
        if trigger == ScalingTrigger.GOAL_BEHIND_SCHEDULE:
            return f"Goal is {((metrics.required_velocity - metrics.current_velocity) / metrics.required_velocity * 100):.1f}% behind target velocity. Need {agent_count} more agents using {strategy.value} strategy."
        
        elif trigger == ScalingTrigger.SWARM_INEFFICIENCY:
            return f"Current efficiency at {metrics.efficiency_score:.1%}. {'Reducing' if agent_count < 0 else 'Adding'} {abs(agent_count)} agents to optimize coordination."
        
        elif trigger == ScalingTrigger.QUALITY_DEGRADATION:
            return f"Quality score at {metrics.quality_score:.1%}. Adding {agent_count} specialized agents to improve output quality."
        
        elif trigger == ScalingTrigger.GOAL_AHEAD_SCHEDULE:
            return f"Goal is ahead of schedule by {((metrics.current_velocity - metrics.required_velocity) / metrics.required_velocity * 100):.1f}%. Scaling down {abs(agent_count)} agents to optimize resources."
        
        return f"Scaling {agent_count} agents based on {trigger.value} using {strategy.value} strategy."
    
    async def _store_goal_metrics(self, metrics: GoalMetrics):
        """Store goal metrics in database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT OR REPLACE INTO goal_metrics 
        (goal_id, timestamp, current_velocity, required_velocity, efficiency_score,
         quality_score, time_utilization, predicted_completion, confidence_interval)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            metrics.goal_id,
            time.time(),
            metrics.current_velocity,
            metrics.required_velocity,
            metrics.efficiency_score,
            metrics.quality_score,
            metrics.time_utilization,
            metrics.predicted_completion.isoformat(),
            metrics.confidence_interval
        ))
        
        conn.commit()
        conn.close()
    
    async def _log_scaling_attempt(self, recommendation: ScalingRecommendation, decision_id: str):
        """Log scaling attempt for analysis"""
        
        goal = swarm_manager.goal_trackers[recommendation.goal_id]
        
        scaling_record = {
            "goal_id": recommendation.goal_id,
            "timestamp": time.time(),
            "trigger_type": recommendation.trigger.value,
            "strategy": recommendation.strategy.value,
            "agents_before": len(goal.assigned_agents),
            "agents_after": len(goal.assigned_agents) + recommendation.recommended_agents,
            "decision_id": decision_id,
            "success": None,  # Will be updated when decision completes
            "impact_metrics": asdict(recommendation.expected_improvement)
        }
        
        self.scaling_history.append(scaling_record)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT INTO scaling_history 
        (goal_id, timestamp, trigger_type, strategy, agents_before, agents_after,
         decision_id, success, impact_metrics)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            scaling_record["goal_id"],
            scaling_record["timestamp"],
            scaling_record["trigger_type"],
            scaling_record["strategy"],
            scaling_record["agents_before"],
            scaling_record["agents_after"],
            scaling_record["decision_id"],
            scaling_record["success"],
            json.dumps(scaling_record["impact_metrics"])
        ))
        
        conn.commit()
        conn.close()
    
    async def _cleanup_old_recommendations(self):
        """Clean up old recommendations"""
        current_time = time.time()
        cutoff_time = current_time - 3600  # 1 hour ago
        
        to_remove = []
        for goal_id, recommendation in self.active_recommendations.items():
            # Check if the related decision has been executed
            # This would integrate with the swarm decision system
            # For now, just remove old recommendations
            if current_time - cutoff_time > 3600:  # Simplified cleanup
                to_remove.append(goal_id)
        
        for goal_id in to_remove:
            del self.active_recommendations[goal_id]
    
    def get_scaling_dashboard_data(self) -> Dict[str, Any]:
        """Get data for scaling dashboard"""
        
        active_goals_metrics = {}
        for goal_id, metrics in self.goal_metrics.items():
            if goal_id in swarm_manager.goal_trackers:
                goal = swarm_manager.goal_trackers[goal_id]
                active_goals_metrics[goal_id] = {
                    "goal_description": goal.description,
                    "current_velocity": metrics.current_velocity,
                    "required_velocity": metrics.required_velocity,
                    "efficiency_score": metrics.efficiency_score,
                    "quality_score": metrics.quality_score,
                    "predicted_completion": metrics.predicted_completion.isoformat(),
                    "confidence": metrics.confidence_interval,
                    "assigned_agents": len(goal.assigned_agents)
                }
        
        active_recommendations_data = {}
        for goal_id, recommendation in self.active_recommendations.items():
            active_recommendations_data[goal_id] = asdict(recommendation)
        
        return {
            "active_goals_metrics": active_goals_metrics,
            "active_recommendations": active_recommendations_data,
            "scaling_history_count": len(self.scaling_history),
            "system_status": "ACTIVE" if self.running else "INACTIVE"
        }

# Global scaling engine
scaling_engine = GoalDrivenScalingEngine()

async def create_sample_goal_scenario():
    """Create sample goal for testing"""
    
    # Create "50 leads by Friday" goal
    from swarm_intelligence_lifecycle import create_lead_generation_goal
    
    goal_id = await create_lead_generation_goal(50, 3)  # 50 leads in 3 days
    
    print(f">> Created sample goal: {goal_id}")
    
    # Simulate some progress
    await swarm_manager.update_goal_progress(goal_id, 15.0)
    
    return goal_id

if __name__ == "__main__":
    print(">> SINCOR Goal-Driven Auto-Scaling Engine")
    print("   Intelligent Scaling: ACTIVE")
    print("   Swarm Consensus: INTEGRATED")
    print("   Predictive Analytics: ENABLED")
    
    # Start the scaling engine
    asyncio.run(scaling_engine.start_goal_monitoring())