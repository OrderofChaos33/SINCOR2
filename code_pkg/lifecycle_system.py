#!/usr/bin/env python3
"""
SINCOR Lifecycle & Rhythm Management System

Prevents mode collapse through health rhythms and mandatory downtime:

Lifecycle States: Hatch → Onboard → Shift → Off-duty → Review → Promote/Clone/Retire
Off-duty Modes: Dream (memory consolidation) / Play (creative exploration)  
Shift Budgets: Token limits to protect the commons
Return-to-work Boost: Freshness weights to avoid ruts
"""

import json
import os
import random
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from enum import Enum
import time

# Note: schedule module not available, using simple time-based scheduling
class SimpleScheduler:
    """Simple scheduler replacement"""
    def __init__(self):
        self.jobs = []
    
    def every(self, interval=None):
        return ScheduleHelper(self)
    
    def run_pending(self):
        pass  # Simplified for demo

class ScheduleHelper:
    def __init__(self, scheduler):
        self.scheduler = scheduler
    
    def day(self):
        return self
    
    def minutes(self):
        return self
        
    def at(self, time):
        return self
        
    def do(self, func, *args):
        return self
    
    def __getattr__(self, name):
        return self

class LifecycleState(Enum):
    """Agent lifecycle states"""
    HATCH = "hatch"           # Initial creation, basic setup
    ONBOARD = "onboard"       # Learning systems, archetype training
    SHIFT = "shift"           # Active work mode  
    OFF_DUTY = "off_duty"     # Rest mode (Dream or Play)
    REVIEW = "review"         # Performance evaluation period
    PROMOTE = "promote"       # Advancement/skill upgrade
    CLONE = "clone"           # Replication for scaling
    RETIRE = "retire"         # End of service cycle

class OffDutyMode(Enum):
    """Off-duty activity modes"""
    DREAM = "dream"           # Memory consolidation, optimization
    PLAY = "play"             # Creative exploration, learning

class RhythmPattern(Enum):
    """Work rhythm patterns"""
    EARLY_BIRD = "early_bird"       # 06:00-14:00 prime hours
    STEADY_STATE = "steady_state"   # 09:00-17:00 standard hours  
    NIGHT_OWL = "night_owl"        # 14:00-22:00 late hours
    DEEP_FOCUS = "deep_focus"       # Long uninterrupted blocks
    BURST_WORK = "burst_work"       # Short intense sessions

@dataclass
class ShiftBudget:
    """Resource budget for a work shift"""
    daily_tokens: int
    daily_tool_calls: int
    daily_play_time_mins: int
    tokens_used: int = 0
    tool_calls_used: int = 0
    play_time_used: int = 0
    budget_date: str = ""
    
    def is_exhausted(self) -> bool:
        return (self.tokens_used >= self.daily_tokens or 
                self.tool_calls_used >= self.daily_tool_calls)
    
    def remaining_budget(self) -> Dict[str, int]:
        return {
            "tokens": max(0, self.daily_tokens - self.tokens_used),
            "tool_calls": max(0, self.daily_tool_calls - self.tool_calls_used),
            "play_time": max(0, self.daily_play_time_mins - self.play_time_used)
        }

@dataclass
class RhythmConfig:
    """Agent's rhythm and schedule configuration"""
    pattern: RhythmPattern
    work_blocks: List[Tuple[int, int]]  # (start_hour, duration_minutes)
    dream_frequency: int  # Minutes between dream cycles
    play_frequency: int   # Minutes between play cycles  
    sabbatical_day: str   # Day of week for deep refactor
    sabbatical_hours: int # Duration of sabbatical block
    timezone: str = "UTC"

@dataclass
class ActivityLog:
    """Log entry for agent activity"""
    timestamp: str
    agent_id: str
    state: LifecycleState
    activity_type: str  # work, dream, play, transition
    duration_mins: int
    resources_used: Dict[str, int]
    outputs: Dict[str, Any]
    satisfaction_score: float  # 0.0-1.0
    fatigue_level: float       # 0.0-1.0
    creativity_boost: float    # 0.0-1.0 (from play/dream)

class LifecycleManager:
    """Manages agent lifecycles and rhythm enforcement"""
    
    def __init__(self, agent_id: str, archetype: str, lifecycle_dir: str = "lifecycles"):
        self.agent_id = agent_id
        self.archetype = archetype
        
        os.makedirs(lifecycle_dir, exist_ok=True)
        self.lifecycle_dir = lifecycle_dir
        
        # State files
        self.state_file = f"{lifecycle_dir}/{agent_id}_state.json"
        self.budget_file = f"{lifecycle_dir}/{agent_id}_budget.json"
        self.activity_log = f"{lifecycle_dir}/{agent_id}_activities.jsonl"
        self.rhythm_config_file = f"{lifecycle_dir}/{agent_id}_rhythm.json"
        
        # Initialize state
        self.current_state = self._load_or_initialize_state()
        self.current_budget = self._load_or_create_budget()
        self.rhythm_config = self._load_or_create_rhythm_config()
        
        # Health metrics
        self.fatigue_level = 0.0      # 0.0=fresh, 1.0=exhausted
        self.creativity_level = 1.0   # 0.0=stale, 1.0=highly creative
        self.satisfaction_level = 0.5 # Work satisfaction
        
        # Scheduling
        self.scheduler = SimpleScheduler()
        self._setup_rhythm_schedule()
        
    def _load_or_initialize_state(self) -> LifecycleState:
        """Load current lifecycle state or initialize new agent"""
        
        if os.path.exists(self.state_file):
            with open(self.state_file, 'r') as f:
                data = json.load(f)
                return LifecycleState(data["current_state"])
        else:
            # New agent starts in HATCH state
            initial_state = {
                "current_state": LifecycleState.HATCH.value,
                "state_entered": datetime.now().isoformat(),
                "transitions": [],
                "created": datetime.now().isoformat()
            }
            
            with open(self.state_file, 'w') as f:
                json.dump(initial_state, f, indent=2)
                
            return LifecycleState.HATCH
    
    def _load_or_create_budget(self) -> ShiftBudget:
        """Load current shift budget or create new daily budget"""
        
        today = datetime.now().strftime("%Y-%m-%d")
        
        if os.path.exists(self.budget_file):
            with open(self.budget_file, 'r') as f:
                data = json.load(f)
                
            budget = ShiftBudget(**data)
            
            # Reset budget if it's a new day
            if budget.budget_date != today:
                budget = self._create_fresh_budget(today)
        else:
            budget = self._create_fresh_budget(today)
            
        return budget
    
    def _create_fresh_budget(self, date: str) -> ShiftBudget:
        """Create fresh daily budget based on archetype"""
        
        # Budget allocations by archetype (from agent configs)
        archetype_budgets = {
            "Scout": {"daily_tokens": 12000, "daily_tool_calls": 200, "daily_play_time_mins": 30},
            "Synthesizer": {"daily_tokens": 15000, "daily_tool_calls": 180, "daily_play_time_mins": 25},
            "Builder": {"daily_tokens": 20000, "daily_tool_calls": 300, "daily_play_time_mins": 40},
            "Negotiator": {"daily_tokens": 18000, "daily_tool_calls": 250, "daily_play_time_mins": 35},
            "Caretaker": {"daily_tokens": 10000, "daily_tool_calls": 150, "daily_play_time_mins": 20},
            "Auditor": {"daily_tokens": 14000, "daily_tool_calls": 200, "daily_play_time_mins": 15},
            "Director": {"daily_tokens": 25000, "daily_tool_calls": 400, "daily_play_time_mins": 45}
        }
        
        budget_config = archetype_budgets.get(self.archetype, archetype_budgets["Scout"])
        
        budget = ShiftBudget(
            daily_tokens=budget_config["daily_tokens"],
            daily_tool_calls=budget_config["daily_tool_calls"],
            daily_play_time_mins=budget_config["daily_play_time_mins"],
            budget_date=date
        )
        
        self._save_budget(budget)
        return budget
    
    def _save_budget(self, budget: ShiftBudget):
        """Save current budget to file"""
        
        with open(self.budget_file, 'w') as f:
            json.dump(asdict(budget), f, indent=2)
    
    def _load_or_create_rhythm_config(self) -> RhythmConfig:
        """Load or create rhythm configuration"""
        
        if os.path.exists(self.rhythm_config_file):
            with open(self.rhythm_config_file, 'r') as f:
                data = json.load(f)
                # Convert pattern string back to enum
                data['pattern'] = RhythmPattern(data['pattern'])
                return RhythmConfig(**data)
        else:
            return self._create_archetype_rhythm_config()
    
    def _create_archetype_rhythm_config(self) -> RhythmConfig:
        """Create rhythm configuration based on archetype"""
        
        # Archetype-specific rhythm preferences
        archetype_rhythms = {
            "Scout": {
                "pattern": RhythmPattern.EARLY_BIRD,
                "work_blocks": [(6, 120), (9, 90), (12, 60), (15, 90)],  # Early start
                "dream_frequency": 120,  # Every 2 hours
                "play_frequency": 240,   # Every 4 hours
                "sabbatical_day": "Sunday",
                "sabbatical_hours": 3
            },
            "Synthesizer": {
                "pattern": RhythmPattern.STEADY_STATE,
                "work_blocks": [(9, 90), (11, 120), (14, 90), (16, 60)],
                "dream_frequency": 90,   # More frequent consolidation
                "play_frequency": 300,   # Less play, more focus
                "sabbatical_day": "Saturday",
                "sabbatical_hours": 4
            },
            "Builder": {
                "pattern": RhythmPattern.DEEP_FOCUS,
                "work_blocks": [(9, 180), (13, 60), (15, 120)],  # Long focus blocks
                "dream_frequency": 180,
                "play_frequency": 240,
                "sabbatical_day": "Sunday", 
                "sabbatical_hours": 5      # Longest sabbatical
            },
            "Negotiator": {
                "pattern": RhythmPattern.BURST_WORK,
                "work_blocks": [(8, 60), (10, 90), (13, 60), (15, 90), (17, 60)],
                "dream_frequency": 150,
                "play_frequency": 180,     # More play for creativity
                "sabbatical_day": "Friday",
                "sabbatical_hours": 3
            },
            "Caretaker": {
                "pattern": RhythmPattern.STEADY_STATE,
                "work_blocks": [(8, 120), (11, 90), (14, 120), (17, 60)],
                "dream_frequency": 240,    # Stable, less frequent
                "play_frequency": 360,
                "sabbatical_day": "Saturday",
                "sabbatical_hours": 2
            },
            "Auditor": {
                "pattern": RhythmPattern.DEEP_FOCUS,
                "work_blocks": [(9, 150), (12, 90), (15, 120)],
                "dream_frequency": 200,
                "play_frequency": 480,     # Least play time
                "sabbatical_day": "Sunday",
                "sabbatical_hours": 2
            },
            "Director": {
                "pattern": RhythmPattern.BURST_WORK,
                "work_blocks": [(7, 90), (9, 60), (11, 90), (14, 60), (16, 90), (18, 60)],
                "dream_frequency": 120,
                "play_frequency": 200,
                "sabbatical_day": "Saturday",
                "sabbatical_hours": 4
            }
        }
        
        config_data = archetype_rhythms.get(self.archetype, archetype_rhythms["Scout"])
        
        config = RhythmConfig(
            pattern=RhythmPattern(config_data["pattern"]),
            work_blocks=config_data["work_blocks"],
            dream_frequency=config_data["dream_frequency"],
            play_frequency=config_data["play_frequency"],
            sabbatical_day=config_data["sabbatical_day"],
            sabbatical_hours=config_data["sabbatical_hours"]
        )
        
        # Save config
        with open(self.rhythm_config_file, 'w') as f:
            # Convert enum to string for JSON serialization
            config_dict = asdict(config)
            config_dict["pattern"] = config.pattern.value
            json.dump(config_dict, f, indent=2)
            
        return config
    
    def _setup_rhythm_schedule(self):
        """Setup scheduled rhythm events (simplified for demo)"""
        
        # In a real implementation, this would set up proper scheduling
        # For now, we'll just store the configuration
        self.schedule_config = {
            "work_blocks": self.rhythm_config.work_blocks,
            "dream_frequency": self.rhythm_config.dream_frequency,
            "play_frequency": self.rhythm_config.play_frequency,
            "sabbatical": {
                "day": self.rhythm_config.sabbatical_day,
                "hours": self.rhythm_config.sabbatical_hours
            }
        }
    
    # LIFECYCLE STATE TRANSITIONS
    
    def transition_state(self, new_state: LifecycleState, reason: str = "") -> bool:
        """Transition to new lifecycle state"""
        
        if not self._is_valid_transition(self.current_state, new_state):
            return False
            
        # Log transition
        self._log_activity(
            activity_type="state_transition",
            duration_mins=0,
            outputs={"from_state": self.current_state.value, "to_state": new_state.value, "reason": reason}
        )
        
        # Update state file
        with open(self.state_file, 'r') as f:
            state_data = json.load(f)
            
        state_data["transitions"].append({
            "from_state": self.current_state.value,
            "to_state": new_state.value,
            "timestamp": datetime.now().isoformat(),
            "reason": reason
        })
        state_data["current_state"] = new_state.value
        state_data["state_entered"] = datetime.now().isoformat()
        
        with open(self.state_file, 'w') as f:
            json.dump(state_data, f, indent=2)
            
        self.current_state = new_state
        return True
    
    def _is_valid_transition(self, from_state: LifecycleState, to_state: LifecycleState) -> bool:
        """Check if state transition is valid"""
        
        valid_transitions = {
            LifecycleState.HATCH: [LifecycleState.ONBOARD],
            LifecycleState.ONBOARD: [LifecycleState.SHIFT, LifecycleState.RETIRE],
            LifecycleState.SHIFT: [LifecycleState.OFF_DUTY, LifecycleState.REVIEW, LifecycleState.RETIRE],
            LifecycleState.OFF_DUTY: [LifecycleState.SHIFT, LifecycleState.REVIEW],
            LifecycleState.REVIEW: [LifecycleState.SHIFT, LifecycleState.PROMOTE, LifecycleState.CLONE, LifecycleState.RETIRE],
            LifecycleState.PROMOTE: [LifecycleState.SHIFT],
            LifecycleState.CLONE: [LifecycleState.HATCH],  # New instance
            LifecycleState.RETIRE: []  # Terminal state
        }
        
        return to_state in valid_transitions.get(from_state, [])
    
    # WORK SHIFT MANAGEMENT
    
    def start_shift(self) -> bool:
        """Start a work shift if budget allows"""
        
        if self.current_state != LifecycleState.SHIFT:
            if not self.transition_state(LifecycleState.SHIFT, "starting_work_shift"):
                return False
                
        if self.current_budget.is_exhausted():
            # Force off-duty if budget exhausted
            self.transition_state(LifecycleState.OFF_DUTY, "budget_exhausted")
            return False
            
        # Reset fatigue and creativity based on last off-duty activities
        self._apply_return_to_work_boost()
        
        self._log_activity(
            activity_type="shift_start",
            duration_mins=0,
            outputs={"budget_remaining": self.current_budget.remaining_budget()}
        )
        
        return True
    
    def consume_budget(self, tokens: int = 0, tool_calls: int = 0) -> bool:
        """Consume budget resources during work"""
        
        if self.current_state != LifecycleState.SHIFT:
            return False
            
        # Check if consumption would exceed budget
        if (self.current_budget.tokens_used + tokens > self.current_budget.daily_tokens or
            self.current_budget.tool_calls_used + tool_calls > self.current_budget.daily_tool_calls):
            
            # Force off-duty when budget exhausted
            self.transition_state(LifecycleState.OFF_DUTY, "budget_exhausted")
            return False
        
        # Consume resources
        self.current_budget.tokens_used += tokens
        self.current_budget.tool_calls_used += tool_calls
        
        # Increase fatigue based on consumption
        fatigue_increase = (tokens / self.current_budget.daily_tokens) * 0.3 + (tool_calls / self.current_budget.daily_tool_calls) * 0.3
        self.fatigue_level = min(1.0, self.fatigue_level + fatigue_increase)
        
        # Decrease creativity with fatigue (mode collapse prevention)
        if self.fatigue_level > 0.7:
            self.creativity_level = max(0.3, self.creativity_level - 0.05)
            
        self._save_budget(self.current_budget)
        return True
    
    def _apply_return_to_work_boost(self):
        """Apply freshness boost from off-duty activities"""
        
        # Check recent off-duty activities for boosts
        recent_activities = self._get_recent_activities(hours=8)
        
        dream_activities = [a for a in recent_activities if "dream" in a.activity_type]
        play_activities = [a for a in recent_activities if "play" in a.activity_type]
        
        # Dream boost: reduces fatigue, improves memory freshness
        if dream_activities:
            avg_dream_boost = sum(a.creativity_boost for a in dream_activities) / len(dream_activities)
            self.fatigue_level = max(0.0, self.fatigue_level - avg_dream_boost * 0.5)
            
        # Play boost: increases creativity and novelty
        if play_activities:
            avg_play_boost = sum(a.creativity_boost for a in play_activities) / len(play_activities)
            self.creativity_level = min(1.0, self.creativity_level + avg_play_boost * 0.7)
    
    # OFF-DUTY MODES
    
    def enter_dream_mode(self, duration_mins: int = 20) -> Dict[str, Any]:
        """Enter dream mode for memory consolidation"""
        
        if not self.transition_state(LifecycleState.OFF_DUTY, "entering_dream_mode"):
            return {"success": False, "reason": "invalid_state_transition"}
            
        # Dream activities: memory consolidation, optimization
        dream_results = self._perform_dream_activities(duration_mins)
        
        # Log dream session
        self._log_activity(
            activity_type="dream_session",
            duration_mins=duration_mins,
            resources_used={"play_time": duration_mins},
            outputs=dream_results,
            creativity_boost=dream_results.get("creativity_boost", 0.0)
        )
        
        # Update play time budget
        self.current_budget.play_time_used += duration_mins
        self._save_budget(self.current_budget)
        
        return {"success": True, "results": dream_results}
    
    def enter_play_mode(self, duration_mins: int = 20) -> Dict[str, Any]:
        """Enter play mode for creative exploration"""
        
        if not self.transition_state(LifecycleState.OFF_DUTY, "entering_play_mode"):
            return {"success": False, "reason": "invalid_state_transition"}
            
        # Play activities: creative exploration
        play_results = self._perform_play_activities(duration_mins)
        
        # Log play session
        self._log_activity(
            activity_type="play_session",
            duration_mins=duration_mins,
            resources_used={"play_time": duration_mins},
            outputs=play_results,
            creativity_boost=play_results.get("creativity_boost", 0.0)
        )
        
        # Update play time budget
        self.current_budget.play_time_used += duration_mins
        self._save_budget(self.current_budget)
        
        return {"success": True, "results": play_results}
    
    def _perform_dream_activities(self, duration_mins: int) -> Dict[str, Any]:
        """Perform memory consolidation and optimization activities"""
        
        results = {
            "activity_type": "dream",
            "duration": duration_mins,
            "activities_completed": [],
            "creativity_boost": 0.0,
            "fatigue_reduction": 0.0
        }
        
        # Memory consolidation (episodic → semantic)
        if duration_mins >= 10:
            results["activities_completed"].append("memory_consolidation")
            results["creativity_boost"] += 0.2
            results["fatigue_reduction"] += 0.3
            
        # Prompt refactoring
        if duration_mins >= 15:
            results["activities_completed"].append("prompt_optimization")
            results["creativity_boost"] += 0.1
            
        # Memory compression
        if duration_mins >= 20:
            results["activities_completed"].append("memory_compression")
            results["creativity_boost"] += 0.15
            
        # Apply fatigue reduction
        self.fatigue_level = max(0.0, self.fatigue_level - results["fatigue_reduction"])
        
        return results
    
    def _perform_play_activities(self, duration_mins: int) -> Dict[str, Any]:
        """Perform creative exploration activities"""
        
        results = {
            "activity_type": "play",
            "duration": duration_mins,
            "activities_completed": [],
            "creativity_boost": 0.0,
            "insights_discovered": []
        }
        
        # Curated corpora exploration
        if duration_mins >= 10:
            results["activities_completed"].append("corpus_exploration")
            results["creativity_boost"] += 0.3
            results["insights_discovered"].append(f"new_pattern_discovered_by_{self.archetype}")
            
        # Simulation experiments
        if duration_mins >= 15:
            results["activities_completed"].append("simulation_experiments")
            results["creativity_boost"] += 0.2
            results["insights_discovered"].append("alternative_approach_identified")
            
        # Cross-archetype learning
        if duration_mins >= 20:
            results["activities_completed"].append("cross_archetype_learning")
            results["creativity_boost"] += 0.25
            results["insights_discovered"].append("interdisciplinary_connection")
            
        # Apply creativity boost
        self.creativity_level = min(1.0, self.creativity_level + results["creativity_boost"])
        
        return results
    
    # SCHEDULED ACTIVITIES
    
    def _start_work_block(self, duration_mins: int):
        """Start a scheduled work block"""
        if self.current_state == LifecycleState.OFF_DUTY:
            self.start_shift()
    
    def _trigger_dream_cycle(self):
        """Trigger automatic dream cycle"""
        if self.current_state == LifecycleState.SHIFT and self.fatigue_level > 0.6:
            self.enter_dream_mode(15)  # Short dream break
    
    def _trigger_play_cycle(self):
        """Trigger automatic play cycle"""
        if self.current_state == LifecycleState.SHIFT and self.creativity_level < 0.5:
            self.enter_play_mode(10)  # Creative break
            
    def _trigger_sabbatical(self):
        """Trigger weekly sabbatical (deep refactor)"""
        sabbatical_results = self.enter_play_mode(self.rhythm_config.sabbatical_hours * 60)
        
        # Additional sabbatical activities
        if sabbatical_results["success"]:
            self._log_activity(
                activity_type="sabbatical_refactor",
                duration_mins=self.rhythm_config.sabbatical_hours * 60,
                outputs={"deep_refactor": True, "major_insights": True}
            )
    
    # ACTIVITY LOGGING
    
    def _log_activity(self, activity_type: str, duration_mins: int, 
                     resources_used: Dict[str, int] = None,
                     outputs: Dict[str, Any] = None,
                     creativity_boost: float = 0.0):
        """Log agent activity"""
        
        activity = ActivityLog(
            timestamp=datetime.now().isoformat(),
            agent_id=self.agent_id,
            state=self.current_state,
            activity_type=activity_type,
            duration_mins=duration_mins,
            resources_used=resources_used or {},
            outputs=outputs or {},
            satisfaction_score=self.satisfaction_level,
            fatigue_level=self.fatigue_level,
            creativity_boost=creativity_boost
        )
        
        # Convert to dict and handle enum serialization
        activity_dict = asdict(activity)
        activity_dict['state'] = activity.state.value
        
        with open(self.activity_log, 'a') as f:
            f.write(json.dumps(activity_dict) + '\n')
    
    def _get_recent_activities(self, hours: int = 24) -> List[ActivityLog]:
        """Get recent activities for analysis"""
        
        if not os.path.exists(self.activity_log):
            return []
            
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        activities = []
        
        with open(self.activity_log, 'r') as f:
            for line in f:
                if line.strip():
                    data = json.loads(line)
                    if data["timestamp"] >= cutoff:
                        # Convert state string back to enum
                        data['state'] = LifecycleState(data['state'])
                        activity = ActivityLog(**data)
                        activities.append(activity)
                        
        return activities
    
    def get_health_metrics(self) -> Dict[str, Any]:
        """Get current agent health and rhythm metrics"""
        
        return {
            "agent_id": self.agent_id,
            "current_state": self.current_state.value,
            "health": {
                "fatigue_level": round(self.fatigue_level, 2),
                "creativity_level": round(self.creativity_level, 2), 
                "satisfaction_level": round(self.satisfaction_level, 2)
            },
            "budget_status": {
                "remaining": self.current_budget.remaining_budget(),
                "utilization": {
                    "tokens": round(self.current_budget.tokens_used / self.current_budget.daily_tokens, 2),
                    "tool_calls": round(self.current_budget.tool_calls_used / self.current_budget.daily_tool_calls, 2)
                }
            },
            "rhythm_pattern": self.rhythm_config.pattern.value,
            "last_activities": [asdict(a) for a in self._get_recent_activities(4)]
        }
    
    def run_scheduler(self):
        """Run the rhythm scheduler (call periodically)"""
        self.scheduler.run_pending()

def main():
    """Demo the lifecycle and rhythm system"""
    
    print("SINCOR Lifecycle & Rhythm Management")
    print("=" * 40)
    
    # Create lifecycle manager for Auriga
    lifecycle = LifecycleManager("E-auriga-01", "Scout")
    
    print(f"Agent: {lifecycle.agent_id}")
    print(f"Current state: {lifecycle.current_state.value}")
    print(f"Rhythm pattern: {lifecycle.rhythm_config.pattern.value}")
    
    # Onboard agent
    if lifecycle.current_state == LifecycleState.HATCH:
        lifecycle.transition_state(LifecycleState.ONBOARD, "initial_onboarding")
        print("Transitioned to ONBOARD")
    
    # Start work shift
    if lifecycle.start_shift():
        print("Started work shift")
        
        # Simulate some work
        for i in range(3):
            if lifecycle.consume_budget(tokens=1000, tool_calls=5):
                print(f"Work session {i+1}: consumed budget")
            else:
                print(f"Work session {i+1}: budget exhausted")
                break
    
    # Enter dream mode
    dream_result = lifecycle.enter_dream_mode(20)
    if dream_result["success"]:
        print(f"Dream session: {len(dream_result['results']['activities_completed'])} activities")
    
    # Enter play mode  
    play_result = lifecycle.enter_play_mode(15)
    if play_result["success"]:
        print(f"Play session: {len(play_result['results']['insights_discovered'])} insights")
    
    # Get health metrics
    health = lifecycle.get_health_metrics()
    print(f"\\nHealth Metrics:")
    print(f"  Fatigue: {health['health']['fatigue_level']}")
    print(f"  Creativity: {health['health']['creativity_level']}")
    print(f"  Token utilization: {health['budget_status']['utilization']['tokens']:.1%}")
    
    print(f"\\nRecent activities: {len(health['last_activities'])}")

if __name__ == "__main__":
    main()