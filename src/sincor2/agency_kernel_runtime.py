#!/usr/bin/env python3
"""Production executor runtime for AgencyKernel — real tools + execute_task entrypoint."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sincor2.agency_kernel import (
    AgencyKernel,
    TaskGoal,
    ExecutionResult,
)


def _real_executor_run_step(self, plan_id: str, step_id: str,
                            tools_available: Dict[str, Any]) -> ExecutionResult:
    """Executor runs a single plan step with real tools."""
    if plan_id not in self.active_plans:
        raise ValueError(f"Plan {plan_id} not found")
    plan = self.active_plans[plan_id]
    step = next((s for s in plan.steps if s.step_id == step_id), None)
    if not step:
        raise ValueError(f"Step {step_id} not found in plan {plan_id}")

    start_time = datetime.now()
    try:
        from sincor2.agency_kernel_tools import run_tools_for_step
        result = run_tools_for_step(
            tools_required=step.tools_required,
            step_description=step.description,
            step_inputs=step.inputs or {},
            tools_available=tools_available,
        )
    except Exception as err:
        result = {
            "status": "failed",
            "outputs": {},
            "evidence": [],
            "citations": [],
            "confidence": 0.0,
            "resource_usage": {"tool_calls": 0, "tokens": 0},
            "errors": [str(err)],
        }
    execution_time = (datetime.now() - start_time).total_seconds()

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
        errors=result.get("errors", []),
    )
    self._log_execution_result(exec_result)
    self.memory_system.record_episode(
        event_type="step_executed",
        content={
            "step_id": step_id,
            "plan_id": plan_id,
            "status": exec_result.status,
            "confidence": exec_result.confidence,
        },
        confidence=exec_result.confidence,
    )
    self.execution_stats["steps_executed"] += 1
    return exec_result


def _execute_task(self, task_context: Dict[str, Any]) -> Dict[str, Any]:
    """Top-level entry used by vertical_dispatch / A2A."""
    task_type = task_context.get("task_type") or task_context.get("skill_id") or "generic"
    input_text = task_context.get("input") or task_context.get("description") or str(task_context)
    goal_id = f"goal_{uuid.uuid4().hex[:10]}"

    goal = TaskGoal(
        goal_id=goal_id,
        description=str(input_text)[:500],
        context=task_context,
        priority=float(task_context.get("priority", 0.7)),
        deadline=task_context.get("deadline"),
        success_criteria=task_context.get("success_criteria") or ["produce_output"],
        assigned_agent=self.agent_id,
    )
    self.planner_accept_goal(goal)
    plan = self.planner_decompose_goal(goal_id)
    if not plan:
        return {"status": "failed", "error": "planner returned no plan", "task_type": task_type}

    step_results = []
    tools_available = {
        "web_search": True, "data_scraping": True, "search": True,
        "python_exec": True, "execution": True, "file_read": True,
        "analysis": True, "summarization": True, "synthesis": True,
        "validation": True, "cross_reference": True, "claude_reason": True,
    }
    for step in plan.steps:
        try:
            res = self.executor_run_step(plan.plan_id, step.step_id, tools_available)
            step_results.append({
                "step_id": res.step_id,
                "status": res.status,
                "outputs": res.outputs,
                "confidence": res.confidence,
                "errors": res.errors,
            })
        except Exception as err:
            step_results.append({
                "step_id": step.step_id,
                "status": "failed",
                "outputs": {},
                "confidence": 0.0,
                "errors": [str(err)],
            })

    successes = [r for r in step_results if r["status"] == "success"]
    overall = "completed" if successes else "failed"
    primary_output = None
    for r in reversed(step_results):
        outs = r.get("outputs") or {}
        for v in outs.values():
            if isinstance(v, dict) and v.get("output"):
                primary_output = v["output"]
                break
            if isinstance(v, dict) and v.get("results"):
                primary_output = v["results"]
                break
        if primary_output is not None:
            break

    return {
        "status": overall,
        "task_type": task_type,
        "goal_id": goal_id,
        "plan_id": plan.plan_id,
        "steps_run": len(step_results),
        "steps_succeeded": len(successes),
        "primary_output": primary_output,
        "step_results": step_results,
        "agent_id": self.agent_id,
        "archetype": self.archetype,
    }


def install_production_runtime() -> None:
    """Bind real executor methods onto AgencyKernel (idempotent)."""
    AgencyKernel.executor_run_step = _real_executor_run_step  # type: ignore[method-assign]
    AgencyKernel.execute_task = _execute_task  # type: ignore[attr-defined]
    _orig_init = AgencyKernel.__init__

    def _init(self, agent_id: str = "E-kernel-01", archetype: str = "Scout",
              memory_system=None, persona_engine=None, kernel_dir: str = "kernels"):
        if memory_system is None:
            class _MemStub:
                def record_episode(self, *a, **k): pass
                def store_semantic_fact(self, *a, **k): pass
                def update_autobiography(self, *a, **k): pass
                def query_episodes(self, limit=20): return []
            memory_system = _MemStub()
        if persona_engine is None:
            class _PersonaStub:
                def get_behavioral_preferences(self):
                    return {"decision_making": {"risk_tolerance": 0.5, "deliberation_level": 0.5}}
                def calculate_continuity_index(self):
                    return 0.8
            persona_engine = _PersonaStub()
        _orig_init(self, agent_id or "E-kernel-01", archetype or "Scout",
                   memory_system, persona_engine, kernel_dir)

    AgencyKernel.__init__ = _init  # type: ignore[method-assign]


# Auto-install on import
install_production_runtime()
