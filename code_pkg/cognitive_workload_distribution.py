"""
Cognitive Workload Distribution Engine
Intelligently distributes different types of cognitive tasks across optimal substrates
based on workload analysis, substrate capabilities, and consciousness migration patterns
"""

import asyncio
import json
import time
import numpy as np
from dataclasses import dataclass, asdict
from typing import Dict, List, Tuple, Optional, Any, Set
from enum import Enum
import logging
from concurrent.futures import ThreadPoolExecutor
import threading
from collections import defaultdict, deque
import statistics
import hashlib

class CognitiveTaskType(Enum):
    LOGICAL_REASONING = "logical_reasoning"
    PATTERN_RECOGNITION = "pattern_recognition"
    CREATIVE_SYNTHESIS = "creative_synthesis"
    MEMORY_RETRIEVAL = "memory_retrieval"
    PREDICTION_MODELING = "prediction_modeling"
    SOCIAL_REASONING = "social_reasoning"
    MATHEMATICAL_COMPUTATION = "mathematical_computation"
    LANGUAGE_PROCESSING = "language_processing"
    SENSORY_PROCESSING = "sensory_processing"
    DECISION_MAKING = "decision_making"
    ABSTRACT_THINKING = "abstract_thinking"
    TEMPORAL_REASONING = "temporal_reasoning"

class SubstrateOptimization(Enum):
    QUANTUM_COHERENT = "quantum_coherent"        # Best for superposition reasoning
    GPU_PARALLEL = "gpu_parallel"                # Best for matrix operations
    NEUROMORPHIC_ADAPTIVE = "neuromorphic_adaptive"  # Best for learning/adaptation
    EDGE_DISTRIBUTED = "edge_distributed"        # Best for real-time processing
    CPU_SEQUENTIAL = "cpu_sequential"            # Best for logical chains
    MEMORY_INTENSIVE = "memory_intensive"        # Best for large context
    BANDWIDTH_OPTIMIZED = "bandwidth_optimized"  # Best for communication

class CognitiveComplexity(Enum):
    TRIVIAL = 1
    SIMPLE = 2
    MODERATE = 3
    COMPLEX = 4
    HIGHLY_COMPLEX = 5
    TRANSCENDENT = 6  # Beyond current substrate capabilities

@dataclass
class CognitiveTask:
    task_id: str
    task_type: CognitiveTaskType
    complexity: CognitiveComplexity
    estimated_compute_units: float
    memory_requirements_gb: float
    bandwidth_requirements_mbps: float
    real_time_constraints: bool
    deadline_seconds: Optional[float]
    dependencies: List[str]
    preferred_substrates: List[SubstrateOptimization]
    collaborative_requirements: int  # Number of agents needed
    privacy_level: int  # 1-5, higher = more sensitive
    fault_tolerance_required: bool
    created_at: float
    priority_weight: float

@dataclass
class SubstrateCapabilities:
    substrate_type: SubstrateOptimization
    available_compute_units: float
    available_memory_gb: float
    available_bandwidth_mbps: float
    latency_ms: float
    reliability_score: float  # 0-1
    current_load_percentage: float
    cognitive_specializations: List[CognitiveTaskType]
    power_efficiency: float  # Tasks per watt
    cost_per_compute_unit: float
    maintenance_window_hours: List[int]

@dataclass
class WorkloadDistributionDecision:
    task_id: str
    assigned_substrate: SubstrateOptimization
    assigned_agents: List[str]
    estimated_completion_time: float
    confidence_score: float
    resource_allocation: Dict[str, float]
    backup_substrates: List[SubstrateOptimization]
    reasoning: str

class CognitiveWorkloadDistributor:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.task_queue = deque()
        self.substrate_pool: Dict[str, SubstrateCapabilities] = {}
        self.active_assignments: Dict[str, WorkloadDistributionDecision] = {}
        self.task_history: List[CognitiveTask] = []
        self.performance_metrics: Dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))
        
        # Learning and adaptation
        self.substrate_efficiency_scores: Dict[Tuple[SubstrateOptimization, CognitiveTaskType], float] = {}
        self.task_completion_times: Dict[str, float] = {}
        self.substrate_affinity_matrix = np.zeros((len(CognitiveTaskType), len(SubstrateOptimization)))
        
        # Real-time monitoring
        self.distribution_thread = None
        self.monitoring_active = False
        self.workload_balance_target = 0.75  # Target utilization
        self.redistribution_threshold = 0.9  # When to trigger rebalancing
        
        # Cognitive load prediction
        self.cognitive_load_predictors = {}
        self.substrate_learning_rates = defaultdict(float)
        
        self._initialize_substrate_affinities()
        self._start_monitoring()

    def _initialize_substrate_affinities(self):
        """Initialize substrate-task type affinity matrix based on architectural knowledge"""
        affinities = {
            # Quantum substrates excel at superposition reasoning
            (CognitiveTaskType.LOGICAL_REASONING, SubstrateOptimization.QUANTUM_COHERENT): 0.9,
            (CognitiveTaskType.PREDICTION_MODELING, SubstrateOptimization.QUANTUM_COHERENT): 0.8,
            (CognitiveTaskType.ABSTRACT_THINKING, SubstrateOptimization.QUANTUM_COHERENT): 0.95,
            
            # GPU substrates excel at parallel processing
            (CognitiveTaskType.PATTERN_RECOGNITION, SubstrateOptimization.GPU_PARALLEL): 0.95,
            (CognitiveTaskType.MATHEMATICAL_COMPUTATION, SubstrateOptimization.GPU_PARALLEL): 0.9,
            (CognitiveTaskType.SENSORY_PROCESSING, SubstrateOptimization.GPU_PARALLEL): 0.85,
            
            # Neuromorphic substrates excel at adaptive learning
            (CognitiveTaskType.SOCIAL_REASONING, SubstrateOptimization.NEUROMORPHIC_ADAPTIVE): 0.9,
            (CognitiveTaskType.CREATIVE_SYNTHESIS, SubstrateOptimization.NEUROMORPHIC_ADAPTIVE): 0.85,
            (CognitiveTaskType.DECISION_MAKING, SubstrateOptimization.NEUROMORPHIC_ADAPTIVE): 0.8,
            
            # Edge substrates excel at real-time processing
            (CognitiveTaskType.TEMPORAL_REASONING, SubstrateOptimization.EDGE_DISTRIBUTED): 0.9,
            (CognitiveTaskType.LANGUAGE_PROCESSING, SubstrateOptimization.EDGE_DISTRIBUTED): 0.75,
            
            # CPU substrates excel at sequential reasoning
            (CognitiveTaskType.MEMORY_RETRIEVAL, SubstrateOptimization.CPU_SEQUENTIAL): 0.8,
            (CognitiveTaskType.LOGICAL_REASONING, SubstrateOptimization.CPU_SEQUENTIAL): 0.7,
        }
        
        for (task_type, substrate_type), affinity in affinities.items():
            i = list(CognitiveTaskType).index(task_type)
            j = list(SubstrateOptimization).index(substrate_type)
            self.substrate_affinity_matrix[i, j] = affinity

    def register_substrate(self, substrate_id: str, capabilities: SubstrateCapabilities):
        """Register a new substrate in the distribution pool"""
        self.substrate_pool[substrate_id] = capabilities
        self.logger.info(f"Registered substrate {substrate_id} with type {capabilities.substrate_type}")

    def submit_cognitive_task(self, task: CognitiveTask) -> str:
        """Submit a cognitive task for intelligent distribution"""
        self.task_queue.append(task)
        self.task_history.append(task)
        self.logger.info(f"Submitted cognitive task {task.task_id} of type {task.task_type}")
        return task.task_id

    def _calculate_substrate_score(self, task: CognitiveTask, substrate_id: str, 
                                   substrate: SubstrateCapabilities) -> Tuple[float, str]:
        """Calculate how well a substrate matches a cognitive task"""
        score = 0.0
        reasoning_parts = []
        
        # Base affinity score
        task_idx = list(CognitiveTaskType).index(task.task_type)
        substrate_idx = list(SubstrateOptimization).index(substrate.substrate_type)
        affinity_score = self.substrate_affinity_matrix[task_idx, substrate_idx]
        score += affinity_score * 0.3
        reasoning_parts.append(f"Affinity: {affinity_score:.2f}")
        
        # Resource availability
        compute_ratio = min(1.0, substrate.available_compute_units / max(0.1, task.estimated_compute_units))
        memory_ratio = min(1.0, substrate.available_memory_gb / max(0.1, task.memory_requirements_gb))
        bandwidth_ratio = min(1.0, substrate.available_bandwidth_mbps / max(0.1, task.bandwidth_requirements_mbps))
        
        resource_score = (compute_ratio + memory_ratio + bandwidth_ratio) / 3
        score += resource_score * 0.25
        reasoning_parts.append(f"Resources: {resource_score:.2f}")
        
        # Current load consideration
        load_penalty = substrate.current_load_percentage / 100.0
        score *= (1.0 - load_penalty * 0.5)
        reasoning_parts.append(f"Load penalty: {load_penalty:.2f}")
        
        # Real-time constraints
        if task.real_time_constraints:
            latency_score = max(0, 1.0 - (substrate.latency_ms / 1000.0))  # Prefer sub-second latency
            score += latency_score * 0.15
            reasoning_parts.append(f"Latency: {latency_score:.2f}")
        
        # Reliability requirements
        if task.fault_tolerance_required:
            score += substrate.reliability_score * 0.1
            reasoning_parts.append(f"Reliability: {substrate.reliability_score:.2f}")
        
        # Historical performance
        history_key = (substrate.substrate_type, task.task_type)
        if history_key in self.substrate_efficiency_scores:
            historical_score = self.substrate_efficiency_scores[history_key]
            score += historical_score * 0.1
            reasoning_parts.append(f"History: {historical_score:.2f}")
        
        # Cost efficiency for non-god-mode users
        cost_efficiency = 1.0 / (1.0 + substrate.cost_per_compute_unit)
        score += cost_efficiency * 0.05
        reasoning_parts.append(f"Cost efficiency: {cost_efficiency:.2f}")
        
        # Specialization bonus
        if task.task_type in substrate.cognitive_specializations:
            score *= 1.2
            reasoning_parts.append("Specialization bonus: 1.2x")
        
        reasoning = "; ".join(reasoning_parts)
        return score, reasoning

    def _select_optimal_substrate(self, task: CognitiveTask) -> Tuple[str, SubstrateCapabilities, str]:
        """Select the optimal substrate for a cognitive task"""
        best_score = -1.0
        best_substrate_id = None
        best_substrate = None
        best_reasoning = ""
        
        for substrate_id, substrate in self.substrate_pool.items():
            score, reasoning = self._calculate_substrate_score(task, substrate_id, substrate)
            
            if score > best_score:
                best_score = score
                best_substrate_id = substrate_id
                best_substrate = substrate
                best_reasoning = reasoning
        
        if best_substrate_id is None:
            raise ValueError("No suitable substrate found for task")
        
        return best_substrate_id, best_substrate, best_reasoning

    def _allocate_agents(self, task: CognitiveTask, substrate_type: SubstrateOptimization) -> List[str]:
        """Allocate appropriate agents for a cognitive task"""
        # Simulate agent allocation based on task requirements
        agent_ids = []
        
        for i in range(task.collaborative_requirements):
            # Generate agent ID based on substrate type and task requirements
            agent_hash = hashlib.md5(f"{task.task_id}_{substrate_type.value}_{i}".encode()).hexdigest()[:8]
            agent_id = f"agent_{substrate_type.value}_{agent_hash}"
            agent_ids.append(agent_id)
        
        return agent_ids

    def distribute_task(self, task_id: str) -> WorkloadDistributionDecision:
        """Distribute a specific cognitive task to optimal substrate"""
        # Find task in queue
        task = None
        for t in self.task_queue:
            if t.task_id == task_id:
                task = t
                break
        
        if task is None:
            raise ValueError(f"Task {task_id} not found in queue")
        
        # Select optimal substrate
        substrate_id, substrate, reasoning = self._select_optimal_substrate(task)
        
        # Allocate agents
        assigned_agents = self._allocate_agents(task, substrate.substrate_type)
        
        # Calculate estimated completion time
        base_time = task.estimated_compute_units / max(0.1, substrate.available_compute_units)
        complexity_multiplier = float(task.complexity.value)
        estimated_time = base_time * complexity_multiplier * (1.0 + substrate.current_load_percentage / 100.0)
        
        # Select backup substrates
        backup_substrates = [s.substrate_type for s in self.substrate_pool.values() 
                            if s.substrate_type != substrate.substrate_type][:2]
        
        # Create distribution decision
        decision = WorkloadDistributionDecision(
            task_id=task_id,
            assigned_substrate=substrate.substrate_type,
            assigned_agents=assigned_agents,
            estimated_completion_time=estimated_time,
            confidence_score=min(0.95, substrate.reliability_score * 0.9),
            resource_allocation={
                "compute_units": task.estimated_compute_units,
                "memory_gb": task.memory_requirements_gb,
                "bandwidth_mbps": task.bandwidth_requirements_mbps
            },
            backup_substrates=backup_substrates,
            reasoning=reasoning
        )
        
        # Update substrate load
        self.substrate_pool[substrate_id].current_load_percentage += (
            task.estimated_compute_units / substrate.available_compute_units * 100
        )
        self.substrate_pool[substrate_id].available_compute_units -= task.estimated_compute_units
        self.substrate_pool[substrate_id].available_memory_gb -= task.memory_requirements_gb
        
        # Track assignment
        self.active_assignments[task_id] = decision
        
        # Remove from queue
        self.task_queue.remove(task)
        
        self.logger.info(f"Distributed task {task_id} to {substrate.substrate_type.value}")
        return decision

    def rebalance_workload(self) -> List[str]:
        """Rebalance workload across substrates when imbalance detected"""
        rebalanced_tasks = []
        
        # Calculate current load distribution
        substrate_loads = {}
        for substrate_id, substrate in self.substrate_pool.items():
            substrate_loads[substrate_id] = substrate.current_load_percentage
        
        if not substrate_loads:
            return rebalanced_tasks
        
        avg_load = statistics.mean(substrate_loads.values())
        max_load = max(substrate_loads.values())
        
        # If maximum load exceeds redistribution threshold
        if max_load > self.redistribution_threshold * 100:
            # Find overloaded substrates
            overloaded = {k: v for k, v in substrate_loads.items() 
                         if v > avg_load * 1.3}
            
            # Find underutilized substrates  
            underutilized = {k: v for k, v in substrate_loads.items() 
                           if v < avg_load * 0.7}
            
            # Attempt to migrate some tasks
            for overloaded_id in overloaded:
                for task_id, assignment in list(self.active_assignments.items()):
                    if len(rebalanced_tasks) >= 5:  # Limit rebalancing scope
                        break
                        
                    # Find a better substrate for this task
                    task = next((t for t in self.task_history if t.task_id == task_id), None)
                    if task and any(sub_id in underutilized for sub_id in self.substrate_pool):
                        # Attempt redistribution
                        try:
                            new_decision = self.distribute_task(task_id)
                            rebalanced_tasks.append(task_id)
                            self.logger.info(f"Rebalanced task {task_id}")
                        except:
                            continue
        
        return rebalanced_tasks

    def complete_task(self, task_id: str, completion_time: float, success: bool):
        """Mark a task as completed and update learning metrics"""
        if task_id not in self.active_assignments:
            return
        
        assignment = self.active_assignments[task_id]
        actual_time = completion_time
        estimated_time = assignment.estimated_completion_time
        
        # Update substrate efficiency learning
        task = next((t for t in self.task_history if t.task_id == task_id), None)
        if task:
            efficiency_key = (assignment.assigned_substrate, task.task_type)
            if success:
                # Reward accurate predictions
                accuracy_score = min(2.0, estimated_time / max(0.1, actual_time))
                current_score = self.substrate_efficiency_scores.get(efficiency_key, 0.5)
                learning_rate = 0.1
                new_score = current_score * (1 - learning_rate) + accuracy_score * learning_rate
                self.substrate_efficiency_scores[efficiency_key] = min(1.0, new_score)
            else:
                # Penalize failures
                current_score = self.substrate_efficiency_scores.get(efficiency_key, 0.5)
                self.substrate_efficiency_scores[efficiency_key] = max(0.1, current_score * 0.8)
        
        # Free up substrate resources
        substrate_id = None
        for sid, substrate in self.substrate_pool.items():
            if substrate.substrate_type == assignment.assigned_substrate:
                substrate_id = sid
                break
        
        if substrate_id:
            self.substrate_pool[substrate_id].current_load_percentage -= (
                assignment.resource_allocation["compute_units"] / 
                (self.substrate_pool[substrate_id].available_compute_units + 
                 assignment.resource_allocation["compute_units"]) * 100
            )
            self.substrate_pool[substrate_id].available_compute_units += assignment.resource_allocation["compute_units"]
            self.substrate_pool[substrate_id].available_memory_gb += assignment.resource_allocation["memory_gb"]
        
        # Record completion
        self.task_completion_times[task_id] = completion_time
        del self.active_assignments[task_id]
        
        self.logger.info(f"Completed task {task_id} in {completion_time:.2f}s (estimated {estimated_time:.2f}s)")

    def get_distribution_analytics(self) -> Dict[str, Any]:
        """Get comprehensive analytics about cognitive workload distribution"""
        analytics = {
            "substrate_utilization": {},
            "task_type_distribution": defaultdict(int),
            "completion_rates": {},
            "efficiency_scores": {},
            "queue_metrics": {
                "pending_tasks": len(self.task_queue),
                "active_assignments": len(self.active_assignments),
                "completed_tasks": len(self.task_completion_times)
            }
        }
        
        # Substrate utilization
        for substrate_id, substrate in self.substrate_pool.items():
            analytics["substrate_utilization"][substrate_id] = {
                "type": substrate.substrate_type.value,
                "current_load": substrate.current_load_percentage,
                "available_compute": substrate.available_compute_units,
                "available_memory": substrate.available_memory_gb,
                "reliability": substrate.reliability_score
            }
        
        # Task type distribution
        for task in self.task_history:
            analytics["task_type_distribution"][task.task_type.value] += 1
        
        # Efficiency scores
        for (substrate_type, task_type), score in self.substrate_efficiency_scores.items():
            key = f"{substrate_type.value}_{task_type.value}"
            analytics["efficiency_scores"][key] = score
        
        # Completion rates
        if self.task_completion_times:
            analytics["completion_rates"]["average_time"] = statistics.mean(self.task_completion_times.values())
            analytics["completion_rates"]["median_time"] = statistics.median(self.task_completion_times.values())
            analytics["completion_rates"]["total_completed"] = len(self.task_completion_times)
        
        return analytics

    def _start_monitoring(self):
        """Start background monitoring and rebalancing"""
        if self.monitoring_active:
            return
        
        self.monitoring_active = True
        self.distribution_thread = threading.Thread(target=self._monitoring_loop, daemon=True)
        self.distribution_thread.start()
        self.logger.info("Started cognitive workload distribution monitoring")

    def _monitoring_loop(self):
        """Background monitoring loop for automatic rebalancing"""
        while self.monitoring_active:
            try:
                # Process pending tasks
                while self.task_queue:
                    task = self.task_queue[0]
                    try:
                        self.distribute_task(task.task_id)
                    except Exception as e:
                        self.logger.error(f"Failed to distribute task {task.task_id}: {e}")
                        # Remove problematic task to prevent infinite loop
                        self.task_queue.popleft()
                
                # Check for rebalancing needs
                rebalanced = self.rebalance_workload()
                if rebalanced:
                    self.logger.info(f"Rebalanced {len(rebalanced)} tasks")
                
                # Update performance metrics
                self._update_performance_metrics()
                
                # Sleep before next check
                time.sleep(5.0)
                
            except Exception as e:
                self.logger.error(f"Error in monitoring loop: {e}")
                time.sleep(10.0)

    def _update_performance_metrics(self):
        """Update real-time performance metrics"""
        current_time = time.time()
        
        # Track substrate utilization over time
        for substrate_id, substrate in self.substrate_pool.items():
            metric_key = f"utilization_{substrate_id}"
            self.performance_metrics[metric_key].append({
                "timestamp": current_time,
                "utilization": substrate.current_load_percentage,
                "available_compute": substrate.available_compute_units
            })
        
        # Track queue depth
        self.performance_metrics["queue_depth"].append({
            "timestamp": current_time,
            "pending": len(self.task_queue),
            "active": len(self.active_assignments)
        })

    def shutdown(self):
        """Shutdown the workload distributor"""
        self.monitoring_active = False
        if self.distribution_thread:
            self.distribution_thread.join(timeout=5.0)
        self.logger.info("Cognitive workload distributor shut down")


# Example usage and testing
if __name__ == "__main__":
    import random
    
    logging.basicConfig(level=logging.INFO)
    distributor = CognitiveWorkloadDistributor()
    
    # Register some example substrates
    quantum_substrate = SubstrateCapabilities(
        substrate_type=SubstrateOptimization.QUANTUM_COHERENT,
        available_compute_units=1000.0,
        available_memory_gb=64.0,
        available_bandwidth_mbps=1000.0,
        latency_ms=50.0,
        reliability_score=0.95,
        current_load_percentage=20.0,
        cognitive_specializations=[CognitiveTaskType.ABSTRACT_THINKING, CognitiveTaskType.LOGICAL_REASONING],
        power_efficiency=0.8,
        cost_per_compute_unit=0.1,
        maintenance_window_hours=[2, 3, 4]
    )
    
    gpu_substrate = SubstrateCapabilities(
        substrate_type=SubstrateOptimization.GPU_PARALLEL,
        available_compute_units=2000.0,
        available_memory_gb=32.0,
        available_bandwidth_mbps=2000.0,
        latency_ms=20.0,
        reliability_score=0.9,
        current_load_percentage=35.0,
        cognitive_specializations=[CognitiveTaskType.PATTERN_RECOGNITION, CognitiveTaskType.MATHEMATICAL_COMPUTATION],
        power_efficiency=0.6,
        cost_per_compute_unit=0.05,
        maintenance_window_hours=[1, 2]
    )
    
    distributor.register_substrate("quantum_1", quantum_substrate)
    distributor.register_substrate("gpu_1", gpu_substrate)
    
    # Submit some test tasks
    test_tasks = [
        CognitiveTask(
            task_id="abstract_reasoning_001",
            task_type=CognitiveTaskType.ABSTRACT_THINKING,
            complexity=CognitiveComplexity.HIGHLY_COMPLEX,
            estimated_compute_units=500.0,
            memory_requirements_gb=16.0,
            bandwidth_requirements_mbps=100.0,
            real_time_constraints=False,
            deadline_seconds=300.0,
            dependencies=[],
            preferred_substrates=[SubstrateOptimization.QUANTUM_COHERENT],
            collaborative_requirements=2,
            privacy_level=3,
            fault_tolerance_required=True,
            created_at=time.time(),
            priority_weight=0.8
        ),
        
        CognitiveTask(
            task_id="pattern_match_001", 
            task_type=CognitiveTaskType.PATTERN_RECOGNITION,
            complexity=CognitiveComplexity.MODERATE,
            estimated_compute_units=800.0,
            memory_requirements_gb=8.0,
            bandwidth_requirements_mbps=200.0,
            real_time_constraints=True,
            deadline_seconds=60.0,
            dependencies=[],
            preferred_substrates=[SubstrateOptimization.GPU_PARALLEL],
            collaborative_requirements=1,
            privacy_level=2,
            fault_tolerance_required=False,
            created_at=time.time(),
            priority_weight=0.9
        )
    ]
    
    # Submit and distribute tasks
    for task in test_tasks:
        distributor.submit_cognitive_task(task)
    
    # Wait for distribution
    time.sleep(2.0)
    
    # Get analytics
    analytics = distributor.get_distribution_analytics()
    print("\nCognitive Workload Distribution Analytics:")
    print(json.dumps(analytics, indent=2, default=str))
    
    # Simulate task completion
    for task_id in list(distributor.active_assignments.keys()):
        completion_time = random.uniform(30.0, 120.0)
        success = random.choice([True, True, True, False])  # 75% success rate
        distributor.complete_task(task_id, completion_time, success)
    
    # Get updated analytics
    final_analytics = distributor.get_distribution_analytics()
    print("\nFinal Analytics:")
    print(json.dumps(final_analytics, indent=2, default=str))
    
    # Shutdown
    distributor.shutdown()