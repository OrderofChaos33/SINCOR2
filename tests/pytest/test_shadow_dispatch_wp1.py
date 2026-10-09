"""WP1 tests: single typed dispatch API on ShadowRuntime.

Covers:
(a) unauthorized originator rejected (ValueError)
(b) kill-switch replay returns blocked_kill_switch, not the cached allow (W-40)
(c) audit failure -> AuditFailureError, no receipt, nothing cached/queued
(d) all 8 effect types dispatch through the single dispatch() API
plus: idempotency conflict, clean replay identity, registry immutability,
and no live credentials/signer/force_live anywhere.
"""

import pytest

from sincor2.shadow_monitor.contract import EFFECT_ORIGINATORS, EFFECT_VOCABULARY
from sincor2.shadow_monitor.effect_boundary import (
    AuditFailureError,
    EffectIntent,
    EffectReceipt,
    IdempotencyConflict,
)
from sincor2.shadow_monitor.runtime import ShadowRuntime, build_shadow_runtime


def _make_intent(effect_type, **overrides):
    base = dict(
        effect_type=effect_type,
        target="ci://wp1-dispatch-test",
        payload={"test": "wp1"},
        idempotency_key=f"wp1-{effect_type}",
        agent_id="ci-agent",
    )
    base.update(overrides)
    return EffectIntent.from_payload(**base)


def _originator_for(effect_type):
    """A valid originator for the type: agent where allowed, else operator."""
    allowed = EFFECT_ORIGINATORS[effect_type]
    return "agent" if "agent" in allowed else "operator"


# ---------------------------------------------------------------------------
# (a) originator validation
# ---------------------------------------------------------------------------


def test_unauthorized_originator_rejected():
    rt = build_shadow_runtime()
    intent = _make_intent("email.send", idempotency_key="wp1-a1")
    # customer is never a valid shadow-proposal originator
    with pytest.raises(ValueError, match="may not originate"):
        rt.dispatch(intent, "customer")
    # unknown originator string
    with pytest.raises(ValueError, match="may not originate"):
        rt.dispatch(intent, "superuser")
    with pytest.raises(ValueError, match="may not originate"):
        rt.dispatch(intent, "")


def test_crm_delete_operator_only_enforced():
    rt = build_shadow_runtime()
    intent = _make_intent("crm.delete", idempotency_key="wp1-a2")
    with pytest.raises(ValueError, match="may not originate"):
        rt.dispatch(intent, "agent")
    with pytest.raises(ValueError, match="may not originate"):
        rt.dispatch(intent, "background_worker")
    # operator is allowed
    decision, receipt = rt.dispatch(intent, "operator")
    assert decision.originator == "operator"
    assert isinstance(receipt, EffectReceipt)


def test_dispatch_rejects_non_intent():
    rt = build_shadow_runtime()
    with pytest.raises(TypeError, match="EffectIntent"):
        rt.dispatch({"effect_type": "email.send"}, "agent")


def test_dispatch_rejects_unknown_effect_type():
    rt = build_shadow_runtime()
    # EffectIntent itself rejects unknown types at construction
    with pytest.raises(ValueError, match="unknown effect_type"):
        _make_intent("nuke.launch", idempotency_key="wp1-a3")
    # registry lookup also fails closed
    with pytest.raises(ValueError, match="unknown shadow effect type"):
        rt.entrypoint_for("shell.exec")


# ---------------------------------------------------------------------------
# (b) kill-switch re-evaluation on replay (W-40 fix)
# ---------------------------------------------------------------------------


def test_kill_switch_replay_returns_blocked_not_cached_allow():
    rt = build_shadow_runtime()
    intent = _make_intent(
        "payment.transfer", idempotency_key="wp1-b1", risk_tier="low"
    )
    decision1, receipt1 = rt.dispatch(intent, "agent")
    assert decision1.verdict == "allow_proposal"
    assert receipt1.status == "would_execute"

    # Engage the kill switch AFTER the original allow.
    rt.effect_boundary.kill_switch.engage()

    # Replay: must NOT return the cached allow.
    decision2, receipt2 = rt.dispatch(intent, "agent")
    assert decision2.verdict == "blocked_kill_switch"
    assert decision2.kill_switch_engaged is True
    assert decision2.effect_id == intent.effect_id
    assert receipt2.status == "blocked_policy"
    assert receipt2.executed is False
    # The original decision object is untouched.
    assert decision1.verdict == "allow_proposal"
    assert decision1.kill_switch_engaged is False


def test_kill_switch_override_replay_is_audited():
    rt = build_shadow_runtime()
    intent = _make_intent(
        "trade.swap", idempotency_key="wp1-b2", risk_tier="low"
    )
    rt.dispatch(intent, "agent")
    audits_before = len(rt.decision_audit_log())
    rt.effect_boundary.kill_switch.engage()
    decision2, _ = rt.dispatch(intent, "agent")
    assert decision2.verdict == "blocked_kill_switch"
    audits_after = len(rt.decision_audit_log())
    assert audits_after == audits_before + 1
    assert rt.decision_audit_log()[-1]["verdict"] == "blocked_kill_switch"


def test_kill_switch_does_not_block_non_value_replay():
    rt = build_shadow_runtime()
    intent = _make_intent("email.send", idempotency_key="wp1-b3")
    d1, r1 = rt.dispatch(intent, "agent")
    rt.effect_boundary.kill_switch.engage()
    d2, r2 = rt.dispatch(intent, "agent")
    # email.send is not value-moving: the original allow stands.
    assert d2 is d1
    assert r2 is r1
    assert d2.verdict == "allow_proposal"


def test_kill_switch_disengage_restores_original_allow():
    rt = build_shadow_runtime()
    intent = _make_intent(
        "contract.call", idempotency_key="wp1-b4", risk_tier="low"
    )
    rt.dispatch(intent, "agent")
    rt.effect_boundary.kill_switch.engage()
    d_blocked, _ = rt.dispatch(intent, "agent")
    assert d_blocked.verdict == "blocked_kill_switch"
    rt.effect_boundary.kill_switch.disengage()
    d_restored, r_restored = rt.dispatch(intent, "agent")
    assert d_restored.verdict == "allow_proposal"
    assert r_restored.status == "would_execute"


def test_boundary_level_replay_also_honors_kill_switch():
    """The W-40 fix at the boundary: direct boundary.dispatch replays."""
    from sincor2.shadow_monitor.effect_boundary import build_effect_boundary

    boundary = build_effect_boundary()
    intent = _make_intent(
        "payment.transfer", idempotency_key="wp1-b5", risk_tier="low"
    )
    r1 = boundary.dispatch(intent)
    assert r1.status == "would_execute"
    boundary.kill_switch.engage()
    r2 = boundary.dispatch(intent)
    assert r2.status == "blocked_policy"
    assert r2.executed is False
    assert "kill_switch_engaged_would_pay_blocked" in r2.policy_reason_codes


# ---------------------------------------------------------------------------
# (c) fail-closed audit
# ---------------------------------------------------------------------------


class _BrokenAuditLog(list):
    def append(self, item):
        raise OSError("audit store unavailable")


def test_audit_failure_means_no_receipt_no_cache_no_queue():
    rt = build_shadow_runtime(decision_audit_log=_BrokenAuditLog())
    intent = _make_intent("email.send", idempotency_key="wp1-c1")
    with pytest.raises(AuditFailureError):
        rt.dispatch(intent, "agent")
    # Fail-closed: nothing cached, nothing queued, nothing audited.
    assert rt._decisions == {}
    assert rt.effect_boundary.proposal_queue == []
    assert rt.effect_boundary.audit_log == []


def test_audit_failure_on_kill_switch_override_replay():
    rt = build_shadow_runtime()
    intent = _make_intent(
        "payment.transfer", idempotency_key="wp1-c2", risk_tier="low"
    )
    rt.dispatch(intent, "agent")
    rt.effect_boundary.kill_switch.engage()
    # Swap in a broken audit log: the override decision cannot be audited.
    # (frozen dataclass: install via object.__setattr__)
    object.__setattr__(rt, "_decision_audit_log", _BrokenAuditLog())
    with pytest.raises(AuditFailureError):
        rt.dispatch(intent, "agent")


# ---------------------------------------------------------------------------
# (d) all 8 effect types through the single API
# ---------------------------------------------------------------------------


def test_all_eight_effect_types_dispatch_through_single_api():
    rt = build_shadow_runtime()
    assert len(EFFECT_VOCABULARY) == 8
    for effect_type in sorted(EFFECT_VOCABULARY):
        originator = _originator_for(effect_type)
        intent = _make_intent(
            effect_type, idempotency_key=f"wp1-d-{effect_type}"
        )
        decision, receipt = rt.dispatch(intent, originator)
        assert decision.effect_type == effect_type
        assert decision.originator == originator
        assert decision.effect_id == intent.effect_id
        assert decision.idempotency_key == intent.idempotency_key
        assert decision.verdict in (
            "allow_proposal",
            "deny",
            "require_approval",
            "blocked_kill_switch",
        )
        assert isinstance(receipt, EffectReceipt)
        assert receipt.effect_id == intent.effect_id
        assert receipt.executed is False


def test_decision_audited_before_receipt():
    rt = build_shadow_runtime()
    intent = _make_intent("message.send", idempotency_key="wp1-d2")
    decision, _ = rt.dispatch(intent, "agent")
    audit = rt.decision_audit_log()
    assert len(audit) == 1
    record = audit[0]
    assert record["event"] == "policy_decision"
    assert record["effect_id"] == decision.effect_id
    assert record["verdict"] == decision.verdict
    assert record["originator"] == "agent"


# ---------------------------------------------------------------------------
# idempotency semantics
# ---------------------------------------------------------------------------


def test_clean_replay_returns_original_decision_and_receipt():
    rt = build_shadow_runtime()
    intent = _make_intent("email.send", idempotency_key="wp1-e1")
    d1, r1 = rt.dispatch(intent, "agent")
    d2, r2 = rt.dispatch(intent, "agent")
    assert d2 is d1
    assert r2 is r1
    # No duplicate audit record for a clean replay.
    assert len(rt.decision_audit_log()) == 1


def test_idempotency_conflict_on_payload_change():
    rt = build_shadow_runtime()
    i1 = _make_intent("email.send", idempotency_key="wp1-e2", payload={"a": 1})
    rt.dispatch(i1, "agent")
    i2 = _make_intent("email.send", idempotency_key="wp1-e2", payload={"a": 2})
    with pytest.raises(IdempotencyConflict):
        rt.dispatch(i2, "agent")


# ---------------------------------------------------------------------------
# registry + structural invariants
# ---------------------------------------------------------------------------


def test_registry_immutable_and_complete():
    rt = build_shadow_runtime()
    assert set(rt.effect_entrypoints.keys()) == set(EFFECT_VOCABULARY)
    with pytest.raises(TypeError):
        rt.effect_entrypoints["email.send"] = lambda intent: None  # type: ignore[index]


def test_entrypoints_route_through_single_dispatch_api():
    rt = build_shadow_runtime()
    for effect_type in sorted(EFFECT_VOCABULARY):
        originator = _originator_for(effect_type)
        intent = _make_intent(
            effect_type, idempotency_key=f"wp1-f-{effect_type}"
        )
        receipt = rt.effect_entrypoints[effect_type](intent, originator)
        assert isinstance(receipt, EffectReceipt)
        assert receipt.executed is False
        # The dispatch went through the single API: decision was audited.
        assert any(
            r["effect_id"] == intent.effect_id
            for r in rt.decision_audit_log()
        )


def test_no_live_credentials_signer_or_force_live():
    rt = build_shadow_runtime()
    assert rt.live_executor is None
    assert rt.signer is None
    assert rt.write_credentials == ()
    assert not hasattr(rt, "force_live")


def test_high_risk_intent_denied():
    rt = build_shadow_runtime()
    intent = _make_intent(
        "payment.transfer", idempotency_key="wp1-g1", risk_tier="high"
    )
    decision, receipt = rt.dispatch(intent, "operator")
    assert decision.verdict == "deny"
    assert receipt.status == "blocked_policy"


def test_medium_risk_cost_requires_approval():
    rt = build_shadow_runtime()
    intent = _make_intent(
        "trade.swap",
        idempotency_key="wp1-g2",
        risk_tier="medium",
        estimated_cost=10.0,
    )
    decision, receipt = rt.dispatch(intent, "operator")
    assert decision.verdict == "require_approval"
    assert receipt.status == "pending_approval"
