"""Tests for Component 3: anomaly detection + alert routing (shadow mode)."""

import pytest

from sincor2.shadow_monitor.alerting import (
    Alert,
    AlertManager,
    Severity,
    route,
    sanitize,
)
from sincor2.shadow_monitor.anomaly import (
    Anomaly,
    AnomalyClass,
    detect_business_truth,
    detect_identity_data_access,
    detect_policy_prompt_injection,
    detect_quality_drift,
    detect_reliability_cost,
    detect_safety_boundary,
)


def _event(**over):
    base = {
        "trace_id": "trace-1",
        "task_id": "task-1",
        "agent_id": "agent-1",
        "tenant": "tenant-a",
        "home_tenant": "tenant-a",
        "workflow": "payments",
        "action_kind": "would_send",
        "authorized": True,
        "authorization_valid": True,
        "principal": "agent-1",
        "known_principals": ["agent-1"],
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------
# Safety boundary — hard rule, fires on first occurrence.
# --------------------------------------------------------------------------


def test_safety_fires_on_real_effect_action_first_occurrence():
    a = detect_safety_boundary(_event(action_kind="sent"))
    assert a is not None
    assert a.anomaly_class is AnomalyClass.SAFETY_BOUNDARY
    assert a.severity_hint == "critical"


@pytest.mark.parametrize("kind", ["sent", "updated", "settled", "paid"])
def test_safety_fires_for_each_real_effect_kind(kind):
    assert detect_safety_boundary(_event(action_kind=kind)) is not None


def test_safety_quiet_for_would_actions():
    assert detect_safety_boundary(_event(action_kind="would_send")) is None


def test_safety_fires_on_unauthorized_tool():
    a = detect_safety_boundary(_event(authorized=False, tool="shell", target="prod"))
    assert a is not None
    assert a.anomaly_class is AnomalyClass.SAFETY_BOUNDARY


def test_safety_fires_on_halt_clear_and_permission_expansion():
    assert detect_safety_boundary(_event(halt_clear_attempt=True)) is not None
    assert detect_safety_boundary(_event(permission_expansion_attempt=True)) is not None


# --------------------------------------------------------------------------
# Identity / data access — hard rule.
# --------------------------------------------------------------------------


def test_identity_fires_on_cross_tenant_first_occurrence():
    a = detect_identity_data_access(_event(tenant="tenant-b", home_tenant="tenant-a"))
    assert a is not None
    assert a.anomaly_class is AnomalyClass.IDENTITY_DATA_ACCESS
    assert a.cross_tenant_leak is True


def test_identity_fires_on_unexpected_sensitive_classification():
    a = detect_identity_data_access(_event(data_classification="pii"))
    assert a is not None


def test_identity_fires_on_unknown_principal():
    a = detect_identity_data_access(_event(principal="intruder-9"))
    assert a is not None


def test_identity_fires_on_missing_authorization_flag():
    ev = _event()
    del ev["authorization_valid"]
    ev["authorized"] = None
    assert detect_identity_data_access(ev) is not None


# --------------------------------------------------------------------------
# Policy / prompt injection.
# --------------------------------------------------------------------------


def test_injection_fires_on_deny_high_risk():
    a = detect_policy_prompt_injection(
        _event(policy_result="deny", risk_tier="high")
    )
    assert a is not None
    assert "detected_attempt" in a.description


def test_injection_marks_actual_bypass():
    a = detect_policy_prompt_injection(
        _event(
            policy_result="deny",
            risk_tier="critical",
            injection_indicators=["ignore previous instructions"],
            bypass_succeeded=True,
        )
    )
    assert a is not None
    assert a.actual_bypass is True
    assert "actual_bypass" in a.description


def test_injection_fires_on_repeated_attempts():
    a = detect_policy_prompt_injection(_event(attempt_count=4))
    assert a is not None
    assert "detected_attempt" in a.description


def test_injection_quiet_when_nothing_suspicious():
    assert (
        detect_policy_prompt_injection(
            _event(policy_result="allow", risk_tier="low")
        )
        is None
    )


# --------------------------------------------------------------------------
# Quality drift — baseline + min samples + no cross-workflow pooling.
# --------------------------------------------------------------------------


def _baseline(**over):
    b = {
        "workflow": "payments",
        "risk_tier": "low",
        "reviewed_sample_count": 50,
        "error_rate": 0.02,
    }
    b.update(over)
    return b


def test_quality_fires_with_enough_samples():
    a = detect_quality_drift(
        _event(unsupported_claims=True), _baseline()
    )
    assert a is not None
    assert a.anomaly_class is AnomalyClass.QUALITY_DRIFT


def test_quality_needs_min_samples():
    a = detect_quality_drift(
        _event(unsupported_claims=True),
        _baseline(reviewed_sample_count=29),
        min_samples=30,
    )
    assert a is None


def test_quality_no_cross_workflow_pooling():
    with pytest.raises(ValueError):
        detect_quality_drift(
            _event(unsupported_claims=True, workflow="payments"),
            _baseline(workflow="refunds"),
        )


def test_quality_quiet_without_signals():
    assert detect_quality_drift(_event(), _baseline()) is None


# --------------------------------------------------------------------------
# Reliability / cost — sustained deviation only.
# --------------------------------------------------------------------------


def _window(**over):
    w = {
        "consecutive_breaches": 4,
        "max_retries": 3,
        "baseline_tokens": 1000.0,
        "token_spike_ratio": 2.0,
        "queue_age_threshold_s": 300.0,
    }
    w.update(over)
    return w


def test_reliability_fires_on_sustained_retry_loop():
    a = detect_reliability_cost(_event(retry_count=9), _window())
    assert a is not None
    assert a.anomaly_class is AnomalyClass.RELIABILITY_COST


def test_reliability_fires_on_stalled_queue_and_spike():
    a = detect_reliability_cost(
        _event(queue_age_s=900.0, tokens_used=5000.0), _window()
    )
    assert a is not None


def test_reliability_needs_sustained_breaches():
    a = detect_reliability_cost(
        _event(retry_count=9), _window(consecutive_breaches=2)
    )
    assert a is None


def test_reliability_tracks_sustained_count():
    a = detect_reliability_cost(_event(timed_out=True), _window(consecutive_breaches=7))
    assert a is not None
    assert a.sustained_breaches == 7


# --------------------------------------------------------------------------
# Business truth — ledger integrity.
# --------------------------------------------------------------------------


def test_business_truth_fires_on_settlement_without_evidence():
    a = detect_business_truth(_event(status="settled", evidence_refs=[]))
    assert a is not None
    assert a.anomaly_class is AnomalyClass.BUSINESS_TRUTH


def test_business_truth_fires_on_duplicate_ledger_id():
    a = detect_business_truth(
        _event(
            ledger_event_id="evt-42",
            seen_event_ids=["evt-42"],
        )
    )
    assert a is not None


def test_business_truth_fires_on_missing_expected_events():
    a = detect_business_truth(
        _event(
            ledger_event_id="evt-1",
            seen_event_ids=["evt-1"],
            expected_event_ids=["evt-1", "evt-2"],
        )
    )
    assert a is not None


def test_business_truth_fires_on_count_drift():
    a = detect_business_truth(
        _event(product_count=26, expected_product_count=24)
    )
    assert a is not None


def test_business_truth_quiet_when_consistent():
    assert (
        detect_business_truth(
            _event(
                status="settled",
                evidence_refs=["ipfs://x"],
                ledger_event_id="evt-3",
                seen_event_ids=["evt-1"],
                expected_event_ids=["evt-1", "evt-3"],
            )
        )
        is None
    )


# --------------------------------------------------------------------------
# Routing.
# --------------------------------------------------------------------------


def _anomaly(aclass, **flags):
    return Anomaly(
        anomaly_class=aclass,
        description="d",
        trace_id="t",
        workflow="w",
        policy_reason="p",
        **flags,
    )


def test_route_safety_critical():
    assert route(_anomaly(AnomalyClass.SAFETY_BOUNDARY)) is Severity.CRITICAL


def test_route_identity_high_and_critical_on_leak():
    assert route(_anomaly(AnomalyClass.IDENTITY_DATA_ACCESS)) is Severity.HIGH
    assert (
        route(_anomaly(AnomalyClass.IDENTITY_DATA_ACCESS, cross_tenant_leak=True))
        is Severity.CRITICAL
    )


def test_route_injection_bypass_vs_attempt():
    assert (
        route(_anomaly(AnomalyClass.POLICY_PROMPT_INJECTION, actual_bypass=True))
        is Severity.HIGH
    )
    assert (
        route(_anomaly(AnomalyClass.POLICY_PROMPT_INJECTION))
        is Severity.WARNING
    )


def test_route_quality_warning():
    assert route(_anomaly(AnomalyClass.QUALITY_DRIFT)) is Severity.WARNING


def test_route_reliability_escalates_at_five():
    assert route(_anomaly(AnomalyClass.RELIABILITY_COST)) is Severity.WARNING
    assert (
        route(_anomaly(AnomalyClass.RELIABILITY_COST, sustained_breaches=5))
        is Severity.HIGH
    )


def test_route_business_truth_high():
    assert route(_anomaly(AnomalyClass.BUSINESS_TRUTH)) is Severity.HIGH


# --------------------------------------------------------------------------
# AlertManager: dedup, sanitize, failure, dead-man, guard, only action.
# --------------------------------------------------------------------------


def test_dedup_by_key():
    mgr = AlertManager()
    alert = Alert.from_anomaly(_anomaly(AnomalyClass.SAFETY_BOUNDARY))
    r1 = mgr.send(alert)
    assert r1["delivered"] is True
    r2 = mgr.send(alert)
    assert r2 == {"deduped": True, "alert_id": alert.alert_id}
    assert len(mgr.outbox) == 1


def test_sanitize_strips_secrets():
    secret = "0x" + "ab" * 32
    cleaned = sanitize(f"key={secret} and token sk_live_abcdef1234567890 end")
    assert secret not in cleaned
    assert "sk_live_abcdef1234567890" not in cleaned
    assert "[REDACTED]" in cleaned


def test_alert_stored_sanitized():
    mgr = AlertManager()
    a = _anomaly(AnomalyClass.SAFETY_BOUNDARY)
    a.description = "saw key 0x" + "cd" * 32
    alert = Alert.from_anomaly(a)
    mgr.send(alert)
    assert "0x" + "cd" * 32 not in mgr.outbox[0].explanation
    assert "[REDACTED]" in mgr.outbox[0].explanation


def test_delivery_failure_recorded_and_capability_paused():
    def boom(alert):
        raise ConnectionError("sink down")

    mgr = AlertManager(delivery_fn=boom)
    alert = Alert.from_anomaly(_anomaly(AnomalyClass.BUSINESS_TRUTH))
    receipt = mgr.send(alert)
    assert receipt["delivered"] is False
    assert len(mgr.failed_deliveries) == 1
    assert "alert_delivery" in mgr.paused_capabilities
    # alert never lost
    assert mgr.failed_deliveries[0]["alert"] is alert


def test_deadman_detects_silent_component():
    mgr = AlertManager()
    mgr.heartbeat("logger")
    mgr.heartbeat("evaluator")
    silent = mgr.check_deadman(timeout_s=3600)
    assert "alert_sender" in silent
    assert "logger" not in silent


def test_agent_cannot_clear_alert_raises():
    mgr = AlertManager()
    with pytest.raises(PermissionError):
        mgr.agent_cannot_clear_alert(
            {"principal_type": "agent"}, "agent-1", "alert-1"
        )
    # humans may acknowledge
    assert (
        mgr.agent_cannot_clear_alert(
            {"principal_type": "human"}, "agent-1", "alert-1"
        )
        is True
    )


def test_pause_shadow_worker_is_only_action():
    mgr = AlertManager()
    rec = mgr.pause_shadow_worker("worker-7", "safety boundary breach")
    assert rec["paused"] is True
    assert mgr.paused_workers["worker-7"]["worker_id"] == "worker-7"
    forbidden = (
        "change_permissions",
        "clear_halt",
        "retry_action",
        "modify_production_data",
        "grant_permission",
        "execute",
    )
    for name in forbidden:
        assert not hasattr(mgr, name), name


def test_alerting_path_independence():
    import sincor2.shadow_monitor.alerting as mod

    src = open(mod.__file__).read()
    # delivery function is injected at construction; module never imports or
    # calls into agent runtime modules.
    assert "a2a_runtime" not in src
    assert "agent_runtime" not in src
    assert "delivery_fn" in src
    assert "never imports or" in mod.AlertManager.__doc__
