#!/usr/bin/env python3
"""
SINCOR Agency Kernel

Core reasoning engine implementing the Planner/Executor/Critic/Archivist pattern:
- Planner: Decomposes goals into actionable steps
- Executor: Handles tool use and action execution  
- Critic: Validates results, checks quality, provides citations
- Archivist: Manages memory writes and knowledge consolidation

Features:
- Local goals only (no central micromanaging)
- Self-evaluation with evidence→claim→confidence chains
- Continuity Index tracking for drift detection
"""

import json
import os
from typing import Dict, List, Optional, Any, Union
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from enum import Enum
import uuid

class TaskStatus(Enum):
    """Task execution status"""
    PENDING = "pending"
    PLANNING = "planning"
    EXECUTING = "executing"
    VALIDATING = "validating"
    ARCHIVING = "archiving"
    COMPLETED = "completed"
    FAILED = "failed"

class ConfidenceLevel(Enum):
    """Confidence levels for self-evaluation"""
    VERY_LOW = 0.2
    LOW = 0.4
    MEDIUM = 0.6
    HIGH = 0.8
    VERY_HIGH = 0.95

@dataclass
class TaskGoal:
    """Local goal definition"""
    goal_id: str
    description: str
    context: Dict[str, Any]
    priority: float  # 0.0-1.0
    deadline: Optional[str]
    success_criteria: List[str]
    assigned_agent: str
    
@dataclass
class PlanStep:
    """Individual step in execution plan"""
    step_id: str
    action_type: str  # tool_call, analysis, communication, etc.
    description: str
    inputs: Dict[str, Any]
    expected_outputs: List[str]
    dependencies: List[str]  # Other step IDs
    tools_required: List[str]
    confidence_estimate: float

@dataclass
class ExecutionPlan:
    """Complete execution plan for a goal"""
    plan_id: str
    goal_id: str
    steps: List[PlanStep]
    estimated_effort: float  # In tool calls or time units
    risk_assessment: Dict[str, float]
    created: str
    planner_notes: str

@dataclass
class ExecutionResult:
    """Result of executing a plan step"""
    step_id: str
    plan_id: str
    status: str  # success, failure, partial
    outputs: Dict[str, Any]
    evidence: List[str]  # Supporting evidence
    citations: List[str]  # Sources and references
    confidence: float
    execution_time: float
    resource_usage: Dict[str, int]  # tool_calls, tokens, etc.
    errors: List[str]

@dataclass
class CriticEvaluation:
    """Critic's evaluation of results"""
    evaluation_id: str
    target_result_id: str
    quality_score: float  # 0.0-1.0
    accuracy_assessment: float  # 0.0-1.0
    completeness_score: float  # 0.0-1.0
    citation_quality: float  # 0.0-1.0
    issues_found: List[str]
    recommendations: List[str]
    overall_confidence: float
    needs_revision: bool

@dataclass
class SelfEvaluation:
    """Self-evaluation with evidence→claim→confidence chain"""
    eval_id: str
    goal_id: str
    evidence: List[str]  # Observed facts and data
    claims: List[str]    # Conclusions drawn from evidence  
    confidence: float    # Overall confidence in claims
    reasoning_chain: List[str]  # Step-by-step reasoning
    uncertainty_factors: List[str]  # What could be wrong
    timestamp: str

class AgencyKernel:
    """Core reasoning engine for SINCOR agents"""
    
    def __init__(self, agent_id: str, archetype: str, memory_system, persona_engine, 
                 kernel_dir: str = "kernels"):
        self.agent_id = agent_id
        self.archetype = archetype
        self.memory_system = memory_system
        self.persona_engine = persona_engine
        
        # Initialize kernel storage
        os.makedirs(kernel_dir, exist_ok=True)
        self.kernel_dir = kernel_dir
        self.goals_file = f"{kernel_dir}/{agent_id}_goals.json"
        self.plans_file = f"{kernel_dir}/{agent_id}_plans.json"
        self.results_file = f"{kernel_dir}/{agent_id}_results.jsonl"
        self.evaluations_file = f"{kernel_dir}/{agent_id}_evaluations.jsonl"
        
        # Active state
        self.current_goals = self._load_goals()
        self.active_plans = self._load_plans()
        
        # Performance tracking
        self.execution_stats = {
            "goals_completed": 0,
            "plans_created": 0,
            "steps_executed": 0,
            "average_confidence": 0.0,
            "success_rate": 0.0
        }
    
    def _load_goals(self) -> Dict[str, TaskGoal]:
        """Load current goals from storage"""
        if not os.path.exists(self.goals_file):
            return {}
            
        with open(self.goals_file, 'r') as f:
            data = json.load(f)
            
        goals = {}
        for goal_id, goal_data in data.items():
            goals[goal_id] = TaskGoal(**goal_data)
        return goals
    
    def _save_goals(self):
        """Save current goals to storage"""
        data = {}
        for goal_id, goal in self.current_goals.items():
            data[goal_id] = asdict(goal)
            
        with open(self.goals_file, 'w') as f:
            json.dump(data, f, indent=2)
    
    def _load_plans(self) -> Dict[str, ExecutionPlan]:
        """Load execution plans from storage"""
        if not os.path.exists(self.plans_file):
            return {}
            
        with open(self.plans_file, 'r') as f:
            data = json.load(f)
            
        plans = {}
        for plan_id, plan_data in data.items():
            # Reconstruct steps
            steps = [PlanStep(**step_data) for step_data in plan_data['steps']]
            plan_data['steps'] = steps
            plans[plan_id] = ExecutionPlan(**plan_data)
        return plans
    
    def _save_plans(self):
        """Save execution plans to storage"""
        data = {}
        for plan_id, plan in self.active_plans.items():
            plan_dict = asdict(plan)
            data[plan_id] = plan_dict
            
        with open(self.plans_file, 'w') as f:
            json.dump(data, f, indent=2)
    
    # PLANNER COMPONENT
    
    def planner_accept_goal(self, goal: TaskGoal) -> str:
        """Planner accepts and stores a new goal"""
        
        # Record goal acceptance in memory
        self.memory_system.record_episode(
            event_type="goal_accepted",
            content={
                "goal_id": goal.goal_id,
                "description": goal.description,
                "priority": goal.priority
            },
            context={"archetype": self.archetype},
            confidence=0.9
        )
        
        # Store goal
        self.current_goals[goal.goal_id] = goal
        self._save_goals()
        
        return goal.goal_id
    
    def planner_decompose_goal(self, goal_id: str) -> Optional[ExecutionPlan]:
        """Planner decomposes goal into actionable steps"""
        
        if goal_id not in self.current_goals:
            return None
            
        goal = self.current_goals[goal_id]
        
        # Get persona preferences for planning style
        prefs = self.persona_engine.get_behavioral_preferences()
        risk_tolerance = prefs["decision_making"]["risk_tolerance"]
        deliberation_level = prefs["decision_making"]["deliberation_level"]
        
        # Create execution plan based on archetype and goal
        plan = self._create_archetype_plan(goal, risk_tolerance, deliberation_level)
        
        # Store plan
        self.active_plans[plan.plan_id] = plan
        self._save_plans()
        
        # Record planning activity
        self.memory_system.record_episode(
            event_type="plan_created",
            content={
                "goal_id": goal_id,
                "plan_id": plan.plan_id,
                "steps_count": len(plan.steps),
                "estimated_effort": plan.estimated_effort
            },
            confidence=0.8
        )
        
        self.execution_stats["plans_created"] += 1
        return plan
    
    def _create_archetype_plan(self, goal: TaskGoal, risk_tolerance: float, 
                              deliberation_level: float) -> ExecutionPlan:
        """Create execution plan tailored to agent archetype"""
        
        plan_id = f"plan_{uuid.uuid4().hex[:8]}"
        steps = []
        
        # Archetype-specific planning patterns
        if self.archetype == "Scout":
            steps = self._create_scout_plan_steps(goal, risk_tolerance)
        elif self.archetype == "Synthesizer":
            steps = self._create_synthesizer_plan_steps(goal, deliberation_level)
        elif self.archetype == "Builder":
            steps = self._create_builder_plan_steps(goal, deliberation_level)
        elif self.archetype == "Negotiator":
            steps = self._create_negotiator_plan_steps(goal, risk_tolerance)
        elif self.archetype == "Caretaker":
            steps = self._create_caretaker_plan_steps(goal, deliberation_level)
        elif self.archetype == "Auditor":
            steps = self._create_auditor_plan_steps(goal, deliberation_level)
        elif self.archetype == "Director":
            steps = self._create_director_plan_steps(goal, risk_tolerance)
        else:
            steps = self._create_generic_plan_steps(goal)
            
        # Estimate effort and assess risks
        estimated_effort = sum(0.5 + (1.0 - step.confidence_estimate) for step in steps)
        risk_assessment = self._assess_plan_risks(steps, risk_tolerance)
        
        return ExecutionPlan(
            plan_id=plan_id,
            goal_id=goal.goal_id,
            steps=steps,
            estimated_effort=estimated_effort,
            risk_assessment=risk_assessment,
            created=datetime.now().isoformat(),
            planner_notes=f"Plan created for {self.archetype} with {len(steps)} steps"
        )
    
    def _create_scout_plan_steps(self, goal: TaskGoal, risk_tolerance: float) -> List[PlanStep]:
        """Create Scout-specific planning steps"""
        return [
            PlanStep(
                step_id="scout_1",
                action_type="search",
                description="Search for relevant information sources",
                inputs={"query": goal.description, "scope": "broad"},
                expected_outputs=["source_list", "initial_data"],
                dependencies=[],
                tools_required=["web_search", "data_scraping"],
                confidence_estimate=0.8 + risk_tolerance * 0.2
            ),
            PlanStep(
                step_id="scout_2", 
                action_type="validation",
                description="Validate and cross-reference sources",
                inputs={"sources": "scout_1.source_list"},
                expected_outputs=["validated_sources", "confidence_scores"],
                dependencies=["scout_1"],
                tools_required=["validation", "cross_reference"],
                confidence_estimate=0.7 + risk_tolerance * 0.1
            ),
            PlanStep(
                step_id="scout_3",
                action_type="analysis",
                description="Analyze and summarize findings",
                inputs={"data": "scout_2.validated_sources"},
                expected_outputs=["summary_report", "actionable_insights"],
                dependencies=["scout_2"],
                tools_required=["analysis", "summarization"],
                confidence_estimate=0.75
            )
        ]
    
    def _create_synthesizer_plan_steps(self, goal: TaskGoal, deliberation: float) -> List[PlanStep]:
        """Create Synthesizer-specific planning steps"""
        return [
            PlanStep(
                step_id="synth_1",
                action_type="gather",
                description="Gather all relevant information",
                inputs={"context": goal.context},
                expected_outputs=["raw_data", "source_metadata"],
                dependencies=[],
                tools_required=["data_gathering", "source_tracking"],
                confidence_estimate=0.85
            ),
            PlanStep(
                step_id="synth_2",
                action_type="deconflict",
                description="Resolve conflicting information",
                inputs={"data": "synth_1.raw_data"},
                expected_outputs=["reconciled_facts", "conflict_report"],
                dependencies=["synth_1"],
                tools_required=["deconfliction", "fact_checking"],
                confidence_estimate=0.7 + deliberation * 0.2
            ),
            PlanStep(
                step_id="synth_3",
                action_type="synthesize",
                description="Create comprehensive synthesis",
                inputs={"facts": "synth_2.reconciled_facts"},
                expected_outputs=["synthesis_document", "citations"],
                dependencies=["synth_2"],
                tools_required=["synthesis", "citation_management"],
                confidence_estimate=0.8 + deliberation * 0.1
            )
        ]
    
    def _create_generic_plan_steps(self, goal: TaskGoal) -> List[PlanStep]:
        """Create generic planning steps"""
        return [
            PlanStep(
                step_id="generic_1",
                action_type="analyze",
                description="Analyze goal requirements",
                inputs={"goal": goal.description},
                expected_outputs=["requirements", "constraints"],
                dependencies=[],
                tools_required=["analysis"],
                confidence_estimate=0.7
            ),
            PlanStep(
                step_id="generic_2",
                action_type="execute",
                description="Execute primary action",
                inputs={"requirements": "generic_1.requirements"},
                expected_outputs=["results"],
                dependencies=["generic_1"],
                tools_required=["execution"],
                confidence_estimate=0.6
            )
        ]
    
    def _create_builder_plan_steps(self, goal: TaskGoal, deliberation: float) -> List[PlanStep]:
        """Create Builder-specific steps"""
        return [
            PlanStep("build_1", "design", "Design system architecture", {}, ["architecture"], [], ["design_tools"], 0.8),
            PlanStep("build_2", "implement", "Implement solution", {}, ["code"], ["build_1"], ["dev_tools"], 0.7),
            PlanStep("build_3", "test", "Test implementation", {}, ["test_results"], ["build_2"], ["testing_tools"], 0.75)
        ]
    
    def _create_negotiator_plan_steps(self, goal: TaskGoal, risk_tolerance: float) -> List[PlanStep]:
        """Create Negotiator-specific steps"""
        return [
            PlanStep("neg_1", "research", "Research counterpart", {}, ["profile"], [], ["research_tools"], 0.8),
            PlanStep("neg_2", "prepare", "Prepare negotiation strategy", {}, ["strategy"], ["neg_1"], ["planning_tools"], 0.7 + risk_tolerance * 0.2),
            PlanStep("neg_3", "negotiate", "Conduct negotiation", {}, ["agreement"], ["neg_2"], ["communication_tools"], 0.6 + risk_tolerance * 0.1)
        ]
    
    def _create_caretaker_plan_steps(self, goal: TaskGoal, deliberation: float) -> List[PlanStep]:
        """Create Caretaker-specific steps"""
        return [
            PlanStep("care_1", "audit", "Audit current state", {}, ["audit_report"], [], ["audit_tools"], 0.9),
            PlanStep("care_2", "clean", "Clean and organize", {}, ["clean_data"], ["care_1"], ["cleaning_tools"], 0.8),
            PlanStep("care_3", "maintain", "Apply maintenance", {}, ["updated_system"], ["care_2"], ["maintenance_tools"], 0.85)
        ]
    
    def _create_auditor_plan_steps(self, goal: TaskGoal, deliberation: float) -> List[PlanStep]:
        """Create Auditor-specific steps"""
        return [
            PlanStep("audit_1", "scope", "Define audit scope", {}, ["scope_document"], [], ["scoping_tools"], 0.9),
            PlanStep("audit_2", "evaluate", "Conduct evaluation", {}, ["findings"], ["audit_1"], ["evaluation_tools"], 0.85),
            PlanStep("audit_3", "report", "Generate audit report", {}, ["audit_report"], ["audit_2"], ["reporting_tools"], 0.8)
        ]
    
    def _create_director_plan_steps(self, goal: TaskGoal, risk_tolerance: float) -> List[PlanStep]:
        """Create Director-specific steps"""
        return [
            PlanStep("dir_1", "strategize", "Develop strategy", {}, ["strategy"], [], ["strategy_tools"], 0.8),
            PlanStep("dir_2", "coordinate", "Coordinate resources", {}, ["coordination_plan"], ["dir_1"], ["coordination_tools"], 0.75),
            PlanStep("dir_3", "monitor", "Monitor execution", {}, ["status_report"], ["dir_2"], ["monitoring_tools"], 0.8)
        ]
    
    def _assess_plan_risks(self, steps: List[PlanStep], risk_tolerance: float) -> Dict[str, float]:
        """Assess risks in execution plan"""
        
        total_risk = sum(1.0 - step.confidence_estimate for step in steps) / len(steps)
        dependency_risk = sum(len(step.dependencies) for step in steps) / len(steps) * 0.1
        complexity_risk = len(steps) * 0.05
        
        return {
            "total_risk": min(1.0, total_risk),
            "dependency_risk": min(1.0, dependency_risk),
            "complexity_risk": min(1.0, complexity_risk),
            "acceptable": total_risk <= (1.0 - risk_tolerance)
        }
    
    # EXECUTOR COMPONENT
    
    def executor_run_step(self, plan_id: str, step_id: str, 
                         tools_available: Dict[str, Any]) -> ExecutionResult:
        """Executor runs a single plan step"""
        
        if plan_id not in self.active_plans:
            raise ValueError(f"Plan {plan_id} not found")
            
        plan = self.active_plans[plan_id]
        step = next((s for s in plan.steps if s.step_id == step_id), None)
        
        if not step:
            raise ValueError(f"Step {step_id} not found in plan {plan_id}")
        
        start_time = datetime.now()
        
        # Simulate step execution (in real implementation, call actual tools)
        result = self._simulate_step_execution(step, tools_available)
        
        execution_time = (datetime.now() - start_time).total_seconds()
        
        # Create execution result
        exec_result = ExecutionResult(
            step_id=step_id,
            plan_id=plan_id,
            status=result.get("status", "success"),
            outputs=result.get("outputs", {}),
            evidence=result.get("evidence", []),
            citations=result.get("citations", []),
            confidence=result.get("confidence", step.confidence_estimate),
            execution_time=execution_time,
            resource_usage=result.get("resource_usage", {"tool_calls": 1, "tokens": 100}),
            errors=result.get("errors", [])
        )
        
        # Log result
        self._log_execution_result(exec_result)
        
        # Update memory
        self.memory_system.record_episode(
            event_type="step_executed",
            content={
                "step_id": step_id,
                "plan_id": plan_id,
                "status": exec_result.status,
                "confidence": exec_result.confidence
            },
            confidence=exec_result.confidence
        )
        
        self.execution_stats["steps_executed"] += 1
        return exec_result
    
    def _simulate_step_execution(self, step: PlanStep, tools: Dict[str, Any]) -> Dict[str, Any]:
        """Simulate step execution (placeholder for real tool calls)"""
        
        # Check if required tools are available
        missing_tools = [tool for tool in step.tools_required if tool not in tools]
        if missing_tools:
            return {
                "status": "failed",
                "errors": [f"Missing required tools: {missing_tools}"],
                "confidence": 0.0
            }
        
        # Simulate successful execution
        return {
            "status": "success",
            "outputs": {
                f"{step.step_id}_output": f"Simulated output for {step.description}"
            },
            "evidence": [f"Evidence from {step.action_type}"],
            "citations": [f"Source: {step.action_type}_data"],
            "confidence": step.confidence_estimate,
            "resource_usage": {"tool_calls": len(step.tools_required), "tokens": 150}
        }
    
    def _log_execution_result(self, result: ExecutionResult):
        """Log execution result to file"""
        
        with open(self.results_file, 'a') as f:
            f.write(json.dumps(asdict(result)) + '\n')
    
    # CRITIC COMPONENT
    
    def critic_evaluate_result(self, result: ExecutionResult) -> CriticEvaluation:
        """Critic evaluates execution result quality"""
        
        evaluation_id = f"eval_{uuid.uuid4().hex[:8]}"
        
        # Quality assessment based on multiple factors
        quality_score = self._assess_result_quality(result)
        accuracy_assessment = self._assess_accuracy(result)
        completeness_score = self._assess_completeness(result)
        citation_quality = self._assess_citations(result)
        
        # Find issues
        issues = self._identify_issues(result)
        recommendations = self._generate_recommendations(result, issues)
        
        # Overall confidence
        overall_confidence = (
            quality_score * 0.3 + 
            accuracy_assessment * 0.3 + 
            completeness_score * 0.2 + 
            citation_quality * 0.2
        )
        
        evaluation = CriticEvaluation(
            evaluation_id=evaluation_id,
            target_result_id=result.step_id,
            quality_score=quality_score,
            accuracy_assessment=accuracy_assessment,
            completeness_score=completeness_score,
            citation_quality=citation_quality,
            issues_found=issues,
            recommendations=recommendations,
            overall_confidence=overall_confidence,
            needs_revision=overall_confidence < 0.6
        )
        
        # Log evaluation
        with open(self.evaluations_file, 'a') as f:
            f.write(json.dumps(asdict(evaluation)) + '\n')
            
        # Update memory
        self.memory_system.record_episode(
            event_type="result_evaluated",
            content={
                "step_id": result.step_id,
                "quality_score": quality_score,
                "needs_revision": evaluation.needs_revision
            },
            confidence=0.85
        )
        
        return evaluation
    
    def _assess_result_quality(self, result: ExecutionResult) -> float:
        """Assess overall quality of execution result"""
        
        quality_factors = []
        
        # Status success
        if result.status == "success":
            quality_factors.append(0.8)
        elif result.status == "partial":
            quality_factors.append(0.5)
        else:
            quality_factors.append(0.2)
            
        # Error count
        error_penalty = min(0.3, len(result.errors) * 0.1)
        quality_factors.append(1.0 - error_penalty)
        
        # Evidence availability
        evidence_bonus = min(0.2, len(result.evidence) * 0.05)
        quality_factors.append(0.6 + evidence_bonus)
        
        return sum(quality_factors) / len(quality_factors)
    
    def _assess_accuracy(self, result: ExecutionResult) -> float:
        """Assess accuracy of result"""
        # Simplified accuracy assessment
        return result.confidence * 0.9  # Slightly conservative
    
    def _assess_completeness(self, result: ExecutionResult) -> float:
        """Assess completeness of result"""
        # Check if expected outputs are present
        expected_count = 3  # Simplified expectation
        actual_count = len(result.outputs)
        
        return min(1.0, actual_count / expected_count)
    
    def _assess_citations(self, result: ExecutionResult) -> float:
        """Assess citation quality"""
        if not result.citations:
            return 0.3  # Some credit for transparency
            
        # Simple citation quality (in real implementation, validate sources)
        return min(1.0, len(result.citations) * 0.2 + 0.5)
    
    def _identify_issues(self, result: ExecutionResult) -> List[str]:
        """Identify issues with execution result"""
        issues = []
        
        if result.errors:
            issues.append(f"Execution errors: {len(result.errors)}")
            
        if result.confidence < 0.5:
            issues.append("Low confidence in results")
            
        if not result.evidence:
            issues.append("Insufficient supporting evidence")
            
        if not result.citations:
            issues.append("Missing source citations")
            
        return issues
    
    def _generate_recommendations(self, result: ExecutionResult, issues: List[str]) -> List[str]:
        """Generate improvement recommendations"""
        recommendations = []
        
        if "errors" in str(issues):
            recommendations.append("Review and fix execution errors")
            
        if "Low confidence" in str(issues):
            recommendations.append("Gather additional validation data")
            
        if "evidence" in str(issues):
            recommendations.append("Collect more supporting evidence")
            
        if "citations" in str(issues):
            recommendations.append("Add proper source citations")
            
        return recommendations
    
    # ARCHIVIST COMPONENT
    
    def archivist_consolidate_results(self, plan_id: str) -> Dict[str, Any]:
        """Archivist consolidates results and updates memory"""
        
        if plan_id not in self.active_plans:
            return {"error": "Plan not found"}
            
        plan = self.active_plans[plan_id]
        
        # Load all results for this plan
        results = self._load_plan_results(plan_id)
        evaluations = self._load_plan_evaluations(plan_id)
        
        # Create consolidated knowledge
        consolidation = {
            "plan_id": plan_id,
            "goal_id": plan.goal_id,
            "total_steps": len(plan.steps),
            "completed_steps": len(results),
            "success_rate": len([r for r in results if r.status == "success"]) / max(1, len(results)),
            "average_confidence": sum(r.confidence for r in results) / max(1, len(results)),
            "key_learnings": self._extract_learnings(results, evaluations),
            "consolidated_timestamp": datetime.now().isoformat()
        }
        
        # Store semantic facts from successful results
        for result in results:
            if result.status == "success" and result.confidence > 0.6:
                self._store_result_as_semantic_fact(result, plan)
        
        # Update autobiographical memory with experience
        self._update_autobiography_with_experience(plan, consolidation)
        
        # Archive the plan
        self._archive_completed_plan(plan_id, consolidation)
        
        return consolidation
    
    def _load_plan_results(self, plan_id: str) -> List[ExecutionResult]:
        """Load all execution results for a plan"""
        
        results = []
        if os.path.exists(self.results_file):
            with open(self.results_file, 'r') as f:
                for line in f:
                    if line.strip():
                        data = json.loads(line)
                        if data.get('plan_id') == plan_id:
                            results.append(ExecutionResult(**data))
        return results
    
    def _load_plan_evaluations(self, plan_id: str) -> List[CriticEvaluation]:
        """Load all evaluations for a plan"""
        
        evaluations = []
        if os.path.exists(self.evaluations_file):
            with open(self.evaluations_file, 'r') as f:
                for line in f:
                    if line.strip():
                        data = json.loads(line)
                        # Match evaluations to plan results
                        evaluations.append(CriticEvaluation(**data))
        return evaluations
    
    def _extract_learnings(self, results: List[ExecutionResult], 
                          evaluations: List[CriticEvaluation]) -> List[str]:
        """Extract key learnings from execution"""
        
        learnings = []
        
        # Success patterns
        successful_results = [r for r in results if r.status == "success"]
        if successful_results:
            learnings.append(f"Successfully completed {len(successful_results)} steps")
            
        # Common issues
        all_issues = []
        for eval in evaluations:
            all_issues.extend(eval.issues_found)
            
        if all_issues:
            from collections import Counter
            common_issues = Counter(all_issues).most_common(3)
            for issue, count in common_issues:
                learnings.append(f"Common issue: {issue} (occurred {count} times)")
        
        return learnings
    
    def _store_result_as_semantic_fact(self, result: ExecutionResult, plan: ExecutionPlan):
        """Store successful result as semantic knowledge"""
        
        from memory_system import SemanticFact
        
        # Create semantic fact from result
        fact = SemanticFact(
            subject=self.agent_id,
            predicate="successfully_executed",
            object=result.step_id,
            confidence=result.confidence,
            source=f"plan_execution_{plan.plan_id}",
            timestamp=datetime.now().isoformat(),
            agent_id=self.agent_id
        )
        
        self.memory_system.store_semantic_fact(fact)
    
    def _update_autobiography_with_experience(self, plan: ExecutionPlan, 
                                            consolidation: Dict[str, Any]):
        """Update autobiographical memory with plan experience"""
        
        experience_text = f"""
Completed plan {plan.plan_id} for goal: {plan.goal_id}
- Success rate: {consolidation['success_rate']:.1%}
- Average confidence: {consolidation['average_confidence']:.2f}
- Key learnings: {'; '.join(consolidation['key_learnings'])}
"""
        
        self.memory_system.update_autobiography("Key Experiences", experience_text)
    
    def _archive_completed_plan(self, plan_id: str, consolidation: Dict[str, Any]):
        """Archive completed plan"""
        
        # Remove from active plans
        if plan_id in self.active_plans:
            del self.active_plans[plan_id]
            self._save_plans()
        
        # Update stats
        if consolidation['success_rate'] > 0.7:
            self.execution_stats["goals_completed"] += 1
        
        # Update average confidence
        current_avg = self.execution_stats["average_confidence"]
        new_confidence = consolidation['average_confidence']
        self.execution_stats["average_confidence"] = (current_avg + new_confidence) / 2
    
    # SELF-EVALUATION 
    
    def perform_self_evaluation(self, goal_id: str) -> SelfEvaluation:
        """Perform self-evaluation with evidence→claim→confidence chain"""
        
        eval_id = f"selfeval_{uuid.uuid4().hex[:8]}"
        
        # Gather evidence from recent activities
        evidence = self._gather_evidence_for_goal(goal_id)
        
        # Draw claims from evidence
        claims = self._derive_claims_from_evidence(evidence, goal_id)
        
        # Assess confidence in claims
        confidence = self._assess_claim_confidence(evidence, claims)
        
        # Build reasoning chain
        reasoning_chain = self._build_reasoning_chain(evidence, claims)
        
        # Identify uncertainty factors
        uncertainty_factors = self._identify_uncertainty_factors(evidence, claims)
        
        evaluation = SelfEvaluation(
            eval_id=eval_id,
            goal_id=goal_id,
            evidence=evidence,
            claims=claims,
            confidence=confidence,
            reasoning_chain=reasoning_chain,
            uncertainty_factors=uncertainty_factors,
            timestamp=datetime.now().isoformat()
        )
        
        # Store evaluation in memory
        self.memory_system.record_episode(
            event_type="self_evaluation",
            content={
                "goal_id": goal_id,
                "confidence": confidence,
                "claims_count": len(claims),
                "evidence_count": len(evidence)
            },
            confidence=confidence
        )
        
        return evaluation
    
    def _gather_evidence_for_goal(self, goal_id: str) -> List[str]:
        """Gather evidence related to goal execution"""
        
        evidence = []
        
        # Evidence from execution results
        results = self._load_plan_results_for_goal(goal_id)
        for result in results:
            evidence.append(f"Step {result.step_id}: {result.status} with confidence {result.confidence}")
            evidence.extend(result.evidence)
        
        # Evidence from memory
        relevant_episodes = self.memory_system.query_episodes(limit=20)
        for episode in relevant_episodes:
            if goal_id in str(episode.content):
                evidence.append(f"Memory: {episode.event_type} - {episode.content}")
        
        return evidence
    
    def _load_plan_results_for_goal(self, goal_id: str) -> List[ExecutionResult]:
        """Load execution results for a specific goal"""
        
        results = []
        
        # Find plans for this goal
        goal_plans = [plan for plan in self.active_plans.values() if plan.goal_id == goal_id]
        
        # Load results for each plan
        for plan in goal_plans:
            plan_results = self._load_plan_results(plan.plan_id)
            results.extend(plan_results)
            
        return results
    
    def _derive_claims_from_evidence(self, evidence: List[str], goal_id: str) -> List[str]:
        """Derive claims from collected evidence"""
        
        claims = []
        
        # Analyze evidence patterns
        success_count = len([e for e in evidence if "success" in e.lower()])
        failure_count = len([e for e in evidence if "failed" in e.lower()])
        
        if success_count > failure_count:
            claims.append(f"Goal {goal_id} execution is proceeding successfully")
            
        if failure_count > 0:
            claims.append(f"Some challenges encountered in goal {goal_id} execution")
            
        # Confidence-based claims
        confidence_mentions = [e for e in evidence if "confidence" in e.lower()]
        if confidence_mentions:
            claims.append("Execution confidence levels are being tracked")
            
        return claims
    
    def _assess_claim_confidence(self, evidence: List[str], claims: List[str]) -> float:
        """Assess confidence in derived claims"""
        
        if not evidence or not claims:
            return 0.2
            
        # Simple confidence based on evidence quantity and success rate
        evidence_strength = min(1.0, len(evidence) / 10.0)  # More evidence = higher confidence
        
        success_evidence = len([e for e in evidence if "success" in e.lower()])
        total_outcomes = len([e for e in evidence if any(word in e.lower() for word in ["success", "failed", "partial"])])
        
        if total_outcomes > 0:
            success_rate = success_evidence / total_outcomes
        else:
            success_rate = 0.5
            
        overall_confidence = (evidence_strength * 0.4 + success_rate * 0.6)
        return min(0.95, max(0.1, overall_confidence))
    
    def _build_reasoning_chain(self, evidence: List[str], claims: List[str]) -> List[str]:
        """Build step-by-step reasoning chain"""
        
        reasoning = []
        reasoning.append(f"Analyzed {len(evidence)} pieces of evidence")
        reasoning.append(f"Identified {len(claims)} key claims")
        
        # Success analysis
        success_evidence = [e for e in evidence if "success" in e.lower()]
        if success_evidence:
            reasoning.append(f"Found {len(success_evidence)} success indicators")
            
        # Failure analysis  
        failure_evidence = [e for e in evidence if "failed" in e.lower()]
        if failure_evidence:
            reasoning.append(f"Identified {len(failure_evidence)} failure cases")
            
        reasoning.append("Weighted evidence to derive confidence assessment")
        
        return reasoning
    
    def _identify_uncertainty_factors(self, evidence: List[str], claims: List[str]) -> List[str]:
        """Identify factors that create uncertainty"""
        
        uncertainty_factors = []
        
        if len(evidence) < 5:
            uncertainty_factors.append("Limited evidence available")
            
        failure_evidence = [e for e in evidence if "failed" in e.lower()]
        if failure_evidence:
            uncertainty_factors.append("Some execution failures detected")
            
        if not claims:
            uncertainty_factors.append("Difficulty drawing clear conclusions")
            
        return uncertainty_factors
    
    def get_kernel_status(self) -> Dict[str, Any]:
        """Get current status of the agency kernel"""
        
        return {
            "agent_id": self.agent_id,
            "archetype": self.archetype,
            "active_goals": len(self.current_goals),
            "active_plans": len(self.active_plans),
            "execution_stats": self.execution_stats.copy(),
            "continuity_index": self.persona_engine.calculate_continuity_index(),
            "status": "operational"
        }

def main():
    """Demo the agency kernel"""
    
    print("SINCOR Agency Kernel")
    print("=" * 25)
    
    # Import required systems (simplified for demo)
    from memory_system import MemorySystem
    from persona_engine import PersonaEngine
    
    # Initialize systems
    memory = MemorySystem("E-auriga-01")
    persona = PersonaEngine("E-auriga-01", "Scout")
    
    # Create agency kernel
    kernel = AgencyKernel("E-auriga-01", "Scout", memory, persona)
    
    # Create and accept a goal
    goal = TaskGoal(
        goal_id="goal_001",
        description="Research competitive landscape in AI market",
        context={"industry": "AI", "timeframe": "Q4_2025"},
        priority=0.8,
        deadline="2025-12-31",
        success_criteria=["comprehensive_report", "actionable_insights"],
        assigned_agent="E-auriga-01"
    )
    
    kernel.planner_accept_goal(goal)
    print(f"Accepted goal: {goal.goal_id}")
    
    # Planner decomposes goal
    plan = kernel.planner_decompose_goal(goal.goal_id)
    if plan:
        print(f"Created plan: {plan.plan_id} with {len(plan.steps)} steps")
        
        # Executor runs first step
        tools_available = {"web_search": True, "data_scraping": True, "analysis": True}
        result = kernel.executor_run_step(plan.plan_id, plan.steps[0].step_id, tools_available)
        print(f"Executed step: {result.step_id} - Status: {result.status}")
        
        # Critic evaluates result
        evaluation = kernel.critic_evaluate_result(result)
        print(f"Critic evaluation: Quality {evaluation.quality_score:.2f}, Needs revision: {evaluation.needs_revision}")
        
        # Self-evaluation
        self_eval = kernel.perform_self_evaluation(goal.goal_id)
        print(f"Self-evaluation: Confidence {self_eval.confidence:.2f}, Claims: {len(self_eval.claims)}")
        
        # Get kernel status
        status = kernel.get_kernel_status()
        print(f"Kernel status: {status['active_goals']} goals, {status['active_plans']} plans")

if __name__ == "__main__":
    main()