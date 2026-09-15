"""Wrap AgencyKernel tool runs with wardrobe.authorize."""

from __future__ import annotations

from typing import Any, Callable

from sincor2.wardrobe.dispatch import authorize


def filter_tools(agent_id: str | None, tools_required: list[str]) -> tuple[list[str], list[dict[str, Any]]]:
    allowed: list[str] = []
    blocked: list[dict[str, Any]] = []
    for tool in tools_required:
        decision = authorize(agent_id, tool)
        if decision.get("ok"):
            allowed.append(tool)
        else:
            blocked.append({"tool": tool, "reason": decision.get("reason")})
    return allowed, blocked


def bind_run_tools(run_tools_for_step: Callable) -> Callable:
    def wrapped(tools_required, step_description, step_inputs, tools_available=None, agent_id=None):
        aid = agent_id or (step_inputs or {}).get("agent_id")
        allowed, blocked = filter_tools(aid, list(tools_required or []))
        result = run_tools_for_step(allowed, step_description, step_inputs, tools_available)
        if blocked:
            result = dict(result)
            result.setdefault("errors", [])
            result["errors"] = list(result["errors"]) + [
                f"{b['tool']}: {b['reason']}" for b in blocked
            ]
            if not allowed:
                result["status"] = "denied"
                result["confidence"] = 0.0
        return result
    return wrapped
