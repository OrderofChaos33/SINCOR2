#!/usr/bin/env python3
"""
SINCOR Swarm Coordination System

Implements lightweight market/contract-net for autonomous work assignment:
- TaskMarket: Broadcast tasks with bounty + skill requirements
- Contract-net Protocol: Agents bid with intent + plan + cost estimates
- Credit Assignment: Merit points for SBT promotions
- Conflict Resolution: Auditor entities enforce standards
- No central micromanaging - pure distributed coordination
"""

import json
import os
import uuid
from typing import Dict, List, Optional, Any, Set
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from enum import Enum
import heapq
from collections import defaultdict

class TaskStatus(Enum):
    """Task lifecycle status"""
    BROADCAST = "broadcast"         # Available for bidding
    BIDDING = "bidding"            # Accepting bids
    AWARDED = "awarded"            # Assigned to agent
    IN_PROGRESS = "in_progress"    # Being executed
    COMPLETED = "completed"        # Successfully finished
    FAILED = "failed"              # Failed execution
    CANCELLED = "cancelled"        # Cancelled before completion

class BidStatus(Enum):
    """Bid status"""
    SUBMITTED = "submitted"        # Bid placed
    SHORTLISTED = "shortlisted"    # Under consideration
    ACCEPTED = "accepted"          # Winning bid
    REJECTED = "rejected"          # Not selected
    WITHDRAWN = "withdrawn"        # Agent withdrew bid

@dataclass
class TaskContract:
    """Task available for bidding in the market"""
    task_id: str
    goal: str                      # What needs to be accomplished
    description: str               # Detailed requirements
    skills_required: List[str]     # Required competencies
    priority: float                # 0.0-1.0 urgency
    reward: int                    # Merit points offered
    deadline: str                  # ISO timestamp
    budget_tokens: int             # Token budget allocated
    budget_tool_calls: int         # Tool call budget
    created_by: str                # Requesting agent/system
    created_at: str                # Creation timestamp
    status: TaskStatus             # Current status
    success_criteria: List[str]    # Acceptance criteria
    context: Dict[str, Any]        # Additional context
    
@dataclass
class AgentBid:
    """Agent's bid for a task contract"""
    bid_id: str
    task_id: str
    agent_id: str
    archetype: str                 # Agent's primary archetype
    confidence: float              # 0.0-1.0 confidence in success
    estimated_cost_tokens: int     # Expected token consumption
    estimated_cost_calls: int      # Expected tool calls
    estimated_duration: int        # Expected minutes to complete
    plan: List[str]                # High-level execution plan
    unique_value: str              # What makes this bid special
    agent_track_record: Dict[str, float]  # Success rates by skill
    submitted_at: str              # Submission timestamp
    status: BidStatus              # Current bid status

@dataclass
class TaskAssignment:
    """Active task assignment to agent"""
    assignment_id: str
    task_id: str
    agent_id: str
    assigned_at: str
    bid_accepted: AgentBid
    progress_milestones: List[Dict[str, Any]]
    status_updates: List[Dict[str, Any]]
    completion_timestamp: Optional[str] = None
    final_results: Optional[Dict[str, Any]] = None

@dataclass
class MarketTransaction:
    """Record of completed market transaction"""
    transaction_id: str
    task_id: str
    winner_agent_id: str
    reward_distributed: int
    completion_quality: float     # 0.0-1.0 quality assessment
    completion_time: int          # Actual minutes taken
    merit_points_earned: int      # Final merit awarded
    penalties_applied: int        # Penalties for quality/deadline issues
    recorded_at: str

class TaskMarket:
    """Distributed task market with contract-net protocol"""
    
    def __init__(self, market_dir: str = "market"):
        self.market_dir = market_dir
        
        os.makedirs(market_dir, exist_ok=True)
        
        # Market state files
        self.active_tasks_file = f"{market_dir}/active_tasks.json"
        self.bids_file = f"{market_dir}/bids.jsonl" 
        self.assignments_file = f"{market_dir}/assignments.json"
        self.transactions_file = f"{market_dir}/transactions.jsonl"
        self.reputation_file = f"{market_dir}/agent_reputation.json"
        
        # Load current state
        self.active_tasks = self._load_active_tasks()
        self.active_assignments = self._load_assignments()
        self.agent_reputation = self._load_reputation()
        
        # Market metrics
        self.market_stats = {
            "total_tasks_posted": 0,
            "total_bids_received": 0,
            "successful_completions": 0,
            "average_bid_confidence": 0.0,
            "market_efficiency": 0.0  # Time from post to assignment
        }
    
    def _load_active_tasks(self) -> Dict[str, TaskContract]:
        """Load currently active tasks"""
        
        if not os.path.exists(self.active_tasks_file):
            return {}
            
        with open(self.active_tasks_file, 'r') as f:
            data = json.load(f)
            
        tasks = {}
        for task_id, task_data in data.items():
            # Convert status string to enum
            task_data['status'] = TaskStatus(task_data['status'])
            tasks[task_id] = TaskContract(**task_data)
            
        return tasks
    
    def _save_active_tasks(self):
        """Save active tasks to storage"""
        
        data = {}
        for task_id, task in self.active_tasks.items():
            task_dict = asdict(task)
            task_dict['status'] = task.status.value
            data[task_id] = task_dict
            
        with open(self.active_tasks_file, 'w') as f:
            json.dump(data, f, indent=2)
    
    def _load_assignments(self) -> Dict[str, TaskAssignment]:
        """Load active task assignments"""
        
        if not os.path.exists(self.assignments_file):
            return {}
            
        with open(self.assignments_file, 'r') as f:
            data = json.load(f)
            
        assignments = {}
        for assignment_id, assignment_data in data.items():
            # Reconstruct bid object
            bid_data = assignment_data['bid_accepted']
            bid_data['status'] = BidStatus(bid_data['status'])
            assignment_data['bid_accepted'] = AgentBid(**bid_data)
            
            assignments[assignment_id] = TaskAssignment(**assignment_data)
            
        return assignments
    
    def _save_assignments(self):
        """Save assignments to storage"""
        
        data = {}
        for assignment_id, assignment in self.active_assignments.items():
            assignment_dict = asdict(assignment)
            assignment_dict['bid_accepted']['status'] = assignment.bid_accepted.status.value
            data[assignment_id] = assignment_dict
            
        with open(self.assignments_file, 'w') as f:
            json.dump(data, f, indent=2)
    
    def _load_reputation(self) -> Dict[str, Dict[str, Any]]:
        """Load agent reputation scores"""
        
        if not os.path.exists(self.reputation_file):
            return {}
            
        with open(self.reputation_file, 'r') as f:
            return json.load(f)
    
    def _save_reputation(self):
        """Save reputation scores"""
        
        with open(self.reputation_file, 'w') as f:
            json.dump(self.agent_reputation, f, indent=2)
    
    # TASK MARKET OPERATIONS
    
    def post_task(self, task: TaskContract) -> str:
        """Post a new task to the market"""
        
        # Validate task requirements
        if not task.goal or not task.skills_required:
            raise ValueError("Task must have goal and required skills")
            
        # Set initial status
        task.status = TaskStatus.BROADCAST
        task.created_at = datetime.now().isoformat()
        
        if not task.task_id:
            task.task_id = f"T-{uuid.uuid4().hex[:8]}"
        
        # Add to active tasks
        self.active_tasks[task.task_id] = task
        self._save_active_tasks()
        
        # Update market stats
        self.market_stats["total_tasks_posted"] += 1
        
        print(f"[TASK] {task.task_id} posted to market: {task.goal}")
        print(f"   Skills required: {task.skills_required}")
        print(f"   Reward: {task.reward} merit points")
        print(f"   Deadline: {task.deadline}")
        
        return task.task_id
    
    def submit_bid(self, bid: AgentBid) -> bool:
        """Submit a bid for a task"""
        
        # Validate bid
        if bid.task_id not in self.active_tasks:
            return False
            
        task = self.active_tasks[bid.task_id]
        
        if task.status != TaskStatus.BROADCAST:
            return False
        
        # Check agent has required skills
        agent_rep = self.agent_reputation.get(bid.agent_id, {})
        agent_skills = agent_rep.get("competencies", [])
        
        missing_skills = set(task.skills_required) - set(agent_skills)
        if missing_skills:
            print(f"[ERROR] Agent {bid.agent_id} missing skills: {missing_skills}")
            return False
        
        # Generate bid ID if not provided
        if not bid.bid_id:
            bid.bid_id = f"B-{uuid.uuid4().hex[:8]}"
            
        bid.submitted_at = datetime.now().isoformat()
        bid.status = BidStatus.SUBMITTED
        
        # Log bid
        with open(self.bids_file, 'a') as f:
            bid_dict = asdict(bid)
            bid_dict['status'] = bid.status.value
            f.write(json.dumps(bid_dict) + '\n')
        
        # Update market stats
        self.market_stats["total_bids_received"] += 1
        avg_conf = self.market_stats["average_bid_confidence"]
        total_bids = self.market_stats["total_bids_received"]
        self.market_stats["average_bid_confidence"] = (avg_conf * (total_bids - 1) + bid.confidence) / total_bids
        
        print(f"[BID] {bid.bid_id} submitted by {bid.agent_id}")
        print(f"   Confidence: {bid.confidence:.2f}")
        print(f"   Estimated cost: {bid.estimated_cost_tokens} tokens, {bid.estimated_cost_calls} calls")
        print(f"   Plan: {' -> '.join(bid.plan)}")
        
        return True
    
    def get_task_bids(self, task_id: str) -> List[AgentBid]:
        """Get all bids for a specific task"""
        
        if not os.path.exists(self.bids_file):
            return []
            
        bids = []
        with open(self.bids_file, 'r') as f:
            for line in f:
                if line.strip():
                    data = json.loads(line)
                    if data.get('task_id') == task_id:
                        data['status'] = BidStatus(data['status'])
                        bids.append(AgentBid(**data))
        
        return bids
    
    def evaluate_and_award_task(self, task_id: str) -> Optional[str]:
        """Evaluate bids and award task to best agent"""
        
        if task_id not in self.active_tasks:
            return None
            
        task = self.active_tasks[task_id]
        bids = self.get_task_bids(task_id)
        
        if not bids:
            print(f"[WARN] No bids received for task {task_id}")
            return None
        
        # Score bids using multi-criteria evaluation
        scored_bids = []
        
        for bid in bids:
            if bid.status != BidStatus.SUBMITTED:
                continue
                
            score = self._score_bid(bid, task)
            scored_bids.append((score, bid))
        
        if not scored_bids:
            return None
            
        # Award to highest scoring bid
        scored_bids.sort(reverse=True)
        winning_score, winning_bid = scored_bids[0]
        
        # Create assignment
        assignment_id = f"A-{uuid.uuid4().hex[:8]}"
        assignment = TaskAssignment(
            assignment_id=assignment_id,
            task_id=task_id,
            agent_id=winning_bid.agent_id,
            assigned_at=datetime.now().isoformat(),
            bid_accepted=winning_bid,
            progress_milestones=[],
            status_updates=[]
        )
        
        # Update task and bid status
        task.status = TaskStatus.AWARDED
        winning_bid.status = BidStatus.ACCEPTED
        
        # Reject other bids
        for _, bid in scored_bids[1:]:
            bid.status = BidStatus.REJECTED
        
        # Store assignment
        self.active_assignments[assignment_id] = assignment
        self._save_assignments()
        self._save_active_tasks()
        
        # Update market efficiency metric
        time_to_award = (datetime.now() - datetime.fromisoformat(task.created_at)).total_seconds() / 60
        current_efficiency = self.market_stats.get("market_efficiency", 0)
        self.market_stats["market_efficiency"] = (current_efficiency + time_to_award) / 2
        
        print(f"[AWARD] Task {task_id} awarded to {winning_bid.agent_id}")
        print(f"   Winning score: {winning_score:.3f}")
        print(f"   Time to award: {time_to_award:.1f} minutes")
        
        return assignment_id
    
    def _score_bid(self, bid: AgentBid, task: TaskContract) -> float:
        """Score a bid using multiple criteria"""
        
        score_components = {}
        
        # Confidence weight (40%)
        score_components["confidence"] = bid.confidence * 0.40
        
        # Agent reputation weight (25%)
        agent_rep = self.agent_reputation.get(bid.agent_id, {})
        success_rate = agent_rep.get("success_rate", 0.5)
        score_components["reputation"] = success_rate * 0.25
        
        # Cost efficiency weight (20%)
        if task.budget_tokens > 0 and task.budget_tool_calls > 0:
            token_efficiency = 1.0 - (bid.estimated_cost_tokens / task.budget_tokens)
            call_efficiency = 1.0 - (bid.estimated_cost_calls / task.budget_tool_calls)
            score_components["efficiency"] = max(0, (token_efficiency + call_efficiency) / 2) * 0.20
        else:
            score_components["efficiency"] = 0.10  # Default moderate score
        
        # Plan quality weight (10%)
        plan_quality = min(1.0, len(bid.plan) / 5.0)  # More detailed plans score higher
        score_components["plan_quality"] = plan_quality * 0.10
        
        # Archetype match weight (5%)
        archetype_bonus = self._get_archetype_task_match(bid.archetype, task.skills_required)
        score_components["archetype_match"] = archetype_bonus * 0.05
        
        total_score = sum(score_components.values())
        
        print(f"   Bid {bid.bid_id} score: {total_score:.3f} " + 
              f"(conf:{score_components['confidence']:.2f}, " +
              f"rep:{score_components['reputation']:.2f}, " +
              f"eff:{score_components['efficiency']:.2f})")
        
        return total_score
    
    def _get_archetype_task_match(self, archetype: str, required_skills: List[str]) -> float:
        """Get bonus score for archetype-skill alignment"""
        
        archetype_skill_affinity = {
            "Scout": ["prospect", "scrape", "monitor", "research", "validate"],
            "Synthesizer": ["summarize", "dedup", "deconflict", "analyze", "curate"],
            "Builder": ["develop", "automate", "deploy", "test", "debug"],
            "Negotiator": ["outreach", "negotiate", "persuade", "present", "close"],
            "Caretaker": ["clean", "label", "backup", "maintain", "organize"],
            "Auditor": ["evaluate", "verify", "investigate", "report", "certify"],
            "Director": ["prioritize", "coordinate", "decide", "allocate", "plan"]
        }
        
        archetype_skills = set(archetype_skill_affinity.get(archetype, []))
        required_skills_set = set(required_skills)
        
        overlap = len(archetype_skills.intersection(required_skills_set))
        total_required = len(required_skills_set)
        
        return overlap / max(1, total_required)
    
    # TASK EXECUTION MANAGEMENT
    
    def start_task_execution(self, assignment_id: str) -> bool:
        """Mark task as in progress"""
        
        if assignment_id not in self.active_assignments:
            return False
            
        assignment = self.active_assignments[assignment_id]
        task = self.active_tasks[assignment.task_id]
        
        task.status = TaskStatus.IN_PROGRESS
        
        # Add progress milestone
        milestone = {
            "timestamp": datetime.now().isoformat(),
            "milestone": "execution_started",
            "details": f"Agent {assignment.agent_id} started execution"
        }
        assignment.progress_milestones.append(milestone)
        
        self._save_active_tasks()
        self._save_assignments()
        
        print(f"[START] Task execution started: {assignment.task_id} by {assignment.agent_id}")
        return True
    
    def update_task_progress(self, assignment_id: str, progress_update: Dict[str, Any]) -> bool:
        """Update task execution progress"""
        
        if assignment_id not in self.active_assignments:
            return False
            
        assignment = self.active_assignments[assignment_id]
        
        # Add status update
        update = {
            "timestamp": datetime.now().isoformat(),
            **progress_update
        }
        assignment.status_updates.append(update)
        
        self._save_assignments()
        
        print(f"[PROGRESS] Progress update for {assignment.task_id}: {progress_update.get('status', 'update')}")
        return True
    
    def complete_task(self, assignment_id: str, completion_results: Dict[str, Any]) -> bool:
        """Mark task as completed and distribute rewards"""
        
        if assignment_id not in self.active_assignments:
            return False
            
        assignment = self.active_assignments[assignment_id]
        task = self.active_tasks[assignment.task_id]
        
        # Mark as completed
        task.status = TaskStatus.COMPLETED
        assignment.completion_timestamp = datetime.now().isoformat()
        assignment.final_results = completion_results
        
        # Calculate actual performance vs bid estimates
        actual_duration = (datetime.now() - datetime.fromisoformat(assignment.assigned_at)).total_seconds() / 60
        estimated_duration = assignment.bid_accepted.estimated_duration
        
        # Assess completion quality (simplified - in real system, use evaluator agents)
        completion_quality = completion_results.get("quality_score", 0.8)
        
        # Calculate merit points based on quality and timeliness
        base_reward = task.reward
        quality_multiplier = completion_quality
        
        # Timeliness bonus/penalty
        if actual_duration <= estimated_duration:
            timeliness_multiplier = 1.1  # 10% bonus for early/on-time
        else:
            timeliness_multiplier = max(0.7, 1.0 - (actual_duration - estimated_duration) / estimated_duration)
        
        final_merit = int(base_reward * quality_multiplier * timeliness_multiplier)
        
        # Record transaction
        transaction = MarketTransaction(
            transaction_id=f"TX-{uuid.uuid4().hex[:8]}",
            task_id=task.task_id,
            winner_agent_id=assignment.agent_id,
            reward_distributed=final_merit,
            completion_quality=completion_quality,
            completion_time=int(actual_duration),
            merit_points_earned=final_merit,
            penalties_applied=max(0, base_reward - final_merit),
            recorded_at=datetime.now().isoformat()
        )
        
        # Log transaction
        with open(self.transactions_file, 'a') as f:
            f.write(json.dumps(asdict(transaction)) + '\n')
        
        # Update agent reputation
        self._update_agent_reputation(assignment.agent_id, task, completion_quality, actual_duration)
        
        # Update market stats
        self.market_stats["successful_completions"] += 1
        
        # Clean up completed assignment
        self._save_active_tasks()
        self._save_assignments()
        
        print(f"[COMPLETE] Task {task.task_id} completed by {assignment.agent_id}")
        print(f"   Quality: {completion_quality:.2f}")
        print(f"   Duration: {actual_duration:.1f}min (est: {estimated_duration}min)")
        print(f"   Merit earned: {final_merit} points")
        
        return True
    
    def _update_agent_reputation(self, agent_id: str, task: TaskContract, 
                               quality: float, duration: float):
        """Update agent reputation based on performance"""
        
        if agent_id not in self.agent_reputation:
            self.agent_reputation[agent_id] = {
                "tasks_completed": 0,
                "success_rate": 1.0,
                "average_quality": 0.0,
                "competencies": [],
                "total_merit_earned": 0,
                "specializations": {}
            }
        
        rep = self.agent_reputation[agent_id]
        
        # Update basic stats
        rep["tasks_completed"] += 1
        
        # Update success rate (consider quality > 0.6 as success)
        current_successes = rep["success_rate"] * (rep["tasks_completed"] - 1)
        is_success = quality > 0.6
        new_success_rate = (current_successes + (1 if is_success else 0)) / rep["tasks_completed"]
        rep["success_rate"] = new_success_rate
        
        # Update average quality
        current_avg_quality = rep["average_quality"]
        rep["average_quality"] = (current_avg_quality * (rep["tasks_completed"] - 1) + quality) / rep["tasks_completed"]
        
        # Update competencies
        for skill in task.skills_required:
            if skill not in rep["competencies"]:
                rep["competencies"].append(skill)
                
        # Update specializations (track performance by skill)
        for skill in task.skills_required:
            if skill not in rep["specializations"]:
                rep["specializations"][skill] = {"count": 0, "avg_quality": 0.0}
                
            spec = rep["specializations"][skill]
            spec["avg_quality"] = (spec["avg_quality"] * spec["count"] + quality) / (spec["count"] + 1)
            spec["count"] += 1
        
        self._save_reputation()
    
    # MARKET ANALYTICS
    
    def get_market_overview(self) -> Dict[str, Any]:
        """Get comprehensive market overview"""
        
        # Count tasks by status
        status_counts = defaultdict(int)
        for task in self.active_tasks.values():
            status_counts[task.status.value] += 1
            
        # Get top performing agents
        top_agents = sorted(
            self.agent_reputation.items(),
            key=lambda x: x[1].get("success_rate", 0) * x[1].get("tasks_completed", 0),
            reverse=True
        )[:5]
        
        # Recent transaction volume
        recent_transactions = self._get_recent_transactions(days=7)
        
        overview = {
            "market_health": {
                "active_tasks": len([t for t in self.active_tasks.values() if t.status in [TaskStatus.BROADCAST, TaskStatus.BIDDING]]),
                "tasks_in_progress": len([t for t in self.active_tasks.values() if t.status == TaskStatus.IN_PROGRESS]),
                "total_agents": len(self.agent_reputation),
                "market_efficiency_mins": round(self.market_stats.get("market_efficiency", 0), 1)
            },
            "task_distribution": dict(status_counts),
            "performance_metrics": {
                "success_rate": self.market_stats["successful_completions"] / max(1, self.market_stats["total_tasks_posted"]),
                "average_bid_confidence": round(self.market_stats["average_bid_confidence"], 3),
                "weekly_transaction_volume": len(recent_transactions)
            },
            "top_performers": [
                {
                    "agent_id": agent_id,
                    "success_rate": rep["success_rate"],
                    "tasks_completed": rep["tasks_completed"],
                    "specializations": list(rep.get("specializations", {}).keys())[:3]
                }
                for agent_id, rep in top_agents
            ]
        }
        
        return overview
    
    def _get_recent_transactions(self, days: int = 7) -> List[MarketTransaction]:
        """Get recent transactions for analysis"""
        
        if not os.path.exists(self.transactions_file):
            return []
            
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        transactions = []
        
        with open(self.transactions_file, 'r') as f:
            for line in f:
                if line.strip():
                    data = json.loads(line)
                    if data["recorded_at"] >= cutoff:
                        transactions.append(MarketTransaction(**data))
                        
        return transactions
    
    def get_agent_profile(self, agent_id: str) -> Dict[str, Any]:
        """Get detailed agent profile and performance"""
        
        if agent_id not in self.agent_reputation:
            return {"error": "Agent not found"}
            
        rep = self.agent_reputation[agent_id]
        
        # Get agent's recent bids and assignments
        recent_bids = []
        if os.path.exists(self.bids_file):
            with open(self.bids_file, 'r') as f:
                for line in f:
                    if line.strip():
                        data = json.loads(line)
                        if data.get("agent_id") == agent_id:
                            recent_bids.append(data)
        
        profile = {
            "agent_id": agent_id,
            "reputation_score": rep["success_rate"],
            "tasks_completed": rep["tasks_completed"],
            "average_quality": round(rep["average_quality"], 3),
            "competencies": rep["competencies"],
            "specializations": rep.get("specializations", {}),
            "total_merit_earned": rep.get("total_merit_earned", 0),
            "recent_activity": {
                "bids_submitted": len(recent_bids),
                "avg_bid_confidence": sum(b.get("confidence", 0) for b in recent_bids) / max(1, len(recent_bids))
            }
        }
        
        return profile

def main():
    """Demo the swarm coordination system"""
    
    print("SINCOR Swarm Coordination System")
    print("=" * 35)
    
    # Create market
    market = TaskMarket()
    
    # Initialize some agent reputations
    market.agent_reputation = {
        "E-auriga-01": {
            "tasks_completed": 5,
            "success_rate": 0.8,
            "average_quality": 0.82,
            "competencies": ["prospect", "research", "summarize"],
            "total_merit_earned": 450,
            "specializations": {"prospect": {"count": 3, "avg_quality": 0.85}}
        },
        "E-vega-02": {
            "tasks_completed": 3,
            "success_rate": 0.9,
            "average_quality": 0.88,
            "competencies": ["prospect", "research", "summarize"],
            "total_merit_earned": 340,
            "specializations": {"prospect": {"count": 2, "avg_quality": 0.9}}
        }
    }
    market._save_reputation()
    
    # Post a task
    task = TaskContract(
        task_id="",  # Will be auto-generated
        goal="Research competitive landscape in AI market",
        description="Identify top 10 AI companies, their products, and market positioning",
        skills_required=["prospect", "research", "summarize"],
        priority=0.8,
        reward=150,
        deadline=(datetime.now() + timedelta(hours=6)).isoformat(),
        budget_tokens=8000,
        budget_tool_calls=25,
        created_by="system",
        created_at="",  # Will be auto-set
        status=TaskStatus.BROADCAST,
        success_criteria=["comprehensive_report", "competitor_analysis", "market_insights"],
        context={"industry": "AI", "focus": "enterprise_solutions"}
    )
    
    task_id = market.post_task(task)
    
    # Submit bids
    bid1 = AgentBid(
        bid_id="",
        task_id=task_id,
        agent_id="E-auriga-01",
        archetype="Scout",
        confidence=0.85,
        estimated_cost_tokens=6000,
        estimated_cost_calls=20,
        estimated_duration=180,
        plan=["search_ai_companies", "extract_data", "analyze_positioning", "create_report"],
        unique_value="Specialized in tech industry research with proven track record",
        agent_track_record={"prospect": 0.85, "research": 0.80, "summarize": 0.82},
        submitted_at="",
        status=BidStatus.SUBMITTED
    )
    
    bid2 = AgentBid(
        bid_id="",
        task_id=task_id,
        agent_id="E-vega-02",
        archetype="Scout",
        confidence=0.78,
        estimated_cost_tokens=7000,
        estimated_cost_calls=22,
        estimated_duration=210,
        plan=["market_research", "competitor_analysis", "synthesis"],
        unique_value="Strong negotiation skills for accessing premium data sources",
        agent_track_record={"prospect": 0.90, "research": 0.75},
        submitted_at="",
        status=BidStatus.SUBMITTED
    )
    
    market.submit_bid(bid1)
    market.submit_bid(bid2)
    
    # Evaluate and award
    assignment_id = market.evaluate_and_award_task(task_id)
    
    if assignment_id:
        # Start execution
        market.start_task_execution(assignment_id)
        
        # Update progress
        market.update_task_progress(assignment_id, {
            "status": "data_collection_complete",
            "progress": 0.6,
            "companies_found": 12
        })
        
        # Complete task
        completion_results = {
            "quality_score": 0.88,
            "deliverables": ["market_report.pdf", "competitor_matrix.xlsx"],
            "insights_count": 15,
            "stakeholder_satisfaction": 0.9
        }
        
        market.complete_task(assignment_id, completion_results)
    
    # Show market overview
    print("\\n" + "=" * 35)
    overview = market.get_market_overview()
    print("Market Overview:")
    print(f"  Active tasks: {overview['market_health']['active_tasks']}")
    print(f"  Success rate: {overview['performance_metrics']['success_rate']:.1%}")
    print(f"  Market efficiency: {overview['market_health']['market_efficiency_mins']} minutes")
    
    print("\\nTop Performers:")
    for performer in overview['top_performers']:
        print(f"  {performer['agent_id']}: {performer['success_rate']:.1%} success, {performer['tasks_completed']} tasks")

if __name__ == "__main__":
    main()