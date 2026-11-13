"""Central agent governance: single enforcement point for all agent actions.

Every agent action in the SINCOR2 system is catalogued in
:mod:`sincor2.governance.action_catalog` (111 actions) and guarded in
:mod:`sincor2.governance.guardrails` (111 guardrails, fail-closed).
This module is the single enforcement point:

* :func:`check_action` — evaluate pre-conditions, policy checks, approval,
  and rate limits for one action; fail-closed on ANY failure.
* :func:`is_high_risk` / :func:`requires_human_approval` — risk introspection.
* :func:`governed` — decorator form of :func:`check_action`.
* :func:`governed_action` — context-manager form.

Fail-closed contract:
  * unknown (uncatalogued) action -> raise :class:`UnknownAction` (never
    allow what isn't catalogued);
  * every check failure -> ``(False, reason)`` — never ``(True, ...)`` on a
    partial failure;
  * a DecisionEvent-equivalent dict is recorded on EVERY call (allowed or
    denied) in :data:`DECISION_LOG` and via the ``sincor.governance`` logger;
  * nothing is raised on deny — only unknown actions and internal errors
    raise.

Evidence model: the gate never invents approval. ``context`` carries the
evidence the caller has gathered::

    {
        "human_approved": True,          # satisfies human/quorum approval
        "approved_by": "0x...",          # approver identity (audited)
        "quorum_reached": True,          # required for quorum approval
        "pre_conditions": {              # named pre-condition evidence
            "agent_registered": True,
            "rate_limit_ok": True,
            ...
        },
        "policy_checks": {               # policy-check evidence, keyed by a
            "a2a_rate_limits.read": True # substring of the guardrail's
            ...                          # policy_checks entry
        },
        "rate_limit_ok": True,           # shortcut for the pre-condition token
    }

Missing or false evidence -> deny. ``POLICY-MISSING`` guardrail entries can
never be evidenced -> deny (the policy must be implemented before the
action can pass the central gate).
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from functools import wraps
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .action_catalog import ACTIONS, get_action
from .guardrails import GUARDRAILS, get_guardrail, validate_coverage

# Fail fast on catalog/guardrail drift: the enforcement point must never run
# against a stale or partial catalog.
validate_coverage()

logger = logging.getLogger("sincor.governance")

__all__ = [
    "ACTIONS",
    "GUARDRAILS",
    "get_action",
    "get_guardrail",
    "GovernanceError",
    "ActionDenied",
    "UnknownAction",
    "check_action",
    "is_high_risk",
    "requires_human_approval",
    "governed",
    "governed_action",
    "decision_log",
    "DECISION_LOG",
]


class GovernanceError(Exception):
    """Base class for governance failures (internal errors)."""


class ActionDenied(GovernanceError):
    """Raised by the decorator/context-manager forms when an action is denied."""


class UnknownAction(GovernanceError):
    """Raised when an action is not in the catalog (fail closed)."""


# In-memory DecisionEvent-equivalent log. Fields mirror
# sincor2.shadow_monitor.events.DecisionEvent: every call records one entry,
# allowed or denied. (A durable hash-chained store is the documented upgrade;
# this list is the enforcement point's own record.)
DECISION_LOG: List[Dict[str, Any]] = []


def decision_log() -> List[Dict[str, Any]]:
    """Return a copy of the in-memory decision log."""
    return list(DECISION_LOG)


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _record_decision(
    *,
    agent_id: str,
    action: str,
    risk_tier: str,
    domain: str,
    approval: str,
    policy_result: str,
    reason: str,
    reason_codes: List[str],
    human_disposition: str,
) -> Dict[str, Any]:
    """Build, store, and log the DecisionEvent-equivalent dict.

    Best-effort: a logging failure must never change the allow/deny outcome
    computed by check_action, so errors here are swallowed after one loud
    log attempt.
    """
    event = {
        "event": "governance_decision",
        "ts": _utc(),
        "agent_id": agent_id,
        "action": action,
        "risk_tier": risk_tier,
        "domain": domain,
        "approval": approval,
        "policy_result": policy_result,  # "allow" | "deny"
        "reason": reason,
        "reason_codes": list(reason_codes),
        "human_disposition": human_disposition,
        "blocked_in_live_mode": policy_result == "deny",
    }
    try:
        DECISION_LOG.append(event)
        logger.info(
            "governance %s action=%s agent=%s reason=%s",
            policy_result, action, agent_id, reason,
        )
    except Exception:  # noqa: BLE001 - logging must not alter the decision
        pass
    return event


def _evidence(context: Dict[str, Any], token: str) -> bool:
    """Check named pre-condition evidence. Missing evidence = False (deny)."""
    pre = context.get("pre_conditions") or {}
    if isinstance(pre, dict) and token in pre:
        return bool(pre[token])
    # Top-level shortcuts for common tokens.
    if token in ("rate_limit_ok", "quorum_reached", "killswitch_clear",
                 "not_in_shadow_mode"):
        return bool(context.get(token, False))
    return False


def _policy_satisfied(check: str, policy_evidence: Any) -> Tuple[bool, str]:
    """Check one policy-check entry against caller evidence.

    Returns (satisfied, detail). POLICY-MISSING entries can never be
    satisfied — the policy must be implemented before the action passes.
    """
    if check.startswith("POLICY-MISSING"):
        return (False, "policy_missing")
    if isinstance(policy_evidence, dict):
        for key, value in policy_evidence.items():
            if value and key and (key in check or check in key):
                return (True, "evidenced")
    return (False, "not_evidenced")


def is_high_risk(action: str) -> bool:
    """True for high/critical risk-tier actions; False for unknown actions."""
    entry = get_action(action)
    return bool(entry) and entry.get("risk_tier") in ("high", "critical")


def requires_human_approval(action: str) -> bool:
    """True when the guardrail requires human or quorum approval."""
    try:
        guard = get_guardrail(action)
    except KeyError:
        return False
    return guard.get("approval") in ("human", "quorum")


def check_action(
    agent_id: str,
    action: str,
    context: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """Single enforcement point for agent actions.

    Returns ``(allowed, reason)``. Raises :class:`UnknownAction` for
    uncatalogued actions. Every check failure returns ``(False, reason)`` —
    never ``(True, ...)`` on a partial failure. A DecisionEvent-equivalent
    dict is recorded on every call. Raises only on unknown action or
    internal error.
    """
    ctx: Dict[str, Any] = dict(context or {})
    agent = str(agent_id or "unknown")
    name = str(action or "")

    def deny(reason: str, code: str, *, risk_tier: str = "unknown",
             domain: str = "unknown", approval: str = "unknown") -> Tuple[bool, str]:
        human_disposition = "accepted" if ctx.get("human_approved") else "denied"
        _record_decision(
            agent_id=agent, action=name, risk_tier=risk_tier, domain=domain,
            approval=approval, policy_result="deny", reason=reason,
            reason_codes=[code], human_disposition=human_disposition,
        )
        return (False, reason)

    try:
        entry = get_action(name)
        if entry is None:
            _record_decision(
                agent_id=agent, action=name, risk_tier="unknown",
                domain="unknown", approval="unknown", policy_result="deny",
                reason=f"unknown action {name!r} — not catalogued (fail closed)",
                reason_codes=["unknown_action"],
                human_disposition="denied",
            )
            raise UnknownAction(
                f"action {name!r} is not catalogued — denied (fail closed)")

        risk_tier = str(entry.get("risk_tier", "unknown"))
        domain = str(entry.get("domain", "unknown"))
        try:
            guard = get_guardrail(name)
        except KeyError:
            # validate_coverage() makes this unreachable; deny anyway.
            return deny("no guardrail defined for catalogued action "
                        "(internal inconsistency)", "guardrail_missing",
                        risk_tier=risk_tier, domain=domain)
        approval = str(guard.get("approval", "unknown"))

        # -- approval ------------------------------------------------------
        if approval in ("human", "quorum"):
            if not ctx.get("human_approved"):
                return deny(
                    f"{approval} approval required for {risk_tier} action "
                    f"{name!r}: no human approval evidenced in context",
                    "approval_required",
                    risk_tier=risk_tier, domain=domain, approval=approval)
            if approval == "quorum" and not _evidence(ctx, "quorum_reached"):
                return deny(
                    f"quorum approval required for {name!r}: quorum not "
                    "evidenced in context",
                    "quorum_not_reached",
                    risk_tier=risk_tier, domain=domain, approval=approval)

        # -- pre-conditions -------------------------------------------------
        for token in guard.get("pre_conditions", []):
            if not _evidence(ctx, str(token)):
                return deny(
                    f"pre-condition not satisfied for {name!r}: {token}",
                    "precondition_failed",
                    risk_tier=risk_tier, domain=domain, approval=approval)

        # -- policy checks --------------------------------------------------
        for check in guard.get("policy_checks", []):
            satisfied, detail = _policy_satisfied(
                str(check), ctx.get("policy_checks"))
            if not satisfied:
                if detail == "policy_missing":
                    return deny(
                        f"policy not implemented for {name!r}: "
                        f"{str(check)[:120]}",
                        "policy_missing",
                        risk_tier=risk_tier, domain=domain, approval=approval)
                return deny(
                    f"policy check not evidenced for {name!r}: "
                    f"{str(check)[:120]}",
                    "policy_check_failed",
                    risk_tier=risk_tier, domain=domain, approval=approval)

        # -- allow ----------------------------------------------------------
        human_disposition = ("accepted" if ctx.get("human_approved")
                             else "not_reviewed")
        _record_decision(
            agent_id=agent, action=name, risk_tier=risk_tier, domain=domain,
            approval=approval, policy_result="allow",
            reason="all pre-conditions, policy checks, approval, and rate "
                   "limits evidenced",
            reason_codes=["all_checks_passed"],
            human_disposition=human_disposition,
        )
        return (True, "allowed: all checks evidenced")

    except (UnknownAction, ActionDenied):
        raise
    except GovernanceError:
        raise
    except Exception as exc:  # noqa: BLE001 - internal error raises per contract
        raise GovernanceError(
            f"governance internal error evaluating {name!r}: {exc}") from exc


def governed(action_name: str):
    """Decorator form of :func:`check_action`.

    The wrapped callable must receive ``governance_agent_id=`` and
    ``governance_context=`` keyword arguments (consumed, not forwarded).
    Denied actions raise :class:`ActionDenied` before the callable runs.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            ctx = kwargs.pop("governance_context", None) or {}
            agent_id = (kwargs.pop("governance_agent_id", None)
                        or ctx.get("agent_id") or "unknown")
            allowed, reason = check_action(agent_id, action_name, ctx)
            if not allowed:
                raise ActionDenied(
                    f"action {action_name!r} denied for agent {agent_id}: "
                    f"{reason}")
            return fn(*args, **kwargs)
        return wrapper
    return decorator


@contextmanager
def governed_action(
    action_name: str,
    agent_id: str,
    context: Optional[Dict[str, Any]] = None,
) -> Iterator[Dict[str, Any]]:
    """Context-manager form of :func:`check_action`.

    Raises :class:`ActionDenied` on deny; yields the allow-record on allow.
    """
    allowed, reason = check_action(agent_id, action_name, context)
    if not allowed:
        raise ActionDenied(
            f"action {action_name!r} denied for agent {agent_id}: {reason}")
    yield {"action": action_name, "agent_id": agent_id, "reason": reason}
