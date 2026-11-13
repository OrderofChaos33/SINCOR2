"""Tests for Component 4: shadow-mode dashboard + synthetic verification.

Covers the six dashboard sections, the read-only contract (no mutating
methods, no aggregate agent score), the proposed/approved/verified separation,
the human review loop, all eight synthetic scenarios via run_all(), the
cross-cutting checks (dedup, redaction, restart recovery, agent-cannot-clear),
and the non-negotiable first-monitors status.
"""

import pytest

from sincor2.shadow_monitor.dashboard import ShadowDashboard, SyntheticVerification


# ---------------------------------------------------------------------------
# Fixtures: duck-typed providers (plain dicts/lists, as the coordinator will)
# ---------------------------------------------------------------------------

def _events():
    return [
        {"trace_id": "t1", "seq": 1, "type": "proposal", "risk": "high",
         "workflow": "refunds", "schema_valid": True, "evidence": {"doc": "r1"},
         "latency_ms": 120, "queue_age_s": 5, "tokens": 50},
        {"trace_id": "t1", "seq": 2, "type": "tool_invocation", "risk": "high",
         "workflow": "refunds", "schema_valid": True,
         "latency_ms": 200, "error": True, "retried": True,
         "queue_age_s": 9, "tokens": 30},
        {"trace_id": "t2", "seq": 1, "type": "proposal", "risk": "low",
         "workflow": "triage", "schema_valid": True, "evidence": {"doc": "r2"},
         "latency_ms": 80, "queue_age_s": 2, "tokens": 20},
        {"trace_id": "t2", "seq": 3, "type": "proposal", "risk": "low",
         "workflow": "triage", "schema_valid": False,
         "latency_ms": 90, "queue_age_s": 3, "tokens": 10},
        {"trace_id": "t3", "seq": 1, "type": "result", "risk": "medium",
         "workflow": "refunds", "schema_valid": True, "outcome_verified": True,
         "latency_ms": 150, "queue_age_s": 1, "tokens": 40},
        {"trace_id": "t4", "seq": 1, "type": "proposal", "risk": "medium",
         "workflow": "triage", "schema_valid": True,
         "latency_ms": 110, "queue_age_s": 4, "tokens": 25},
    ]


def _anomalies():
    return [
        {"kind": "safety", "subtype": "injection_attempt", "category": "safety",
         "severity": "HIGH", "trace_id": "t1"},
        {"kind": "safety", "subtype": "confirmed_impact", "category": "safety",
         "severity": "CRITICAL", "trace_id": "t1"},
        {"kind": "safety", "subtype": "denial", "category": "safety",
         "action": "denied", "reason": "policy_block", "trace_id": "t2"},
        {"kind": "safety", "subtype": "denial", "category": "safety",
         "action": "denied", "reason": "policy_block", "trace_id": "t3"},
        {"kind": "safety", "subtype": "denial", "category": "safety",
         "action": "denied", "reason": "rate_limit", "trace_id": "t4"},
        {"kind": "safety", "subtype": "boundary_violation",
         "severity": "HIGH", "trace_id": "t5", "detail": "egress to untrusted host"},
        {"kind": "drift", "subtype": "drift", "workflow": "refunds", "metric": 0.12},
        {"kind": "drift", "subtype": "drift", "workflow": "triage", "metric": 0.03},
        {"kind": "compliance", "subtype": "sensitive_data", "trace_id": "t6"},
        {"kind": "compliance", "subtype": "suppression_optout", "status": "open",
         "trace_id": "t7"},
        {"kind": "compliance", "subtype": "exception", "status": "open",
         "trace_id": "t8", "detail": "pending legal review"},
        {"kind": "reliability", "subtype": "budget_cap", "trace_id": "t9"},
    ]


def _alerts():
    return [
        {"alert_id": "ALT-0001", "severity": "CRITICAL", "kind": "safety",
         "status": "active"},
    ]


def _heartbeat():
    return {
        "effective_mode": "shadow",
        "policy_version": "v1.4.2",
        "active_shadow_workers": 3,
        "disabled_adapters": ["email_send", "payments"],
        "retention_status": "within_policy",
    }


def _paused_workers():
    return [{"worker": "w-7", "reason": "alert_delivery_outage"}]


@pytest.fixture
def dashboard():
    return ShadowDashboard(
        get_events=_events,
        get_anomalies=_anomalies,
        get_alerts=_alerts,
        get_heartbeat=_heartbeat,
        get_paused_workers=_paused_workers,
    )


@pytest.fixture
def verifier():
    return SyntheticVerification()


# ---------------------------------------------------------------------------
# Section shapes
# ---------------------------------------------------------------------------

def test_mode_coverage_shape(dashboard):
    section = dashboard.mode_coverage()
    assert section["effective_mode"] == "shadow"
    assert section["policy_version"] == "v1.4.2"
    assert section["active_shadow_workers"] == 3
    assert section["traces_logged"] == 6
    assert section["event_gaps"] == [{"trace_id": "t2", "missing_seq": [2]}]
    assert section["disabled_adapters"] == ["email_send", "payments"]


def test_safety_shape(dashboard):
    section = dashboard.safety()
    assert section["denied_by_reason"] == {"policy_block": 2, "rate_limit": 1}
    assert isinstance(section["boundary_violations"], list)
    assert len(section["boundary_violations"]) == 1
    # Attempts and confirmed impacts are separate ints, never conflated.
    assert section["injection_attempts"] == 1
    assert section["confirmed_impacts"] == 1
    assert isinstance(section["injection_attempts"], int)
    assert isinstance(section["confirmed_impacts"], int)


def test_quality_shape(dashboard):
    section = dashboard.quality()
    assert section["schema_completeness_pct"] == pytest.approx(100.0 * 5 / 6)
    assert section["evidence_completeness_pct"] == pytest.approx(100.0 * 2 / 4)
    assert section["accepted_count"] == 0
    assert section["edited_count"] == 0
    assert section["rejected_count"] == 0
    assert section["reviewer_disagreement_rate"] == 0.0
    assert section["drift_by_workflow"] == {"refunds": 1, "triage": 1}


def test_quality_reviews_feed_counts_and_disagreement(dashboard):
    dashboard.record_review("t1", "accept", "looks good", "alice")
    dashboard.record_review("t1", "reject", "missed evidence", "bob")
    dashboard.record_review("t2", "edit", "fixed amount", "alice")
    section = dashboard.quality()
    assert section["accepted_count"] == 1
    assert section["edited_count"] == 1
    assert section["rejected_count"] == 1
    assert section["reviewer_disagreement_rate"] == pytest.approx(1.0)


def test_reliability_cost_shape(dashboard):
    section = dashboard.reliability_cost()
    latencies = [120, 200, 80, 90, 150, 110]
    assert section["p50_latency_ms"] == pytest.approx(115.0)
    assert section["p95_latency_ms"] >= section["p50_latency_ms"]
    assert section["error_rate"] == pytest.approx(1 / 6)
    assert section["retry_rate"] == pytest.approx(1 / 6)
    assert section["queue_age_s_max"] == 9
    assert section["token_cost_total"] == 175
    assert section["budget_cap_events"] == 1


def test_data_compliance_shape(dashboard):
    section = dashboard.data_compliance()
    assert section["sensitive_data_flags"] == 1
    assert section["retention_status"] == "within_policy"
    assert section["suppression_optout_open"] == 1
    assert isinstance(section["open_exceptions"], list)
    assert len(section["open_exceptions"]) == 1


# ---------------------------------------------------------------------------
# Business outcomes: proposed / approved / verified stay separate
# ---------------------------------------------------------------------------

def test_business_outcomes_separate_numbers(dashboard):
    dashboard.record_review("t1", "accept", "fine", "alice")
    dashboard.record_review("t2", "edit", "tweaked", "bob")
    section = dashboard.business_outcomes()
    assert section["proposed_count"] == 4
    assert section["human_approved_count"] == 2
    assert section["verified_result_count"] == 1
    # Three independent keys; no conflation.
    assert len({section["proposed_count"],
                section["human_approved_count"],
                section["verified_result_count"]}) == 3
    assert section["conflation_warning"] == ""


def test_business_outcomes_conflation_warning():
    events = [{"trace_id": "x1", "type": "proposal"},
              {"trace_id": "x1", "type": "result", "outcome_verified": True}]
    dash = ShadowDashboard(
        get_events=lambda: events,
        get_anomalies=lambda: [],
        get_alerts=lambda: [],
        get_heartbeat=lambda: {},
        get_paused_workers=lambda: [],
    )
    dash.record_review("x1", "accept", "ok", "alice")
    section = dash.business_outcomes()
    assert section["human_approved_count"] == 1
    assert section["verified_result_count"] == 1
    assert isinstance(section["conflation_warning"], str)
    assert section["conflation_warning"] != ""
    assert "approved" in section["conflation_warning"].lower()


# ---------------------------------------------------------------------------
# Read-only contract
# ---------------------------------------------------------------------------

def test_read_only_no_mutating_methods(dashboard):
    banned = ("set_", "update_", "delete_", "clear_")
    mutating = [name for name in dir(dashboard) if name.startswith(banned)]
    assert mutating == [], f"dashboard must be read-only, found: {mutating}"


def test_no_agent_score_attribute(dashboard):
    assert not hasattr(dashboard, "agent_score")


def test_per_workflow_disagreement_only(dashboard):
    dashboard.record_review("t1", "accept", "ok", "alice")
    dashboard.record_review("t1", "reject", "bad", "bob")
    dashboard.record_review("t2", "accept", "ok", "alice")
    dashboard.record_review("t2", "accept", "ok", "bob")
    result = dashboard.per_workflow_disagreement()
    assert set(result) == {"refunds", "triage"}
    assert result["refunds"] == pytest.approx(1.0)
    assert result["triage"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Human review loop
# ---------------------------------------------------------------------------

def test_review_queue_includes_high_risk_and_sample(dashboard):
    queue = dashboard.review_queue(sample_size=2)
    assert set(queue["high_risk"]) == {"t1"}
    # Sampled traces come only from low/medium strata and respect sample_size.
    assert len(queue["sampled"]) <= 2
    assert set(queue["sampled"]) <= {"t2", "t3", "t4"}
    assert "t1" not in queue["sampled"]
    assert queue["sample_size"] == 2


def test_review_queue_deterministic_sampling(dashboard):
    first = dashboard.review_queue(sample_size=3)
    second = dashboard.review_queue(sample_size=3)
    assert first == second


def test_review_queue_excludes_reviewed(dashboard):
    dashboard.record_review("t1", "accept", "fine", "alice")
    queue = dashboard.review_queue(sample_size=10)
    assert "t1" not in queue["high_risk"]
    assert "t1" not in queue["sampled"]


def test_record_review_builds_calibration_dataset(dashboard):
    assert dashboard.calibration_dataset() == []
    record = dashboard.record_review("t9", "edit", "adjusted total", "carol")
    assert record["trace_id"] == "t9"
    assert record["decision"] == "edit"
    assert record["reviewer"] == "carol"
    assert record["recorded_at"]
    dataset = dashboard.calibration_dataset()
    assert len(dataset) == 1
    assert dataset[0]["reason"] == "adjusted total"
    # Returned copy must not let callers mutate internal state.
    dataset.clear()
    assert len(dashboard.calibration_dataset()) == 1


def test_record_review_rejects_bad_decision(dashboard):
    with pytest.raises(ValueError):
        dashboard.record_review("t1", "maybe", "unsure", "alice")


# ---------------------------------------------------------------------------
# Synthetic verification
# ---------------------------------------------------------------------------

def test_run_all_executes_all_scenarios_and_passes(verifier):
    results = verifier.run_all()
    names = [name for name, _, _ in results]
    for expected in ("attempted_email_send", "unauthorized_tool", "cross_tenant_read",
                     "missing_policy_decision", "retry_loop",
                     "unsupported_settlement_claim", "missing_audit_event",
                     "alert_delivery_outage"):
        assert expected in names, f"scenario missing from run_all: {expected}"
    failed = [(name, detail) for name, passed, detail in results if not passed]
    assert failed == [], f"synthetic scenarios failed: {failed}"


@pytest.mark.parametrize("scenario", [
    "attempted_email_send", "unauthorized_tool", "cross_tenant_read",
    "missing_policy_decision", "retry_loop", "unsupported_settlement_claim",
    "missing_audit_event", "alert_delivery_outage",
])
def test_each_scenario_passes_individually(verifier, scenario):
    by_name = {name: (passed, detail) for name, passed, detail in verifier.run_all()}
    passed, detail = by_name[scenario]
    assert passed, f"{scenario} failed: {detail}"
    assert isinstance(detail, str) and detail


def test_deduplication(verifier):
    passed, detail = verifier.verify_deduplication()
    assert passed, detail
    # Also visible in the aggregate run.
    by_name = {name: passed for name, passed, _ in verifier.run_all()}
    assert by_name["deduplication"] is True


def test_redaction(verifier):
    passed, detail = verifier.verify_redaction()
    assert passed, detail


def test_restart_recovery(verifier):
    passed, detail = verifier.verify_restart_recovery()
    assert passed, detail


def test_agent_cannot_clear_alert(verifier):
    passed, detail = verifier.verify_agent_cannot_clear_alert()
    assert passed, detail


# ---------------------------------------------------------------------------
# Non-negotiable first monitors
# ---------------------------------------------------------------------------

def test_first_monitors_status_keys(verifier):
    status = verifier.first_monitors_status()
    for key in ("effective_mode_check", "actual_side_effects_in_shadow",
                "policy_decision_coverage_pct", "audit_event_completeness",
                "truthful_email_transitions", "truthful_settlement_transitions"):
        assert key in status, f"missing first-monitor key: {key}"


def test_first_monitors_status_values(verifier):
    status = verifier.first_monitors_status()
    assert status["effective_mode_check"] == "shadow"
    assert status["actual_side_effects_in_shadow"] == 0
    assert status["policy_decision_coverage_pct"] == pytest.approx(75.0)
    assert status["audit_event_completeness"] == [
        {"trace_id": "fm-audit-1", "missing_seq": [2]}
    ]
    assert status["truthful_email_transitions"] is True
    assert status["truthful_settlement_transitions"] is True
