#!/usr/bin/env python3
"""
SINCOR Swarm Intelligence Lifecycle Manager
Advanced agent lifecycle management with collective decision making
"""

import asyncio
import time
import json
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum
import sqlite3
from datetime import datetime, timedelta
import threading
import uuid

class PermissionLevel(Enum):
    GOD_MODE = "god_mode"
    PAID_USER = "paid_user"
    FREE_USER = "free_user"

class SwarmDecisionType(Enum):
    SCALE_UP = "scale_up"
    SCALE_DOWN = "scale_down"
    TASK_REDISTRIBUTION = "task_redistribution"
    RESOURCE_ALLOCATION = "resource_allocation"
    EMERGENCY_RESPONSE = "emergency_response"

class VoteType(Enum):
    STRONG_AGREE = 2
    AGREE = 1
    NEUTRAL = 0
    DISAGREE = -1
    STRONG_DISAGREE = -2

@dataclass
class SwarmVote:
    agent_id: str
    decision_id: str
    vote_type: VoteType
    confidence: float  # 0.0 to 1.0
    reasoning: str
    performance_weight: float  # Based on agent's historical performance
    timestamp: float

@dataclass
class SwarmDecision:
    decision_id: str
    decision_type: SwarmDecisionType
    proposal: Dict[str, Any]
    votes: List[SwarmVote]
    consensus_reached: bool
    decision_outcome: Optional[Dict[str, Any]]
    execution_status: str
    created_at: float
    executed_at: Optional[float]

@dataclass
class UserPermissions:
    user_id: str
    permission_level: PermissionLevel
    max_agents: int
    max_scaling_rate: int  # agents per hour
    resource_quota: float
    can_override_swarm: bool
    can_emergency_spawn: bool
    priority_weight: float

@dataclass
class GoalTracker:
    goal_id: str
    description: str
    target_value: float
    current_progress: float
    deadline: datetime
    assigned_agents: List[str]
    priority: int  # 1-10
    user_id: str
    auto_scaling_enabled: bool

class SwarmIntelligenceManager:
    """Core swarm intelligence and lifecycle management system"""
    
    def __init__(self, db_path: str = "swarm_intelligence.db"):
        self.db_path = db_path
        self.active_decisions: Dict[str, SwarmDecision] = {}
        self.user_permissions: Dict[str, UserPermissions] = {}
        self.goal_trackers: Dict[str, GoalTracker] = {}
        self.agent_performance_weights: Dict[str, float] = {}
        self.running = False
        
        self._setup_database()
        self._initialize_permissions()
        
    def _setup_database(self):
        """Initialize SQLite database for swarm intelligence"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Swarm decisions table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS swarm_decisions (
            decision_id TEXT PRIMARY KEY,
            decision_type TEXT,
            proposal TEXT,
            votes TEXT,
            consensus_reached BOOLEAN,
            decision_outcome TEXT,
            execution_status TEXT,
            created_at REAL,
            executed_at REAL
        )
        ''')
        
        # User permissions table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_permissions (
            user_id TEXT PRIMARY KEY,
            permission_level TEXT,
            max_agents INTEGER,
            max_scaling_rate INTEGER,
            resource_quota REAL,
            can_override_swarm BOOLEAN,
            can_emergency_spawn BOOLEAN,
            priority_weight REAL
        )
        ''')
        
        # Goal tracking table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS goal_trackers (
            goal_id TEXT PRIMARY KEY,
            description TEXT,
            target_value REAL,
            current_progress REAL,
            deadline TEXT,
            assigned_agents TEXT,
            priority INTEGER,
            user_id TEXT,
            auto_scaling_enabled BOOLEAN
        )
        ''')
        
        # Agent voting history
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS voting_history (
            vote_id TEXT PRIMARY KEY,
            agent_id TEXT,
            decision_id TEXT,
            vote_type INTEGER,
            confidence REAL,
            reasoning TEXT,
            performance_weight REAL,
            timestamp REAL
        )
        ''')
        
        conn.commit()
        conn.close()
        
    def _initialize_permissions(self):
        """Initialize default user permissions"""
        # God mode (system owner)
        god_permissions = UserPermissions(
            user_id="GOD_USER",
            permission_level=PermissionLevel.GOD_MODE,
            max_agents=999999,
            max_scaling_rate=999999,
            resource_quota=999999.0,
            can_override_swarm=True,
            can_emergency_spawn=True,
            priority_weight=10.0
        )
        
        # Standard paid user template
        paid_user_template = UserPermissions(
            user_id="PAID_USER_TEMPLATE",
            permission_level=PermissionLevel.PAID_USER,
            max_agents=25,
            max_scaling_rate=5,
            resource_quota=100.0,
            can_override_swarm=False,
            can_emergency_spawn=False,
            priority_weight=1.0
        )
        
        self.user_permissions["GOD_USER"] = god_permissions
        self.user_permissions["PAID_USER_TEMPLATE"] = paid_user_template
        
    async def create_swarm_decision(self, decision_type: SwarmDecisionType, 
                                  proposal: Dict[str, Any], 
                                  user_id: str = "SYSTEM") -> str:
        """Create a new decision for swarm voting"""
        
        decision_id = f"decision_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        
        decision = SwarmDecision(
            decision_id=decision_id,
            decision_type=decision_type,
            proposal=proposal,
            votes=[],
            consensus_reached=False,
            decision_outcome=None,
            execution_status="PENDING_VOTES",
            created_at=time.time(),
            executed_at=None
        )
        
        self.active_decisions[decision_id] = decision
        
        # God mode auto-approval for system owner
        if user_id == "GOD_USER":
            await self._execute_god_mode_decision(decision)
            return decision_id
            
        print(f">> New swarm decision created: {decision_type.value}")
        print(f"   Decision ID: {decision_id}")
        print(f"   Proposal: {json.dumps(proposal, indent=2)}")
        
        return decision_id
    
    async def submit_vote(self, agent_id: str, decision_id: str, 
                         vote_type: VoteType, confidence: float, 
                         reasoning: str) -> bool:
        """Submit a vote from an agent"""
        
        if decision_id not in self.active_decisions:
            return False
            
        # Get agent performance weight
        performance_weight = self.agent_performance_weights.get(agent_id, 0.5)
        
        vote = SwarmVote(
            agent_id=agent_id,
            decision_id=decision_id,
            vote_type=vote_type,
            confidence=confidence,
            reasoning=reasoning,
            performance_weight=performance_weight,
            timestamp=time.time()
        )
        
        self.active_decisions[decision_id].votes.append(vote)
        
        # Store vote in database
        await self._store_vote(vote)
        
        # Check if consensus reached
        if await self._check_consensus(decision_id):
            await self._execute_swarm_decision(decision_id)
            
        return True
    
    async def _check_consensus(self, decision_id: str) -> bool:
        """Check if swarm has reached consensus on a decision"""
        
        decision = self.active_decisions[decision_id]
        
        if len(decision.votes) < 5:  # Minimum 5 votes required
            return False
            
        # Calculate weighted consensus
        total_weight = 0
        weighted_score = 0
        
        for vote in decision.votes:
            weight = vote.performance_weight * vote.confidence
            total_weight += weight
            weighted_score += vote.vote_type.value * weight
            
        if total_weight == 0:
            return False
            
        consensus_score = weighted_score / total_weight
        
        # Consensus reached if score > 0.6 (positive) or < -0.6 (negative)
        if abs(consensus_score) > 0.6:
            decision.consensus_reached = True
            decision.decision_outcome = {
                "consensus_score": consensus_score,
                "action": "APPROVE" if consensus_score > 0 else "REJECT",
                "participating_agents": len(decision.votes),
                "confidence_level": abs(consensus_score)
            }
            return True
            
        return False
    
    async def _execute_swarm_decision(self, decision_id: str):
        """Execute a swarm decision after consensus"""
        
        decision = self.active_decisions[decision_id]
        outcome = decision.decision_outcome
        
        if outcome["action"] == "APPROVE":
            print(f">> SWARM CONSENSUS: Executing {decision.decision_type.value}")
            print(f"   Consensus Score: {outcome['consensus_score']:.2f}")
            print(f"   Participating Agents: {outcome['participating_agents']}")
            
            # Execute the actual decision
            await self._execute_decision_action(decision)
            
        else:
            print(f">> SWARM CONSENSUS: Rejecting {decision.decision_type.value}")
            decision.execution_status = "REJECTED_BY_SWARM"
            
        decision.executed_at = time.time()
        
    async def _execute_god_mode_decision(self, decision: SwarmDecision):
        """Execute God mode decision immediately"""
        
        print(f">> GOD MODE: Executing {decision.decision_type.value} immediately")
        decision.consensus_reached = True
        decision.decision_outcome = {
            "consensus_score": 10.0,
            "action": "APPROVE",
            "participating_agents": 0,
            "confidence_level": 1.0,
            "god_mode_override": True
        }
        decision.execution_status = "GOD_MODE_APPROVED"
        
        await self._execute_decision_action(decision)
        decision.executed_at = time.time()
    
    async def _execute_decision_action(self, decision: SwarmDecision):
        """Execute the actual decision action"""
        
        proposal = decision.proposal
        decision_type = decision.decision_type
        
        if decision_type == SwarmDecisionType.SCALE_UP:
            await self._scale_up_agents(proposal)
        elif decision_type == SwarmDecisionType.SCALE_DOWN:
            await self._scale_down_agents(proposal)
        elif decision_type == SwarmDecisionType.TASK_REDISTRIBUTION:
            await self._redistribute_tasks(proposal)
        elif decision_type == SwarmDecisionType.RESOURCE_ALLOCATION:
            await self._allocate_resources(proposal)
        elif decision_type == SwarmDecisionType.EMERGENCY_RESPONSE:
            await self._emergency_response(proposal)
            
        decision.execution_status = "EXECUTED"
    
    async def _scale_up_agents(self, proposal: Dict[str, Any]):
        """Scale up agents based on swarm decision"""
        agent_count = proposal.get("agent_count", 1)
        task_type = proposal.get("task_type", "general")
        
        print(f"   Spawning {agent_count} agents for {task_type} tasks")
        
        # Integration point with existing agent spawning system
        # This would call your existing agent creation methods
        
    async def _scale_down_agents(self, proposal: Dict[str, Any]):
        """Scale down agents based on swarm decision"""
        agent_ids = proposal.get("agent_ids", [])
        
        print(f"   Terminating {len(agent_ids)} underperforming agents")
        
        # Integration point with existing agent termination system
        
    async def _redistribute_tasks(self, proposal: Dict[str, Any]):
        """Redistribute tasks between agents"""
        print("   Redistributing tasks based on swarm optimization")
        
        # Integration point with task management system
        
    async def _allocate_resources(self, proposal: Dict[str, Any]):
        """Allocate computational resources"""
        print("   Reallocating computational resources")
        
        # Integration point with resource management
        
    async def _emergency_response(self, proposal: Dict[str, Any]):
        """Execute emergency response protocol"""
        print("   Executing emergency response protocol")
        
        # Integration point with emergency systems
    
    async def create_goal_tracker(self, description: str, target_value: float, 
                                deadline: datetime, user_id: str, 
                                priority: int = 5) -> str:
        """Create a new goal tracker (e.g., '50 leads by Friday')"""
        
        goal_id = f"goal_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        
        goal = GoalTracker(
            goal_id=goal_id,
            description=description,
            target_value=target_value,
            current_progress=0.0,
            deadline=deadline,
            assigned_agents=[],
            priority=priority,
            user_id=user_id,
            auto_scaling_enabled=True
        )
        
        self.goal_trackers[goal_id] = goal
        
        print(f">> New goal created: {description}")
        print(f"   Target: {target_value} by {deadline}")
        print(f"   Priority: {priority}/10")
        
        return goal_id
    
    async def update_goal_progress(self, goal_id: str, current_progress: float):
        """Update progress on a goal and trigger scaling if needed"""
        
        if goal_id not in self.goal_trackers:
            return False
            
        goal = self.goal_trackers[goal_id]
        goal.current_progress = current_progress
        
        # Calculate progress velocity and time remaining
        time_remaining = (goal.deadline - datetime.now()).total_seconds()
        progress_percentage = current_progress / goal.target_value
        
        if time_remaining > 0 and goal.auto_scaling_enabled:
            required_velocity = (goal.target_value - current_progress) / (time_remaining / 3600)  # per hour
            current_velocity = self._estimate_current_velocity(goal_id)
            
            if current_velocity < required_velocity * 0.8:  # 20% buffer
                # Need more agents
                additional_agents = max(1, int((required_velocity - current_velocity) / 10))  # Rough estimate
                
                scaling_proposal = {
                    "goal_id": goal_id,
                    "agent_count": additional_agents,
                    "task_type": "goal_oriented",
                    "reasoning": f"Goal '{goal.description}' falling behind. Need {additional_agents} more agents.",
                    "urgency": "high" if time_remaining < 86400 else "medium"  # 24 hours
                }
                
                await self.create_swarm_decision(
                    SwarmDecisionType.SCALE_UP,
                    scaling_proposal,
                    goal.user_id
                )
        
        return True
    
    def _estimate_current_velocity(self, goal_id: str) -> float:
        """Estimate current progress velocity for a goal"""
        # This would analyze recent progress data
        # For now, return a placeholder
        return 5.0  # units per hour
    
    async def _store_vote(self, vote: SwarmVote):
        """Store vote in database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT INTO voting_history 
        (vote_id, agent_id, decision_id, vote_type, confidence, reasoning, performance_weight, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            uuid.uuid4().hex,
            vote.agent_id,
            vote.decision_id,
            vote.vote_type.value,
            vote.confidence,
            vote.reasoning,
            vote.performance_weight,
            vote.timestamp
        ))
        
        conn.commit()
        conn.close()
    
    def get_swarm_intelligence_summary(self) -> Dict[str, Any]:
        """Get current swarm intelligence status"""
        
        active_decisions = len(self.active_decisions)
        active_goals = len(self.goal_trackers)
        
        # Calculate swarm consensus health
        recent_decisions = [d for d in self.active_decisions.values() 
                          if d.created_at > time.time() - 3600]  # Last hour
        
        consensus_rate = len([d for d in recent_decisions if d.consensus_reached]) / max(1, len(recent_decisions))
        
        return {
            "active_decisions": active_decisions,
            "active_goals": active_goals,
            "consensus_rate": consensus_rate,
            "swarm_intelligence_health": "OPTIMAL" if consensus_rate > 0.8 else "DEGRADED",
            "god_mode_active": "GOD_USER" in self.user_permissions,
            "total_agents_in_swarm": len(self.agent_performance_weights)
        }

# Global swarm intelligence instance
swarm_manager = SwarmIntelligenceManager()

async def god_mode_scaling_request(agent_count: int, task_type: str = "general", 
                                 reasoning: str = "God mode request") -> str:
    """God mode instant scaling - no swarm voting required"""
    
    proposal = {
        "agent_count": agent_count,
        "task_type": task_type,
        "reasoning": reasoning,
        "god_mode": True,
        "instant_execution": True
    }
    
    return await swarm_manager.create_swarm_decision(
        SwarmDecisionType.SCALE_UP,
        proposal,
        "GOD_USER"
    )

async def create_lead_generation_goal(target_leads: int, deadline_days: int) -> str:
    """Create a lead generation goal with auto-scaling"""
    
    deadline = datetime.now() + timedelta(days=deadline_days)
    
    return await swarm_manager.create_goal_tracker(
        description=f"{target_leads} leads by {deadline.strftime('%A')}",
        target_value=float(target_leads),
        deadline=deadline,
        user_id="GOD_USER",
        priority=8
    )

if __name__ == "__main__":
    print(">> SINCOR Swarm Intelligence Lifecycle Manager")
    print("   God Mode: ENABLED")
    print("   Swarm Democracy: ACTIVE")
    print("   Goal-Driven Scaling: READY")