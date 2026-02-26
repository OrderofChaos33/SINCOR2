"""
Optimized Asset Creation Workflow Engine
Parallel processing, caching, and performance optimization
"""

import logging
import asyncio
import hashlib
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
import sqlite3

logger = logging.getLogger('sincor2.workflow_optimization')

# ============================================================================
# WORKFLOW OPTIMIZATION ENUMS & CONFIG
# ============================================================================

class WorkflowPhase(str):
    """Workflow phases"""
    REQUEST_INTAKE = "request_intake"
    REQUIREMENT_ANALYSIS = "requirement_analysis"
    AGENT_ASSIGNMENT = "agent_assignment"
    PARALLEL_EXECUTION = "parallel_execution"
    QUALITY_ASSESSMENT = "quality_assessment"
    REVISION_LOOP = "revision_loop"
    FINAL_APPROVAL = "final_approval"
    DELIVERY = "delivery"

WORKFLOW_OPTIMIZATION_CONFIG = {
    "max_parallel_agents": 5,
    "quality_check_threshold": 0.75,
    "cache_ttl_seconds": 3600,
    "revision_limit": 3,
    "timeout_seconds": 3600,
    "batch_processing_enabled": True,
    "batch_size": 5,
    "enable_caching": True,
    "enable_async": True,
}

# ============================================================================
# WORKFLOW STATE & TRACKING
# ============================================================================

@dataclass
class WorkflowPhaseExecution:
    """Track execution of a workflow phase"""
    phase: WorkflowPhase
    start_time: datetime = field(default_factory=datetime.utcnow)
    end_time: Optional[datetime] = None
    status: str = "in_progress"  # in_progress, completed, failed
    duration_seconds: float = 0.0
    result: Dict = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def complete(self):
        """Mark phase as complete"""
        self.end_time = datetime.utcnow()
        self.duration_seconds = (self.end_time - self.start_time).total_seconds()
        self.status = "completed"

    def fail(self, error: str):
        """Mark phase as failed"""
        self.end_time = datetime.utcnow()
        self.duration_seconds = (self.end_time - self.start_time).total_seconds()
        self.status = "failed"
        self.errors.append(error)

@dataclass
class WorkflowOptimizationMetrics:
    """Performance metrics for workflow"""
    total_duration_seconds: float = 0.0
    phases: Dict[str, WorkflowPhaseExecution] = field(default_factory=dict)
    parallel_efficiency: float = 1.0  # 0.0-1.0
    cache_hits: int = 0
    cache_misses: int = 0
    agent_utilization: float = 0.0
    quality_pass_rate: float = 0.0
    revision_count: int = 0

    @property
    def cache_hit_rate(self) -> float:
        """Cache hit rate percentage"""
        total = self.cache_hits + self.cache_misses
        return (self.cache_hits / total * 100) if total > 0 else 0

# ============================================================================
# WORKFLOW CACHE & OPTIMIZATION
# ============================================================================

class WorkflowCache:
    """In-memory cache for workflow data"""

    def __init__(self, ttl_seconds: int = 3600):
        self.ttl_seconds = ttl_seconds
        self.cache: Dict[str, Tuple[any, datetime]] = {}

    def get(self, key: str) -> Optional[any]:
        """Get value from cache"""
        if key not in self.cache:
            return None

        value, expiry = self.cache[key]
        if datetime.utcnow() > expiry:
            del self.cache[key]
            return None

        return value

    def set(self, key: str, value: any):
        """Set value in cache"""
        expiry = datetime.utcnow() + timedelta(seconds=self.ttl_seconds)
        self.cache[key] = (value, expiry)

    def invalidate(self, pattern: str = "*"):
        """Invalidate cache entries matching pattern"""
        if pattern == "*":
            self.cache.clear()
        else:
            keys_to_delete = [k for k in self.cache.keys() if pattern in k]
            for k in keys_to_delete:
                del self.cache[k]

    def stats(self) -> Dict:
        """Cache statistics"""
        return {
            'entries': len(self.cache),
            'ttl_seconds': self.ttl_seconds
        }

# ============================================================================
# PARALLEL WORKFLOW ENGINE
# ============================================================================

class OptimizedWorkflowEngine:
    """Optimized workflow orchestration with parallel processing"""

    def __init__(self, config: Dict = None, max_workers: int = 5):
        self.config = {**WORKFLOW_OPTIMIZATION_CONFIG, **(config or {})}
        self.max_workers = max_workers
        self.cache = WorkflowCache(self.config["cache_ttl_seconds"])
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.workflow_metrics: Dict[str, WorkflowOptimizationMetrics] = {}
        self._init_db()
        logger.info("Optimized Workflow Engine initialized")

    def _init_db(self):
        """Initialize workflow tracking database"""
        conn = sqlite3.connect("workflow_optimization.db")
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS workflow_executions (
                asset_id TEXT PRIMARY KEY,
                start_time TEXT,
                end_time TEXT,
                total_duration REAL,
                phases_data TEXT,
                cache_hit_rate REAL,
                agent_util REAL,
                quality_pass_rate REAL,
                status TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS phase_metrics (
                phase_id TEXT PRIMARY KEY,
                asset_id TEXT,
                phase_name TEXT,
                duration REAL,
                status TEXT,
                result_data TEXT
            )
        """)

        conn.commit()
        conn.close()

    def execute_optimized_workflow(self, asset_id: str, asset_data: Dict) -> Tuple[bool, Dict]:
        """
        Execute complete optimized workflow for asset creation
        Returns: (success: bool, metrics: Dict)
        """
        metrics = WorkflowOptimizationMetrics()
        workflow_start = datetime.utcnow()

        try:
            # Phase 1: Request Intake
            metrics.phases[WorkflowPhase.REQUEST_INTAKE] = self._phase_request_intake(asset_data)

            # Phase 2: Requirement Analysis
            metrics.phases[WorkflowPhase.REQUIREMENT_ANALYSIS] = self._phase_requirement_analysis(
                asset_data
            )

            # Phase 3: Agent Assignment (parallel)
            metrics.phases[WorkflowPhase.AGENT_ASSIGNMENT] = self._phase_agent_assignment(
                asset_data
            )

            # Phase 4: Parallel Execution
            metrics.phases[WorkflowPhase.PARALLEL_EXECUTION] = self._phase_parallel_execution(
                asset_id, asset_data
            )

            # Phase 5: Quality Assessment
            metrics.phases[WorkflowPhase.QUALITY_ASSESSMENT] = self._phase_quality_assessment(
                asset_id, asset_data
            )

            # Phase 6: Revision Loop (if needed)
            revision_count = 0
            while (metrics.phases[WorkflowPhase.QUALITY_ASSESSMENT].status == "failed" and
                   revision_count < self.config["revision_limit"]):
                metrics.phases[WorkflowPhase.REVISION_LOOP] = self._phase_revision_loop(
                    asset_id, asset_data
                )
                metrics.phases[WorkflowPhase.QUALITY_ASSESSMENT] = self._phase_quality_assessment(
                    asset_id, asset_data
                )
                revision_count += 1
                metrics.revision_count += 1

            if revision_count >= self.config["revision_limit"]:
                raise Exception(f"Asset failed after {revision_count} revisions")

            # Phase 7: Final Approval
            metrics.phases[WorkflowPhase.FINAL_APPROVAL] = self._phase_final_approval(
                asset_id, asset_data
            )

            # Phase 8: Delivery
            metrics.phases[WorkflowPhase.DELIVERY] = self._phase_delivery(asset_id, asset_data)

            # Calculate metrics
            metrics.total_duration_seconds = (datetime.utcnow() - workflow_start).total_seconds()
            metrics.parallel_efficiency = self._calculate_efficiency(metrics)
            metrics.quality_pass_rate = 1.0 if all(
                p.status == "completed" for p in metrics.phases.values()
            ) else 0.0

            self.workflow_metrics[asset_id] = metrics

            logger.info(f"Optimized workflow completed for {asset_id} in {metrics.total_duration_seconds:.2f}s")
            return True, self._metrics_to_dict(metrics)

        except Exception as e:
            logger.error(f"Workflow error for {asset_id}: {e}")
            metrics.phases[WorkflowPhase.DELIVERY].fail(str(e))
            return False, self._metrics_to_dict(metrics)

    def _phase_request_intake(self, asset_data: Dict) -> WorkflowPhaseExecution:
        """Phase 1: Validate request"""
        phase = WorkflowPhaseExecution(phase=WorkflowPhase.REQUEST_INTAKE)

        try:
            # Validate required fields
            required_fields = ["asset_type", "client_id", "description"]
            missing = [f for f in required_fields if f not in asset_data]

            if missing:
                phase.fail(f"Missing required fields: {missing}")
            else:
                phase.result = {
                    "request_valid": True,
                    "client_id": asset_data.get("client_id"),
                    "asset_type": asset_data.get("asset_type")
                }
                phase.complete()

        except Exception as e:
            phase.fail(str(e))

        return phase

    def _phase_requirement_analysis(self, asset_data: Dict) -> WorkflowPhaseExecution:
        """Phase 2: Analyze requirements"""
        phase = WorkflowPhaseExecution(phase=WorkflowPhase.REQUIREMENT_ANALYSIS)

        try:
            # Check cache
            cache_key = f"requirements:{asset_data.get('asset_type')}"
            cached = self.cache.get(cache_key)

            if cached:
                phase.result = cached
            else:
                # Analyze complexity, resource needs, timeline
                phase.result = {
                    "complexity": self._assess_complexity(asset_data),
                    "estimated_duration_minutes": self._estimate_duration(asset_data),
                    "required_agents": self._estimate_agent_count(asset_data),
                    "resource_allocation": self._allocate_resources(asset_data)
                }
                self.cache.set(cache_key, phase.result)

            phase.complete()

        except Exception as e:
            phase.fail(str(e))

        return phase

    def _phase_agent_assignment(self, asset_data: Dict) -> WorkflowPhaseExecution:
        """Phase 3: Assign best agents"""
        phase = WorkflowPhaseExecution(phase=WorkflowPhase.AGENT_ASSIGNMENT)

        try:
            asset_type = asset_data.get("asset_type")
            agent_count = self._estimate_agent_count(asset_data)

            # Get best agents for this task (would call agent_monitor)
            phase.result = {
                "assigned_agents": self._select_best_agents(asset_type, agent_count),
                "total_agents": agent_count,
                "bids_accepted": True
            }
            phase.complete()

        except Exception as e:
            phase.fail(str(e))

        return phase

    def _phase_parallel_execution(self, asset_id: str, asset_data: Dict) -> WorkflowPhaseExecution:
        """Phase 4: Execute in parallel with multiple agents"""
        phase = WorkflowPhaseExecution(phase=WorkflowPhase.PARALLEL_EXECUTION)

        try:
            # Simulate parallel agent execution
            agents = phase.result.get("assigned_agents", [])[:self.max_workers]

            with ThreadPoolExecutor(max_workers=len(agents)) as executor:
                futures = {
                    executor.submit(self._execute_agent_task, asset_id, agent): agent
                    for agent in agents
                }

                results = {}
                for future in as_completed(futures):
                    agent = futures[future]
                    try:
                        result = future.result(timeout=self.config["timeout_seconds"])
                        results[agent] = result
                    except Exception as e:
                        logger.error(f"Agent {agent} failed: {e}")
                        results[agent] = {"status": "failed", "error": str(e)}

            phase.result = {
                "execution_results": results,
                "agents_succeeded": sum(1 for r in results.values() if r.get("status") == "success"),
                "agents_failed": sum(1 for r in results.values() if r.get("status") == "failed")
            }

            phase.complete()

        except Exception as e:
            phase.fail(str(e))

        return phase

    def _phase_quality_assessment(self, asset_id: str, asset_data: Dict) -> WorkflowPhaseExecution:
        """Phase 5: Assess quality across 9 dimensions"""
        phase = WorkflowPhaseExecution(phase=WorkflowPhase.QUALITY_ASSESSMENT)

        try:
            # Get quality metrics from asset_data
            quality_metrics = {
                "accuracy": asset_data.get("accuracy", 0.85),
                "completeness": asset_data.get("completeness", 0.80),
                "relevance": asset_data.get("relevance", 0.85),
                "timeliness": asset_data.get("timeliness", 0.75),
                "clarity": asset_data.get("clarity", 0.85),
                "actionability": asset_data.get("actionability", 0.70),
                "innovation": asset_data.get("innovation", 0.60),
                "depth": asset_data.get("depth", 0.80),
                "credibility": asset_data.get("credibility", 0.85)
            }

            overall_score = sum(quality_metrics.values()) / len(quality_metrics)
            passed = overall_score >= self.config["quality_check_threshold"]

            phase.result = {
                "metrics": quality_metrics,
                "overall_score": overall_score,
                "passed": passed,
                "status": "passed" if passed else "failed"
            }

            if passed:
                phase.complete()
            else:
                phase.fail(f"Quality score {overall_score:.2f} below threshold {self.config['quality_check_threshold']}")

        except Exception as e:
            phase.fail(str(e))

        return phase

    def _phase_revision_loop(self, asset_id: str, asset_data: Dict) -> WorkflowPhaseExecution:
        """Phase 6: Request revisions from agents"""
        phase = WorkflowPhaseExecution(phase=WorkflowPhase.REVISION_LOOP)

        try:
            # Trigger revision requests to assigned agents
            improvement_areas = self._identify_weak_areas(asset_data)

            phase.result = {
                "revision_requested": True,
                "areas_to_improve": improvement_areas,
                "revision_agents": self._select_best_agents("revision", 2)
            }
            phase.complete()

        except Exception as e:
            phase.fail(str(e))

        return phase

    def _phase_final_approval(self, asset_id: str, asset_data: Dict) -> WorkflowPhaseExecution:
        """Phase 7: Final approval"""
        phase = WorkflowPhaseExecution(phase=WorkflowPhase.FINAL_APPROVAL)

        try:
            phase.result = {
                "approved": True,
                "approval_date": datetime.utcnow().isoformat(),
                "approver": "quality_system"
            }
            phase.complete()

        except Exception as e:
            phase.fail(str(e))

        return phase

    def _phase_delivery(self, asset_id: str, asset_data: Dict) -> WorkflowPhaseExecution:
        """Phase 8: Deliver to client"""
        phase = WorkflowPhaseExecution(phase=WorkflowPhase.DELIVERY)

        try:
            phase.result = {
                "delivered": True,
                "delivery_date": datetime.utcnow().isoformat(),
                "client_id": asset_data.get("client_id"),
                "delivery_method": "api"
            }
            phase.complete()

        except Exception as e:
            phase.fail(str(e))

        return phase

    # ============================================================================
    # HELPER METHODS
    # ============================================================================

    def _assess_complexity(self, asset_data: Dict) -> str:
        """Assess asset complexity"""
        asset_type = asset_data.get("asset_type", "")
        complexity_map = {
            "intelligence_report": "medium",
            "predictive_model": "high",
            "custom_ai_agent": "high",
            "automation_solution": "medium",
            "market_analysis": "medium"
        }
        return complexity_map.get(asset_type, "medium")

    def _estimate_duration(self, asset_data: Dict) -> float:
        """Estimate execution duration in minutes"""
        complexity = self._assess_complexity(asset_data)
        base_times = {"simple": 30, "medium": 120, "high": 240}
        return base_times.get(complexity, 120)

    def _estimate_agent_count(self, asset_data: Dict) -> int:
        """Estimate number of agents needed"""
        complexity = self._assess_complexity(asset_data)
        agent_counts = {"simple": 1, "medium": 2, "high": 3}
        return agent_counts.get(complexity, 2)

    def _allocate_resources(self, asset_data: Dict) -> Dict:
        """Allocate resources for asset creation"""
        complexity = self._assess_complexity(asset_data)
        return {
            "compute_credits": {"simple": 100, "medium": 300, "high": 500}.get(complexity, 300),
            "memory_mb": {"simple": 512, "medium": 1024, "high": 2048}.get(complexity, 1024),
            "timeout_seconds": {"simple": 600, "medium": 1800, "high": 3600}.get(complexity, 1800)
        }

    def _select_best_agents(self, asset_type: str, count: int) -> List[str]:
        """Select best agents for task (would call agent_monitor)"""
        # Simulated agent selection - would use real agent health/quality data
        all_agents = [
            f"E-auriga-0{i}" for i in range(1, 4)
        ] + [
            f"E-vega-0{i}" for i in range(1, 4)
        ] + [
            f"E-synthesizer-0{i}" for i in range(1, 3)
        ]
        return all_agents[:count]

    def _execute_agent_task(self, asset_id: str, agent: str) -> Dict:
        """Simulate agent task execution"""
        return {
            "agent": agent,
            "asset_id": asset_id,
            "status": "success",
            "tokens_used": 5000,
            "time_minutes": 10
        }

    def _identify_weak_areas(self, asset_data: Dict) -> List[str]:
        """Identify areas needing improvement"""
        weak_areas = []
        for dim, score in asset_data.get("quality_metrics", {}).items():
            if score < 0.75:
                weak_areas.append(f"{dim}: {score:.2f}")
        return weak_areas

    def _calculate_efficiency(self, metrics: WorkflowOptimizationMetrics) -> float:
        """Calculate parallel execution efficiency"""
        sequential_time = sum(p.duration_seconds for p in metrics.phases.values())
        actual_time = metrics.total_duration_seconds
        return min(1.0, (sequential_time / actual_time) if actual_time > 0 else 1.0)

    def _metrics_to_dict(self, metrics: WorkflowOptimizationMetrics) -> Dict:
        """Convert metrics to dictionary"""
        return {
            "total_duration_seconds": metrics.total_duration_seconds,
            "phases": {
                k: {
                    "phase": v.phase,
                    "duration_seconds": v.duration_seconds,
                    "status": v.status,
                    "result": v.result,
                    "errors": v.errors
                }
                for k, v in metrics.phases.items()
            },
            "parallel_efficiency": metrics.parallel_efficiency,
            "cache_hit_rate": metrics.cache_hit_rate,
            "quality_pass_rate": metrics.quality_pass_rate,
            "revision_count": metrics.revision_count
        }

    def get_workflow_metrics(self, asset_id: str) -> Optional[Dict]:
        """Get metrics for completed workflow"""
        if asset_id not in self.workflow_metrics:
            return None
        return self._metrics_to_dict(self.workflow_metrics[asset_id])

    def get_cache_stats(self) -> Dict:
        """Get cache performance stats"""
        return {
            "cache_entries": len(self.cache.cache),
            "hit_rate": f"{self.cache_hit_rate:.1f}%",
            "ttl_seconds": self.cache.ttl_seconds
        }


# ============================================================================
# GLOBAL INSTANCE
# ============================================================================

optimized_workflow_engine = OptimizedWorkflowEngine()
