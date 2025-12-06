#!/usr/bin/env python3
"""
SINCOR Infinite Agent Scaling Engine

Creates new agents at $1 cost point that immediately generate enough revenue
to fund their own operation and create additional agents exponentially:

- Agent ROI tracking and optimization
- Dynamic agent spawning based on demand
- Cost-per-agent optimization 
- Revenue-per-agent tracking
- Exponential scaling algorithms
- Resource allocation optimization
"""

import json
import asyncio
import os
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from enum import Enum
import uuid
import math

@dataclass
class AgentEconomics:
    """Economic metrics for individual agents"""
    agent_id: str
    spawn_cost: float
    operational_cost_per_hour: float
    revenue_generated: float
    tasks_completed: int
    hours_active: float
    roi: float  # Return on investment
    payback_period_hours: float
    created: str
    last_updated: str

@dataclass  
class ScalingEvent:
    """Event in the scaling process"""
    event_id: str
    event_type: str  # spawn, shutdown, optimize, rebalance
    trigger: str  # What triggered this event
    agents_affected: List[str]
    cost_impact: float
    revenue_impact: float
    timestamp: str

class AgentArchetype(Enum):
    """Optimized agent archetypes for scaling"""
    MICRO_SCOUT = "micro_scout"           # $0.50 cost, simple prospecting
    NANO_ANALYZER = "nano_analyzer"       # $0.75 cost, basic analysis
    STANDARD_AGENT = "standard_agent"     # $1.00 cost, full capabilities
    PREMIUM_SPECIALIST = "premium_specialist"  # $2.00 cost, expert-level
    SWARM_COORDINATOR = "swarm_coordinator"    # $5.00 cost, manages 20+ agents

@dataclass
class ScalingTarget:
    """Target configuration for scaling"""
    target_agents: int
    target_revenue_per_hour: float
    max_spawn_cost: float
    min_roi_threshold: float
    target_payback_hours: float
    archetype_distribution: Dict[AgentArchetype, float]

class InfiniteScalingEngine:
    """Engine for infinite agent scaling at $1 cost point"""
    
    def __init__(self):
        self.scaling_id = f"scale_{uuid.uuid4().hex[:8]}"
        
        # Economic tracking
        self.agent_economics = {}  # agent_id -> AgentEconomics
        self.scaling_events = []
        self.total_investment = 0.0
        self.total_revenue = 0.0
        self.net_profit = 0.0
        
        # Scaling parameters
        self.base_spawn_cost = 1.0  # $1 target
        self.min_roi_threshold = 3.0  # 300% ROI minimum
        self.target_payback_hours = 4.0  # Must pay for itself in 4 hours
        self.scaling_factor = 1.5  # Exponential scaling factor
        
        # Agent lifecycle optimization
        self.archetype_configs = self._initialize_archetype_configs()
        self.demand_prediction = {}
        self.resource_pool = {"cpu": 1000, "memory": 10000, "api_calls": 100000}
        
        # Performance optimization
        self.optimization_cycles = 0
        self.scaling_efficiency = []
        
    def _initialize_archetype_configs(self) -> Dict[AgentArchetype, Dict[str, Any]]:
        """Initialize optimized configurations for each agent archetype"""
        
        return {
            AgentArchetype.MICRO_SCOUT: {
                "spawn_cost": 0.50,
                "hourly_operational_cost": 0.10,
                "expected_revenue_per_hour": 2.00,
                "resource_requirements": {"cpu": 5, "memory": 50, "api_calls": 20},
                "capabilities": ["basic_prospecting", "lead_discovery"],
                "target_roi": 4.0,
                "scaling_priority": 1
            },
            AgentArchetype.NANO_ANALYZER: {
                "spawn_cost": 0.75,
                "hourly_operational_cost": 0.15,
                "expected_revenue_per_hour": 3.50,
                "resource_requirements": {"cpu": 8, "memory": 80, "api_calls": 35},
                "capabilities": ["data_analysis", "basic_insights", "report_generation"],
                "target_roi": 4.7,
                "scaling_priority": 2
            },
            AgentArchetype.STANDARD_AGENT: {
                "spawn_cost": 1.00,
                "hourly_operational_cost": 0.25,
                "expected_revenue_per_hour": 6.00,
                "resource_requirements": {"cpu": 15, "memory": 150, "api_calls": 60},
                "capabilities": ["market_research", "competitive_analysis", "strategic_insights"],
                "target_roi": 6.0,
                "scaling_priority": 3
            },
            AgentArchetype.PREMIUM_SPECIALIST: {
                "spawn_cost": 2.00,
                "hourly_operational_cost": 0.50,
                "expected_revenue_per_hour": 15.00,
                "resource_requirements": {"cpu": 30, "memory": 300, "api_calls": 120},
                "capabilities": ["expert_analysis", "complex_strategy", "high_value_insights"],
                "target_roi": 7.5,
                "scaling_priority": 4
            },
            AgentArchetype.SWARM_COORDINATOR: {
                "spawn_cost": 5.00,
                "hourly_operational_cost": 1.00,
                "expected_revenue_per_hour": 40.00,
                "resource_requirements": {"cpu": 75, "memory": 750, "api_calls": 300},
                "capabilities": ["swarm_management", "task_coordination", "resource_optimization"],
                "target_roi": 8.0,
                "scaling_priority": 5
            }
        }
    
    async def spawn_optimal_agent(self, demand_context: Dict[str, Any]) -> str:
        """Spawn the most economically optimal agent for current demand"""
        
        # Analyze demand to determine optimal archetype
        optimal_archetype = await self._determine_optimal_archetype(demand_context)
        
        # Check resource availability
        if not self._check_resource_availability(optimal_archetype):
            # Try to optimize existing agents or scale resources
            await self._optimize_resource_allocation()
            
            if not self._check_resource_availability(optimal_archetype):
                raise Exception(f"Insufficient resources to spawn {optimal_archetype.value}")
        
        # Generate agent identity
        agent_id = f"E-scale-{uuid.uuid4().hex[:8]}"
        
        # Calculate economics
        config = self.archetype_configs[optimal_archetype]
        spawn_cost = config["spawn_cost"]
        
        # Ensure we stay at or below target cost
        if spawn_cost > self.base_spawn_cost and optimal_archetype != AgentArchetype.SWARM_COORDINATOR:
            # Apply cost optimization
            spawn_cost = await self._optimize_spawn_cost(optimal_archetype, demand_context)
        
        # Create agent economics tracking
        economics = AgentEconomics(
            agent_id=agent_id,
            spawn_cost=spawn_cost,
            operational_cost_per_hour=config["hourly_operational_cost"],
            revenue_generated=0.0,
            tasks_completed=0,
            hours_active=0.0,
            roi=0.0,
            payback_period_hours=float('inf'),
            created=datetime.now().isoformat(),
            last_updated=datetime.now().isoformat()
        )
        
        # Reserve resources
        self._allocate_resources(optimal_archetype)
        
        # Track investment
        self.total_investment += spawn_cost
        
        # Store economics
        self.agent_economics[agent_id] = economics
        
        # Record scaling event
        event = ScalingEvent(
            event_id=f"spawn_{uuid.uuid4().hex[:8]}",
            event_type="spawn",
            trigger=f"demand_context: {demand_context.get('type', 'unknown')}",
            agents_affected=[agent_id],
            cost_impact=spawn_cost,
            revenue_impact=config["expected_revenue_per_hour"] * 8,  # 8-hour projection
            timestamp=datetime.now().isoformat()
        )
        self.scaling_events.append(event)
        
        print(f"[SCALING] Spawned {optimal_archetype.value} agent {agent_id} for ${spawn_cost:.2f}")
        print(f"[SCALING] Expected ROI: {config['target_roi']:.1f}x, Payback: {spawn_cost/config['expected_revenue_per_hour']:.1f} hours")
        
        return agent_id
    
    async def _determine_optimal_archetype(self, demand_context: Dict[str, Any]) -> AgentArchetype:
        """Determine the most cost-effective agent archetype for demand"""
        
        task_complexity = demand_context.get("complexity", 1)
        urgency = demand_context.get("urgency", "standard")
        budget = demand_context.get("budget", 1000)
        expected_revenue = demand_context.get("expected_revenue", 100)
        
        # Calculate value density (revenue per dollar of cost)
        archetype_scores = {}
        
        for archetype, config in self.archetype_configs.items():
            # Base score from ROI
            base_score = config["target_roi"]
            
            # Adjust for task complexity match
            complexity_match = self._calculate_complexity_match(archetype, task_complexity)
            
            # Adjust for urgency requirements
            urgency_bonus = self._calculate_urgency_bonus(archetype, urgency)
            
            # Adjust for budget constraints
            budget_fit = self._calculate_budget_fit(config["spawn_cost"], budget)
            
            # Adjust for expected revenue
            revenue_efficiency = config["expected_revenue_per_hour"] / config["spawn_cost"]
            
            # Combined score
            total_score = (base_score * complexity_match * budget_fit * 
                         (1 + urgency_bonus) * (revenue_efficiency / 10))
            
            archetype_scores[archetype] = total_score
        
        # Select highest scoring archetype
        optimal_archetype = max(archetype_scores.items(), key=lambda x: x[1])[0]
        
        print(f"[OPTIMIZATION] Selected {optimal_archetype.value} with score {archetype_scores[optimal_archetype]:.2f}")
        
        return optimal_archetype
    
    def _calculate_complexity_match(self, archetype: AgentArchetype, complexity: int) -> float:
        """Calculate how well archetype matches task complexity"""
        
        archetype_complexity = {
            AgentArchetype.MICRO_SCOUT: 1,
            AgentArchetype.NANO_ANALYZER: 2,
            AgentArchetype.STANDARD_AGENT: 3,
            AgentArchetype.PREMIUM_SPECIALIST: 4,
            AgentArchetype.SWARM_COORDINATOR: 5
        }
        
        agent_complexity = archetype_complexity[archetype]
        
        # Perfect match = 1.0, over/under-qualified reduces score
        if agent_complexity == complexity:
            return 1.0
        elif agent_complexity > complexity:
            return 0.7  # Over-qualified, less efficient
        else:
            return 0.5  # Under-qualified, may fail
    
    def _calculate_urgency_bonus(self, archetype: AgentArchetype, urgency: str) -> float:
        """Calculate urgency bonus multiplier"""
        
        if urgency == "emergency":
            # Premium agents get bigger bonus for emergency work
            return {
                AgentArchetype.MICRO_SCOUT: 0.1,
                AgentArchetype.NANO_ANALYZER: 0.2,
                AgentArchetype.STANDARD_AGENT: 0.3,
                AgentArchetype.PREMIUM_SPECIALIST: 0.5,
                AgentArchetype.SWARM_COORDINATOR: 0.7
            }[archetype]
        elif urgency == "priority":
            return 0.2
        else:
            return 0.0
    
    def _calculate_budget_fit(self, spawn_cost: float, budget: float) -> float:
        """Calculate how well spawn cost fits within budget"""
        
        cost_ratio = spawn_cost / budget
        
        if cost_ratio <= 0.01:  # Less than 1% of budget
            return 1.0
        elif cost_ratio <= 0.05:  # Less than 5% of budget  
            return 0.8
        elif cost_ratio <= 0.10:  # Less than 10% of budget
            return 0.6
        else:
            return 0.3  # Too expensive
    
    def _check_resource_availability(self, archetype: AgentArchetype) -> bool:
        """Check if resources are available for spawning agent"""
        
        requirements = self.archetype_configs[archetype]["resource_requirements"]
        
        return (self.resource_pool["cpu"] >= requirements["cpu"] and
                self.resource_pool["memory"] >= requirements["memory"] and
                self.resource_pool["api_calls"] >= requirements["api_calls"])
    
    def _allocate_resources(self, archetype: AgentArchetype):
        """Allocate resources for new agent"""
        
        requirements = self.archetype_configs[archetype]["resource_requirements"]
        
        self.resource_pool["cpu"] -= requirements["cpu"]
        self.resource_pool["memory"] -= requirements["memory"]
        self.resource_pool["api_calls"] -= requirements["api_calls"]
    
    async def _optimize_spawn_cost(self, archetype: AgentArchetype, 
                                 demand_context: Dict[str, Any]) -> float:
        """Optimize spawn cost while maintaining performance"""
        
        base_cost = self.archetype_configs[archetype]["spawn_cost"]
        
        # Apply optimization techniques
        optimizations = []
        
        # 1. Batch spawning discount
        if demand_context.get("batch_size", 1) > 1:
            batch_discount = min(0.2, 0.05 * demand_context["batch_size"])
            optimizations.append(("batch_discount", -batch_discount))
        
        # 2. Resource sharing optimization
        if self._can_share_resources(archetype):
            sharing_discount = 0.15
            optimizations.append(("resource_sharing", -sharing_discount))
        
        # 3. Pre-trained model reuse
        if self._has_pretrained_models(archetype):
            training_discount = 0.10
            optimizations.append(("pretrained_reuse", -training_discount))
        
        # 4. Peak hour pricing adjustment
        if self._is_off_peak_hour():
            off_peak_discount = 0.1
            optimizations.append(("off_peak", -off_peak_discount))
        
        # Apply optimizations
        total_discount = sum(discount for _, discount in optimizations)
        optimized_cost = base_cost * (1 + total_discount)  # total_discount is negative
        
        # Ensure minimum viability
        min_cost = base_cost * 0.5  # Never go below 50% of base cost
        optimized_cost = max(optimized_cost, min_cost)
        
        if optimizations:
            print(f"[OPTIMIZATION] Applied {len(optimizations)} optimizations: {optimized_cost:.2f} vs {base_cost:.2f}")
        
        return optimized_cost
    
    def _can_share_resources(self, archetype: AgentArchetype) -> bool:
        """Check if agent can share resources with existing agents"""
        # Simplified - in practice would check for compatible agent clusters
        return len(self.agent_economics) > 5
    
    def _has_pretrained_models(self, archetype: AgentArchetype) -> bool:
        """Check if pretrained models exist for this archetype"""
        # Simplified - would check model repository
        existing_archetypes = [self._get_agent_archetype(econ) for econ in self.agent_economics.values()]
        return archetype in existing_archetypes
    
    def _is_off_peak_hour(self) -> bool:
        """Check if current time is off-peak for resource usage"""
        current_hour = datetime.now().hour
        return 0 <= current_hour <= 6 or 22 <= current_hour <= 23
    
    def _get_agent_archetype(self, economics: AgentEconomics) -> AgentArchetype:
        """Determine archetype from agent economics (simplified)"""
        
        if economics.spawn_cost <= 0.50:
            return AgentArchetype.MICRO_SCOUT
        elif economics.spawn_cost <= 0.75:
            return AgentArchetype.NANO_ANALYZER  
        elif economics.spawn_cost <= 1.00:
            return AgentArchetype.STANDARD_AGENT
        elif economics.spawn_cost <= 2.00:
            return AgentArchetype.PREMIUM_SPECIALIST
        else:
            return AgentArchetype.SWARM_COORDINATOR
    
    async def update_agent_performance(self, agent_id: str, revenue_increment: float, 
                                     hours_worked: float, tasks_completed: int):
        """Update agent performance metrics for ROI calculation"""
        
        if agent_id not in self.agent_economics:
            return
            
        economics = self.agent_economics[agent_id]
        
        # Update metrics
        economics.revenue_generated += revenue_increment
        economics.hours_active += hours_worked
        economics.tasks_completed += tasks_completed
        
        # Calculate operational costs
        operational_costs = economics.hours_active * economics.operational_cost_per_hour
        total_costs = economics.spawn_cost + operational_costs
        
        # Calculate ROI
        if total_costs > 0:
            economics.roi = economics.revenue_generated / total_costs
        
        # Calculate payback period
        if revenue_increment > 0:
            net_profit = economics.revenue_generated - total_costs
            if net_profit >= 0:
                economics.payback_period_hours = economics.hours_active
            else:
                # Estimate remaining payback time
                hourly_profit = revenue_increment / hours_worked - economics.operational_cost_per_hour
                if hourly_profit > 0:
                    remaining_deficit = abs(net_profit)
                    economics.payback_period_hours = economics.hours_active + (remaining_deficit / hourly_profit)
        
        economics.last_updated = datetime.now().isoformat()
        
        # Update global revenue tracking
        self.total_revenue += revenue_increment
        self.net_profit = self.total_revenue - self.total_investment
        
        # Check for scaling triggers
        await self._check_scaling_triggers(agent_id, economics)
    
    async def _check_scaling_triggers(self, agent_id: str, economics: AgentEconomics):
        """Check if agent performance triggers scaling decisions"""
        
        # Trigger 1: High-performing agent should spawn similar agents
        if (economics.roi >= self.min_roi_threshold * 1.5 and 
            economics.payback_period_hours <= self.target_payback_hours * 0.5):
            
            await self._trigger_replication(agent_id, "high_performance")
        
        # Trigger 2: Low-performing agent should be optimized or shut down
        elif (economics.roi < self.min_roi_threshold * 0.7 and 
              economics.hours_active > self.target_payback_hours * 2):
            
            await self._trigger_optimization_or_shutdown(agent_id, "low_performance")
        
        # Trigger 3: Market demand indicates need for more agents
        if self._detect_demand_spike():
            await self._trigger_demand_scaling("market_demand")
    
    async def _trigger_replication(self, high_performer_id: str, reason: str):
        """Replicate a high-performing agent"""
        
        economics = self.agent_economics[high_performer_id]
        archetype = self._get_agent_archetype(economics)
        
        # Create demand context based on high performer
        demand_context = {
            "type": "replication",
            "source_agent": high_performer_id,
            "proven_roi": economics.roi,
            "complexity": 3,  # Standard complexity
            "urgency": "standard",
            "budget": economics.revenue_generated,
            "expected_revenue": economics.revenue_generated / economics.hours_active * 8  # 8-hour projection
        }
        
        try:
            new_agent_id = await self.spawn_optimal_agent(demand_context)
            
            event = ScalingEvent(
                event_id=f"replicate_{uuid.uuid4().hex[:8]}",
                event_type="spawn",
                trigger=f"replication_{reason}_{high_performer_id}",
                agents_affected=[high_performer_id, new_agent_id],
                cost_impact=self.agent_economics[new_agent_id].spawn_cost,
                revenue_impact=demand_context["expected_revenue"],
                timestamp=datetime.now().isoformat()
            )
            self.scaling_events.append(event)
            
            print(f"[SCALING] Replicated high-performer {high_performer_id} -> {new_agent_id}")
            
        except Exception as e:
            print(f"[SCALING] Failed to replicate {high_performer_id}: {e}")
    
    async def _trigger_optimization_or_shutdown(self, poor_performer_id: str, reason: str):
        """Optimize or shutdown a poor-performing agent"""
        
        economics = self.agent_economics[poor_performer_id]
        
        # First, try optimization
        optimization_applied = await self._apply_performance_optimization(poor_performer_id)
        
        if not optimization_applied:
            # Shutdown agent and recover resources
            await self._shutdown_agent(poor_performer_id, reason)
    
    async def _apply_performance_optimization(self, agent_id: str) -> bool:
        """Apply performance optimizations to struggling agent"""
        
        economics = self.agent_economics[agent_id]
        archetype = self._get_agent_archetype(economics)
        
        optimizations_applied = []
        
        # 1. Reduce operational costs
        if economics.operational_cost_per_hour > 0.05:
            economics.operational_cost_per_hour *= 0.8
            optimizations_applied.append("cost_reduction")
        
        # 2. Task specialization - focus on higher-value tasks
        if archetype in [AgentArchetype.STANDARD_AGENT, AgentArchetype.PREMIUM_SPECIALIST]:
            # Would redirect to higher-value task types
            optimizations_applied.append("task_specialization")
        
        # 3. Resource optimization
        archetype_config = self.archetype_configs[archetype]
        if self._optimize_agent_resources(agent_id, archetype_config):
            optimizations_applied.append("resource_optimization")
        
        if optimizations_applied:
            event = ScalingEvent(
                event_id=f"optimize_{uuid.uuid4().hex[:8]}",
                event_type="optimize",
                trigger=f"performance_optimization_{agent_id}",
                agents_affected=[agent_id],
                cost_impact=-economics.operational_cost_per_hour * 0.2 * 24,  # Daily savings
                revenue_impact=0,  # Neutral for now
                timestamp=datetime.now().isoformat()
            )
            self.scaling_events.append(event)
            
            print(f"[OPTIMIZATION] Applied {len(optimizations_applied)} optimizations to {agent_id}")
            return True
        
        return False
    
    def _optimize_agent_resources(self, agent_id: str, config: Dict[str, Any]) -> bool:
        """Optimize resource allocation for specific agent"""
        
        requirements = config["resource_requirements"]
        
        # Free up unused resources
        freed_resources = {
            "cpu": int(requirements["cpu"] * 0.1),
            "memory": int(requirements["memory"] * 0.1),
            "api_calls": int(requirements["api_calls"] * 0.1)
        }
        
        for resource, amount in freed_resources.items():
            self.resource_pool[resource] += amount
        
        return True
    
    async def _shutdown_agent(self, agent_id: str, reason: str):
        """Shutdown underperforming agent and recover resources"""
        
        economics = self.agent_economics[agent_id]
        archetype = self._get_agent_archetype(economics)
        
        # Recover resources
        requirements = self.archetype_configs[archetype]["resource_requirements"]
        for resource, amount in requirements.items():
            self.resource_pool[resource] += amount
        
        # Record shutdown
        event = ScalingEvent(
            event_id=f"shutdown_{uuid.uuid4().hex[:8]}",
            event_type="shutdown", 
            trigger=f"shutdown_{reason}_{agent_id}",
            agents_affected=[agent_id],
            cost_impact=0,  # No additional cost
            revenue_impact=-economics.revenue_generated / economics.hours_active * 8,  # Lost daily revenue
            timestamp=datetime.now().isoformat()
        )
        self.scaling_events.append(event)
        
        # Remove from tracking (but keep for historical analysis)
        print(f"[SCALING] Shutdown agent {agent_id} - ROI: {economics.roi:.2f}, Reason: {reason}")
    
    def _detect_demand_spike(self) -> bool:
        """Detect if market demand indicates need for scaling"""
        
        # Simplified demand detection
        recent_events = [e for e in self.scaling_events[-10:] if e.event_type == "spawn"]
        
        if len(recent_events) >= 3:
            # High spawn rate indicates demand
            return True
            
        # Check resource utilization
        total_resources = 1000 + 10000 + 100000  # cpu + memory + api_calls base
        available_resources = sum(self.resource_pool.values())
        utilization = 1 - (available_resources / total_resources)
        
        return utilization > 0.8  # 80% resource utilization
    
    async def _trigger_demand_scaling(self, trigger: str):
        """Scale agents based on market demand"""
        
        # Determine optimal scaling strategy
        scaling_strategy = await self._calculate_optimal_scaling_strategy()
        
        for archetype, spawn_count in scaling_strategy.items():
            for _ in range(spawn_count):
                demand_context = {
                    "type": "demand_scaling",
                    "complexity": 2,
                    "urgency": "priority",
                    "budget": 5000,
                    "expected_revenue": 500
                }
                
                try:
                    await self.spawn_optimal_agent(demand_context)
                except Exception as e:
                    print(f"[SCALING] Demand scaling failed: {e}")
                    break
    
    async def _calculate_optimal_scaling_strategy(self) -> Dict[AgentArchetype, int]:
        """Calculate optimal scaling strategy based on current performance"""
        
        # Analyze current archetype performance
        archetype_performance = {}
        
        for economics in self.agent_economics.values():
            archetype = self._get_agent_archetype(economics)
            
            if archetype not in archetype_performance:
                archetype_performance[archetype] = {
                    "count": 0,
                    "total_roi": 0,
                    "total_revenue": 0
                }
            
            perf = archetype_performance[archetype]
            perf["count"] += 1
            perf["total_roi"] += economics.roi
            perf["total_revenue"] += economics.revenue_generated
        
        # Calculate average performance
        for archetype, perf in archetype_performance.items():
            if perf["count"] > 0:
                perf["avg_roi"] = perf["total_roi"] / perf["count"]
                perf["avg_revenue"] = perf["total_revenue"] / perf["count"]
        
        # Determine scaling strategy
        scaling_strategy = {}
        
        # Prioritize high-performing archetypes
        for archetype, perf in archetype_performance.items():
            if perf.get("avg_roi", 0) >= self.min_roi_threshold:
                # Scale successful archetypes
                scaling_strategy[archetype] = min(3, int(perf["avg_roi"] / 2))
        
        # Ensure at least some micro scouts for cost efficiency
        if AgentArchetype.MICRO_SCOUT not in scaling_strategy:
            scaling_strategy[AgentArchetype.MICRO_SCOUT] = 2
        
        return scaling_strategy
    
    async def _optimize_resource_allocation(self):
        """Optimize resource allocation across all agents"""
        
        print("[SCALING] Optimizing resource allocation...")
        
        # Scale up resources if needed
        current_utilization = self._calculate_resource_utilization()
        
        if current_utilization > 0.9:  # 90% utilization
            # Scale resources
            scale_factor = 1.5
            for resource in self.resource_pool:
                self.resource_pool[resource] = int(self.resource_pool[resource] * scale_factor)
            
            print(f"[SCALING] Scaled resources by {scale_factor}x due to high utilization")
        
        self.optimization_cycles += 1
    
    def _calculate_resource_utilization(self) -> float:
        """Calculate current resource utilization across all agents"""
        
        total_allocated = 0
        total_capacity = 0
        
        for resource, available in self.resource_pool.items():
            if resource == "cpu":
                base_capacity = 1000
            elif resource == "memory": 
                base_capacity = 10000
            else:  # api_calls
                base_capacity = 100000
            
            allocated = base_capacity - available
            total_allocated += allocated / base_capacity  # Normalize
            total_capacity += 1  # Each resource counts as 1 unit
        
        return total_allocated / total_capacity if total_capacity > 0 else 0
    
    def get_scaling_dashboard(self) -> Dict[str, Any]:
        """Get comprehensive scaling performance dashboard"""
        
        active_agents = len(self.agent_economics)
        
        if active_agents == 0:
            return {"status": "No agents deployed"}
        
        # Calculate aggregate metrics
        total_investment = sum(e.spawn_cost for e in self.agent_economics.values())
        total_revenue = sum(e.revenue_generated for e in self.agent_economics.values())
        total_operational_costs = sum(e.hours_active * e.operational_cost_per_hour 
                                    for e in self.agent_economics.values())
        
        net_profit = total_revenue - total_investment - total_operational_costs
        overall_roi = (total_revenue / (total_investment + total_operational_costs) 
                      if (total_investment + total_operational_costs) > 0 else 0)
        
        # Calculate archetype distribution
        archetype_distribution = {}
        for economics in self.agent_economics.values():
            archetype = self._get_agent_archetype(economics)
            archetype_distribution[archetype.value] = archetype_distribution.get(archetype.value, 0) + 1
        
        # Performance metrics
        rois = [e.roi for e in self.agent_economics.values() if e.roi > 0]
        avg_roi = sum(rois) / len(rois) if rois else 0
        
        payback_times = [e.payback_period_hours for e in self.agent_economics.values() 
                        if e.payback_period_hours < float('inf')]
        avg_payback = sum(payback_times) / len(payback_times) if payback_times else 0
        
        return {
            "scaling_overview": {
                "active_agents": active_agents,
                "total_investment": total_investment,
                "total_revenue": total_revenue,
                "net_profit": net_profit,
                "overall_roi": overall_roi,
                "optimization_cycles": self.optimization_cycles
            },
            "performance_metrics": {
                "average_roi": avg_roi,
                "average_payback_hours": avg_payback,
                "agents_above_roi_threshold": len([e for e in self.agent_economics.values() 
                                                 if e.roi >= self.min_roi_threshold]),
                "resource_utilization": self._calculate_resource_utilization()
            },
            "archetype_distribution": archetype_distribution,
            "resource_pool": self.resource_pool,
            "scaling_events": len(self.scaling_events),
            "recent_events": [asdict(e) for e in self.scaling_events[-5:]]
        }
    
    async def simulate_exponential_scaling(self, hours: int = 24) -> Dict[str, Any]:
        """Simulate exponential scaling over time period"""
        
        simulation_results = {
            "hourly_snapshots": [],
            "final_metrics": {}
        }
        
        print(f"[SIMULATION] Starting {hours}-hour exponential scaling simulation")
        
        # Initial spawn
        initial_demand = {
            "type": "initial_spawn",
            "complexity": 2,
            "urgency": "standard", 
            "budget": 1000,
            "expected_revenue": 100
        }
        
        await self.spawn_optimal_agent(initial_demand)
        
        for hour in range(hours):
            print(f"[SIMULATION] Hour {hour + 1}/{hours}")
            
            # Simulate agent activity and revenue generation
            for agent_id, economics in self.agent_economics.items():
                if economics.hours_active < 1000:  # Agent still active
                    
                    archetype = self._get_agent_archetype(economics)
                    config = self.archetype_configs[archetype]
                    
                    # Simulate revenue for this hour
                    hourly_revenue = config["expected_revenue_per_hour"] * (0.8 + 0.4 * math.random())  # Some randomness
                    
                    await self.update_agent_performance(agent_id, hourly_revenue, 1.0, 1)
            
            # Check for scaling opportunities every 4 hours
            if (hour + 1) % 4 == 0:
                await self._trigger_demand_scaling("simulation_growth")
            
            # Snapshot metrics
            dashboard = self.get_scaling_dashboard()
            dashboard["simulation_hour"] = hour + 1
            simulation_results["hourly_snapshots"].append(dashboard)
        
        simulation_results["final_metrics"] = self.get_scaling_dashboard()
        
        return simulation_results

async def main():
    """Demo infinite scaling engine"""
    print("SINCOR Infinite Agent Scaling Engine Demo")
    print("=" * 47)
    
    # Create scaling engine
    engine = InfiniteScalingEngine()
    
    # Simulate initial demand
    demand_contexts = [
        {
            "type": "market_analysis",
            "complexity": 2,
            "urgency": "priority",
            "budget": 5000,
            "expected_revenue": 2000
        },
        {
            "type": "competitor_intelligence", 
            "complexity": 3,
            "urgency": "standard",
            "budget": 3000,
            "expected_revenue": 1500
        }
    ]
    
    # Spawn initial agents
    for context in demand_contexts:
        agent_id = await engine.spawn_optimal_agent(context)
        
        # Simulate some performance
        await engine.update_agent_performance(agent_id, 500, 4.0, 3)
    
    # Show dashboard
    dashboard = engine.get_scaling_dashboard()
    print("\nScaling Dashboard:")
    for section, data in dashboard.items():
        print(f"\n{section.replace('_', ' ').title()}:")
        if isinstance(data, dict):
            for key, value in data.items():
                print(f"  {key}: {value}")
        else:
            print(f"  {data}")

if __name__ == "__main__":
    asyncio.run(main())