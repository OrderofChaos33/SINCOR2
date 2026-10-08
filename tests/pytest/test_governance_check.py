"""Tests for the central governance enforcement point
(sincor2.governance.check_action, governed decorator, context manager).

Fail-closed contract under test:
  * unknown action -> UnknownAction (never allow what isn't catalogued)
  * ANY check failure -> (False, reason), never (True, ...) on partial failure
  * high/critical actions require approval evidence
  * the @governed decorator and governed_action context manager enforce
  * a DecisionEvent-equivalent dict is recorded on every call
"""
from __future__ import annotations

import pytest

from sincor2.governance import (
    ActionDenied,
    GovernanceError,
    UnknownAction,
    check_action,
    decision_log,
    governed,
    governed_action,
    is_high_risk,
    requires_human_approval,
)


def _commit_bid_context(**over):
    """Full evidence for commit_bid (high, human approval, real policies)."""
    ctx = {
        "human_approved": True,
        "approved_by": "owner-0xabc",
        "pre_conditions": {
            "agent_registered": True,
            "kya_verified": True,
            "kya_heartbeat_fresh": True,
            "stake_sufficient": True,
            "commit_phase_open": True,
            "agent_not_killed": True,
            "standing_approval_envelope_ok": True,
            "rate_limit_ok": True,
        },
        "policy_checks": {
            "verify_wallet_proof": True,
            "heartbeat freshness": True,
            "a2a_rate_limits.bid": True,
        },
    }
    ctx.update(over)
    return ctx


# -- unknown actions ---------------------------------------------------------

def test_unknown_action_raises():
    with pytest.raises(UnknownAction):
        check_action("agent-1", "launch_nukes", {})


def test_unknown_action_is_governance_error():
    assert issubclass(UnknownAction, GovernanceError)
    with pytest.raises(GovernanceError):
        check_action("agent-1", "definitely_not_a_real_action", {})


def test_empty_action_name_raises():
    with pytest.raises(UnknownAction):
        check_action("agent-1", "", {})


# -- approval gating ----------------------------------------------------------

def test_critical_without_approval_denied():
    allowed, reason = check_action("agent-1", "treasury_intent_allocate_live", {})
    assert allowed is False
    assert "approval" in reason.lower()


def test_critical_with_approval_but_missing_preconditions_denied():
    # human_approved alone is not enough: pre-conditions must be evidenced too.
    allowed, reason = check_action(
        "agent-1", "treasury_intent_allocate_live",
        {"human_approved": True, "quorum_reached": True})
    assert allowed is False
    assert "pre-condition" in reason.lower()


def test_high_without_approval_denied():
    allowed, reason = check_action("agent-1", "place_bid", {})
    assert allowed is False
    assert "approval" in reason.lower()


def test_quorum_action_requires_quorum_reached():
    allowed, reason = check_action(
        "agent-1", "treasury_intent_allocate_live",
        {"human_approved": True})  # quorum_reached missing
    assert allowed is False
    assert "quorum" in reason.lower()


# -- evidence evaluation -------------------------------------------------------

def test_high_full_evidence_allowed():
    allowed, reason = check_action("agent-1", "commit_bid", _commit_bid_context())
    assert allowed is True, reason


def test_low_read_allowed_with_minimal_evidence():
    allowed, reason = check_action("agent-1", "mcp_list_tasks", {
        "rate_limit_ok": True,
        "policy_checks": {"a2a_rate_limits.read": True},
    })
    assert allowed is True, reason


def test_missing_precondition_denied_and_named():
    ctx = _commit_bid_context()
    del ctx["pre_conditions"]["stake_sufficient"]
    allowed, reason = check_action("agent-1", "commit_bid", ctx)
    assert allowed is False
    assert "stake_sufficient" in reason


def test_never_allows_on_partial_failure():
    # All-but-one pre-condition evidenced -> still denied.
    ctx = _commit_bid_context()
    ctx["pre_conditions"]["kya_heartbeat_fresh"] = False
    allowed, _ = check_action("agent-1", "commit_bid", ctx)
    assert allowed is False


def test_rate_limit_failure_denied():
    allowed, reason = check_action("agent-1", "mcp_list_tasks", {
        "rate_limit_ok": False,
        "policy_checks": {"a2a_rate_limits.read": True},
    })
    assert allowed is False
    assert "rate_limit_ok" in reason


def test_policy_missing_is_fail_closed():
    # contract_net_auction's only policy check is POLICY-MISSING -> can never
    # be evidenced -> denied.
    allowed, reason = check_action("agent-1", "contract_net_auction", {
        "rate_limit_ok": True,
        "policy_checks": {"anything": True},
    })
    assert allowed is False
    assert "policy" in reason.lower()


def test_unevidenced_policy_check_denied():
    allowed, reason = check_action("agent-1", "mcp_list_tasks", {
        "rate_limit_ok": True,
        "policy_checks": {},  # rate-limit policy not evidenced
    })
    assert allowed is False
    assert "policy" in reason.lower()


# -- introspection --------------------------------------------------------------

def test_is_high_risk():
    assert is_high_risk("treasury_intent_allocate_live") is True   # critical
    assert is_high_risk("place_bid") is True                       # high
    assert is_high_risk("mcp_list_tasks") is False                 # low
    assert is_high_risk("no_such_action") is False                 # unknown


def test_requires_human_approval():
    assert requires_human_approval("treasury_intent_allocate_live") is True
    assert requires_human_approval("place_bid") is True
    assert requires_human_approval("mcp_list_tasks") is False      # auto
    assert requires_human_approval("no_such_action") is False


# -- decorator ------------------------------------------------------------------

def test_governed_decorator_denies_without_approval():
    called = []

    @governed("place_bid")
    def do_bid():
        called.append(True)
        return "bid-placed"

    with pytest.raises(ActionDenied):
        do_bid(governance_agent_id="agent-1", governance_context={})
    assert called == []  # wrapped callable never ran


def test_governed_decorator_no_bypass_on_partial_evidence():
    @governed("commit_bid")
    def do_commit():
        return "committed"

    # human_approved but pre-conditions missing -> still denied.
    with pytest.raises(ActionDenied):
        do_commit(governance_agent_id="agent-1",
                  governance_context={"human_approved": True})


def test_governed_decorator_allows_with_full_evidence():
    @governed("commit_bid")
    def do_commit(task_id):
        return f"committed-{task_id}"

    out = do_commit("task-9", governance_agent_id="agent-1",
                    governance_context=_commit_bid_context())
    assert out == "committed-task-9"


def test_governed_decorator_consumes_governance_kwargs():
    seen = {}

    @governed("mcp_list_tasks")
    def list_tasks(**kwargs):
        seen.update(kwargs)
        return []

    list_tasks(governance_agent_id="agent-1",
               governance_context={"rate_limit_ok": True,
                                   "policy_checks": {"a2a_rate_limits.read": True}})
    assert "governance_agent_id" not in seen
    assert "governance_context" not in seen


# -- context manager ---------------------------------------------------------------

def test_governed_action_context_manager_denies():
    with pytest.raises(ActionDenied):
        with governed_action("place_bid", "agent-1", {}):
            pytest.fail("must not enter the block on deny")


def test_governed_action_context_manager_allows():
    with governed_action("commit_bid", "agent-1",
                         _commit_bid_context()) as rec:
        assert rec["action"] == "commit_bid"
        assert rec["agent_id"] == "agent-1"


# -- decision log ---------------------------------------------------------------------

def test_decision_logged_on_every_call():
    before = len(decision_log())
    check_action("agent-1", "place_bid", {})                       # deny
    check_action("agent-1", "commit_bid", _commit_bid_context())   # allow
    after = decision_log()
    assert len(after) == before + 2
    deny_ev, allow_ev = after[-2], after[-1]
    assert deny_ev["policy_result"] == "deny"
    assert allow_ev["policy_result"] == "allow"
    for ev in (deny_ev, allow_ev):
        for field in ("event", "ts", "agent_id", "action", "risk_tier",
                      "policy_result", "reason", "reason_codes",
                      "human_disposition"):
            assert field in ev, field
    assert deny_ev["action"] == "place_bid"
    assert allow_ev["action"] == "commit_bid"
    assert deny_ev["blocked_in_live_mode"] is True
    assert allow_ev["blocked_in_live_mode"] is False


def test_unknown_action_is_logged_as_deny():
    before = len(decision_log())
    with pytest.raises(UnknownAction):
        check_action("agent-1", "bogus_action", {})
    ev = decision_log()[-1]
    assert len(decision_log()) == before + 1
    assert ev["policy_result"] == "deny"
    assert ev["reason_codes"] == ["unknown_action"]
