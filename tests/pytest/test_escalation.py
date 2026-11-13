"""Phase 1 escalation layer — unit tests.

Covers: PolicyEngine classification of every category class (hard
gates, detector rule hits, spend thresholds, internal allows, unknown
fail-closed), the Canonical Escalation Envelope Schema serialized
shape, the state machine (legal + illegal transitions), the
override-note rule, store durability (state file + append-only audit
log), and the require_decision gate hook.
"""

import json

import pytest

from sincor2.escalation import (
    ALLOW,
    APPROVED,
    DENIED,
    ESCALATED,
    ESCALATE,
    EXPIRED,
    HARD_GATE_CATEGORIES,
    INTERNAL_CATEGORIES,
    OVERRIDDEN,
    PENDING,
    Decision,
    EscalationPackage,
    EscalationStore,
    IllegalTransitionError,
    PackageNotFoundError,
    PolicyEngine,
    StoreCorruptError,
    require_decision,
    verify_chain,
)

T0 = "2026-11-01T00:00:00+00:00"
T1 = "2026-11-01T01:00:00+00:00"
T2 = "2026-11-01T02:00:00+00:00"


@pytest.fixture
def store(tmp_path):
    return EscalationStore(str(tmp_path / "esc"))


def _raise(store, category="merge", **overrides):
    kwargs = dict(
        source_surface="test-surface",
        actor="agent-1",
        action_category=category,
        summary="do the thing",
        evidence_refs=["doc:1"],
        severity="high",
        now=T0,
    )
    kwargs.update(overrides)
    return store.create_package(**kwargs)


# ---------------------------------------------------------------------------
# PolicyEngine
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("category", sorted(HARD_GATE_CATEGORIES))
def test_hard_gate_categories_always_escalate(category):
    verdict = PolicyEngine.classify(category)
    assert verdict.verdict == ESCALATE
    assert verdict.reason == "hard_gate_category"
    assert verdict.category == category


def test_hard_gate_categories_are_the_spec_set():
    assert HARD_GATE_CATEGORIES == {
        "treasury",
        "funds",
        "merge",
        "deploy",
        "private_keys",
        "legal_compliance",
        "external_send",
        "production_action",
        "withdrawal",
    }


@pytest.mark.parametrize("category", sorted(INTERNAL_CATEGORIES))
def test_reversible_internal_categories_allow(category):
    verdict = PolicyEngine.classify(category)
    assert verdict.verdict == ALLOW
    assert verdict.reason == "reversible_internal"


@pytest.mark.parametrize(
    "category", ["teleport", "", "  ", "MERGE_ALL", "delete_everything"]
)
def test_unknown_categories_fail_closed(category):
    verdict = PolicyEngine.classify(category)
    assert verdict.verdict == ESCALATE
    assert verdict.reason == "unknown_category"


@pytest.mark.parametrize("category", [None, 42, ["merge"], {"x": 1}])
def test_non_string_category_fails_closed(category):
    verdict = PolicyEngine.classify(category)
    assert verdict.verdict == ESCALATE
    assert verdict.reason == "unknown_category"


def test_category_is_normalized_before_classification():
    assert PolicyEngine.classify("  Merge ").verdict == ESCALATE
    assert PolicyEngine.classify("REPORT").verdict == ALLOW


@pytest.mark.parametrize("flag", ["irreversible", "external_effect"])
def test_context_escalation_flags_override_internal_allow(flag):
    verdict = PolicyEngine.classify("report", {flag: True})
    assert verdict.verdict == ESCALATE
    assert verdict.reason == f"context_flag:{flag}"


def test_context_flags_do_not_change_hard_gate_reason():
    verdict = PolicyEngine.classify("merge", {"irreversible": True})
    assert verdict.verdict == ESCALATE
    assert verdict.reason == "hard_gate_category"


def test_classification_is_deterministic():
    first = PolicyEngine.classify("deploy", {"irreversible": False})
    for _ in range(5):
        assert PolicyEngine.classify("deploy", {"irreversible": False}) == first


# ---------------------------------------------------------------------------
# Package schema v1
# ---------------------------------------------------------------------------


def test_envelope_serialized_shape_is_canonical_schema(store):
    pkg = _raise(store)
    data = pkg.to_dict()
    assert set(data) == {
        "schema",
        "schema_version",
        "id",
        "created_at",
        "tenant",
        "source_surface",
        "source_agent_id",
        "source_context",
        "actor",
        "action_category",
        "summary",
        "triggered_rule",
        "state_snapshot",
        "evidence_refs",
        "severity",
        "state",
        "action_choices",
        "risk",
        "decision",
        "attestations",
        "history",
    }
    assert data["schema"] == "EscalationEnvelope"
    assert data["schema_version"] == "escalation-envelope/1.0"
    assert data["tenant"] == "sincor"
    assert data["state"] == PENDING
    assert data["decision"] is None
    assert data["attestations"] == []
    assert set(data["risk"]) == {
        "violations",
        "exposure",
        "recommended_action",
        "required_signers",
    }
    assert data["risk"]["required_signers"] == []
    assert data["evidence_refs"] == ["doc:1"]
    assert data["action_choices"] == ["approve", "override", "escalate"]
    assert set(data["triggered_rule"]) == {"policy_id", "rule", "detail"}
    assert set(data["state_snapshot"]) == {"input_parameters", "dry_run_state"}


def test_envelope_traceability_fields(store):
    pkg = _raise(
        store,
        source_agent_id="agent-99",
        session_id="sess-1",
        trace_id="trace-abc",
    )
    data = pkg.to_dict()
    assert data["source_agent_id"] == "agent-99"
    assert data["source_context"] == {
        "surface": "test-surface",
        "session_id": "sess-1",
        "trace_id": "trace-abc",
    }


def test_source_agent_id_defaults_to_actor(store):
    pkg = _raise(store)
    assert pkg.source_agent_id == "agent-1"


def test_envelope_state_snapshot_carries_inputs_and_dry_run(store):
    pkg = _raise(
        store,
        input_parameters={"to": "0xabc", "amount_axm": 5},
        dry_run_state={"pool_after": 19995.0, "would_settle": True},
    )
    assert pkg.state_snapshot == {
        "input_parameters": {"to": "0xabc", "amount_axm": 5},
        "dry_run_state": {"pool_after": 19995.0, "would_settle": True},
    }
    clone = EscalationPackage.from_dict(pkg.to_dict())
    assert clone.state_snapshot == pkg.state_snapshot
    assert clone.triggered_rule == pkg.triggered_rule
    assert clone.source_context == pkg.source_context


def test_hard_gate_triggered_rule_stamped_on_envelope(store):
    pkg = _raise(store, category="merge")
    assert pkg.triggered_rule == {
        "policy_id": "hard-gate",
        "rule": "hard_gate_category",
        "detail": "category=merge",
    }


def test_detector_rule_hit_stamped_verbatim(store):
    pkg = _raise(
        store,
        category="funds",
        context={
            "rule_hit": {
                "policy_id": "sanctions-screen",
                "rule": "ofac-match",
                "detail": "counterparty on OFAC SDN list",
            }
        },
    )
    assert pkg.triggered_rule == {
        "policy_id": "sanctions-screen",
        "rule": "ofac-match",
        "detail": "counterparty on OFAC SDN list",
    }
    verdict = PolicyEngine.classify(
        "funds",
        {"rule_hit": {"policy_id": "sanctions-screen", "rule": "ofac-match"}},
    )
    assert verdict.verdict == ESCALATE
    assert verdict.reason == "rule_hit"


def test_spend_threshold_rule(store):
    verdict = PolicyEngine.classify("funds", {"amount": 25_000, "threshold": 10_000})
    assert verdict.verdict == ESCALATE
    assert verdict.policy_id == "spend-threshold"
    assert verdict.rule == "amount_exceeds_threshold"
    # At or below threshold: no spend rule fires (hard gate still does).
    at = PolicyEngine.classify("report", {"amount": 10_000, "threshold": 10_000})
    assert at.verdict == ALLOW
    pkg = _raise(store, context={"amount": 25_000, "threshold": 10_000})
    assert pkg.triggered_rule["policy_id"] == "spend-threshold"


def test_package_round_trip_through_dict(store):
    pkg = _raise(store)
    decided = store.decide(pkg.id, "approve", by="court", now=T1)
    clone = EscalationPackage.from_dict(decided.to_dict())
    assert clone == decided
    assert clone.decision == Decision(by="court", at=T1, kind="approve", note=None)


def test_new_package_records_created_and_classified_history(store):
    pkg = _raise(store, category="merge")
    events = [h["event"] for h in pkg.history]
    assert events == ["created", "classified"]
    assert "verdict=ESCALATE" in pkg.history[1]["detail"]
    assert "reason=hard_gate_category" in pkg.history[1]["detail"]


# ---------------------------------------------------------------------------
# Decision API + state machine
# ---------------------------------------------------------------------------


def test_approve_transitions_to_approved(store):
    pkg = _raise(store)
    decided = store.decide(pkg.id, "approve", by="court", note="ok", now=T1)
    assert decided.state == APPROVED
    assert decided.decision.kind == "approve"
    assert decided.decision.by == "court"
    assert decided.decision.at == T1
    assert store.get_package(pkg.id).state == APPROVED


def test_deny_transitions_to_denied(store):
    pkg = _raise(store)
    assert store.decide(pkg.id, "deny", by="court", now=T1).state == DENIED


def test_escalate_transitions_to_escalated(store):
    pkg = _raise(store)
    assert store.decide(pkg.id, "escalate", by="court", now=T1).state == ESCALATED


def test_expire_transitions_to_expired(store):
    pkg = _raise(store)
    assert store.decide(pkg.id, "expire", by="system", now=T1).state == EXPIRED


def test_override_with_note_transitions_to_overridden(store):
    pkg = _raise(store)
    decided = store.decide(
        pkg.id, "override", by="court", note="proceed at half size", now=T1
    )
    assert decided.state == OVERRIDDEN
    assert decided.decision.note == "proceed at half size"


@pytest.mark.parametrize("bad_note", [None, "", "   "])
def test_override_without_note_is_refused(store, bad_note):
    pkg = _raise(store)
    with pytest.raises(ValueError):
        store.decide(pkg.id, "override", by="court", note=bad_note, now=T1)
    # Refused override leaves the package untouched and still decidable.
    assert store.get_package(pkg.id).state == PENDING


@pytest.mark.parametrize(
    "first_kind,note",
    [
        ("approve", None),
        ("override", "noted"),
        ("escalate", None),
        ("deny", None),
        ("expire", None),
    ],
)
def test_terminal_states_reject_further_decisions(store, first_kind, note):
    pkg = _raise(store)
    store.decide(pkg.id, first_kind, by="court", note=note, now=T1)
    with pytest.raises(IllegalTransitionError):
        store.decide(pkg.id, "approve", by="court", now=T2)


def test_escalated_is_terminal_for_agents(store):
    pkg = _raise(store)
    store.decide(pkg.id, "escalate", by="operator", now=T1)
    with pytest.raises(IllegalTransitionError):
        store.decide(pkg.id, "approve", by="operator", now=T2)


def test_decide_unknown_package_raises(store):
    with pytest.raises(PackageNotFoundError):
        store.decide("esc_missing", "approve", by="court", now=T1)


def test_get_unknown_package_raises(store):
    with pytest.raises(PackageNotFoundError):
        store.get_package("esc_missing")
    assert store.find_package("esc_missing") is None


def test_decide_unknown_kind_raises(store):
    pkg = _raise(store)
    with pytest.raises(ValueError):
        store.decide(pkg.id, "shrug", by="court", now=T1)
    assert store.get_package(pkg.id).state == PENDING


def test_unknown_severity_rejected(store):
    with pytest.raises(ValueError):
        _raise(store, severity="catastrophic")


def test_list_pending_oldest_first_and_excludes_decided(store):
    first = _raise(store, now=T0)
    second = _raise(store, now=T1)
    third = _raise(store, now=T2)
    store.decide(second.id, "deny", by="court", now=T2)
    pending = store.list_pending()
    assert [p.id for p in pending] == [first.id, third.id]


def test_list_pending_tenant_filter(store):
    mine = _raise(store, tenant="sincor")
    _raise(store, tenant="other-tenant")
    assert [p.id for p in store.list_pending(tenant="sincor")] == [mine.id]
    assert len(store.list_pending()) == 2


def test_returned_packages_are_copies(store):
    pkg = _raise(store)
    pkg.state = APPROVED  # mutating the copy must not touch the store
    assert store.get_package(pkg.id).state == PENDING


# ---------------------------------------------------------------------------
# Gate hook
# ---------------------------------------------------------------------------


def test_gate_opens_only_for_approved_and_overridden(store):
    approved = _raise(store)
    store.decide(approved.id, "approve", by="court", now=T1)
    assert require_decision(store, approved.id) is True

    overridden = _raise(store)
    store.decide(overridden.id, "override", by="court", note="scoped", now=T1)
    assert require_decision(store, overridden.id) is True


@pytest.mark.parametrize(
    "kind,note",
    [("deny", None), ("escalate", None), ("expire", None)],
)
def test_gate_stays_shut_for_other_terminal_states(store, kind, note):
    pkg = _raise(store)
    store.decide(pkg.id, kind, by="court", note=note, now=T1)
    assert require_decision(store, pkg.id) is False


def test_gate_shut_for_pending_missing_and_none(store):
    pkg = _raise(store)
    assert require_decision(store, pkg.id) is False
    assert require_decision(store, "esc_missing") is False
    assert require_decision(store, None) is False
    assert require_decision(store, "") is False


# ---------------------------------------------------------------------------
# Store durability
# ---------------------------------------------------------------------------


def test_state_survives_reinstantiation(tmp_path):
    base = str(tmp_path / "esc")
    store = EscalationStore(base)
    pkg = _raise(store)
    store.decide(pkg.id, "approve", by="court", now=T1)

    reopened = EscalationStore(base)
    loaded = reopened.get_package(pkg.id)
    assert loaded.state == APPROVED
    assert loaded.decision.by == "court"
    assert require_decision(reopened, pkg.id) is True


def test_state_file_is_valid_json_after_every_write(store, tmp_path):
    pkg = _raise(store)
    state_file = tmp_path / "esc" / EscalationStore.STATE_FILENAME
    json.loads(state_file.read_text(encoding="utf-8"))
    store.decide(pkg.id, "deny", by="court", now=T1)
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data["packages"][pkg.id]["state"] == DENIED


def test_audit_log_records_every_event_in_order(store):
    pkg = _raise(store, category="treasury")
    store.decide(pkg.id, "approve", by="court", note="verified", now=T1)
    records = store.read_audit_log()
    assert [r["event"] for r in records] == [
        "created",
        "classified",
        "decision:approve",
    ]
    assert [r["seq"] for r in records] == [1, 2, 3]
    assert all(r["package_id"] == pkg.id for r in records)
    transition = records[-1]
    assert transition["from_state"] == PENDING
    assert transition["to_state"] == APPROVED
    assert transition["by"] == "court"
    assert transition["note"] == "verified"


def test_audit_log_is_append_only_across_instances(tmp_path):
    base = str(tmp_path / "esc")
    first_store = EscalationStore(base)
    pkg = _raise(first_store)
    second_store = EscalationStore(base)
    second_store.decide(pkg.id, "approve", by="court", now=T1)
    records = second_store.read_audit_log()
    assert [r["seq"] for r in records] == [1, 2, 3]


def test_corrupt_state_file_fails_closed(tmp_path):
    base = tmp_path / "esc"
    base.mkdir()
    (base / EscalationStore.STATE_FILENAME).write_text("{not json", "utf-8")
    with pytest.raises(StoreCorruptError):
        EscalationStore(str(base))


# ---------------------------------------------------------------------------
# Default-Deny posture
# ---------------------------------------------------------------------------


def test_default_deny_no_third_path(store):
    """Every category the engine knows reaches execution by exactly one
    of the two permitted paths — (a) deterministic policy ALLOW, or
    (b) a HITL decision on its envelope. Nothing else gets through."""
    from sincor2.escalation import ESCALATE as ESC, require_decision as gate

    all_categories = (
        sorted(HARD_GATE_CATEGORIES)
        + sorted(INTERNAL_CATEGORIES)
        + ["totally-unknown-category", ""]
    )
    for category in all_categories:
        verdict = PolicyEngine.classify(category)
        assert verdict.verdict in (ALLOW, ESC), category
        if verdict.verdict == ALLOW:
            # Path (a) exists ONLY for known reversible internal work.
            assert category in INTERNAL_CATEGORIES, category
        else:
            # Path (b): envelope raised, gate shut until a human decides.
            pkg = _raise(store, category=category or "merge")
            assert gate(store, pkg.id) is False, category
            store.decide(pkg.id, "approve", by="court", now=T1)
            assert gate(store, pkg.id) is True, category


def test_policy_engine_posture_is_named_default_deny():
    assert "Default-Deny" in PolicyEngine.__doc__
    import sincor2.escalation as esc_module

    assert "Default-Deny" in esc_module.__doc__


# ---------------------------------------------------------------------------
# Risk block
# ---------------------------------------------------------------------------


def test_risk_defaults_derived_from_verdict(store):
    pkg = _raise(store, category="merge")
    assert pkg.risk["violations"] == [
        {"policy_id": "hard-gate", "detail": "category=merge", "confidence": None}
    ]
    assert pkg.risk["exposure"] is None
    assert pkg.risk["recommended_action"] == "escalate"
    assert pkg.risk["required_signers"] == []


def test_risk_explicit_fields_and_rule_hit_confidence(store):
    pkg = _raise(
        store,
        category="funds",
        context={
            "rule_hit": {
                "policy_id": "sanctions-screen",
                "rule": "ofac-match",
                "detail": "sdn match",
                "confidence": 0.98,
            }
        },
        exposure={"amount": 500, "currency": "AXM"},
        recommended_action="deny",
        required_signers=["cfo"],
    )
    assert pkg.risk["violations"][0]["confidence"] == 0.98
    assert pkg.risk["violations"][0]["policy_id"] == "sanctions-screen"
    assert pkg.risk["exposure"] == {"amount": 500, "currency": "AXM"}
    assert pkg.risk["recommended_action"] == "deny"
    assert pkg.risk["required_signers"] == ["cfo"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"recommended_action": "whatever"},
        {"violations": [{"policy_id": "x", "confidence": 1.5}]},
        {"violations": [{"policy_id": "x", "confidence": -0.1}]},
        {"violations": [{"detail": "no policy id"}]},
        {"exposure": {"amount": 5}},
        {"required_signers": [""]},
    ],
)
def test_risk_validation_rejects_malformed(store, kwargs):
    with pytest.raises(ValueError):
        _raise(store, **kwargs)


# ---------------------------------------------------------------------------
# Co-sign quorum
# ---------------------------------------------------------------------------


def _raise_cosign(store, **overrides):
    overrides.setdefault("required_signers", ["cto", "cfo"])
    return _raise(store, category="funds", **overrides)


def test_cosign_single_decision_leaves_gate_shut(store):
    pkg = _raise_cosign(store)
    decided = store.decide(pkg.id, "approve", by="alex", role="cto", now=T1)
    assert decided.state == PENDING
    assert decided.decision is None
    assert len(decided.attestations) == 1
    assert require_decision(store, pkg.id) is False


def test_cosign_quorum_opens_gate(store):
    pkg = _raise_cosign(store)
    store.decide(pkg.id, "approve", by="alex", role="cto", now=T1)
    decided = store.decide(pkg.id, "approve", by="blair", role="cfo", now=T2)
    assert decided.state == APPROVED
    assert decided.decision.by == "blair"
    assert [a.role for a in decided.attestations] == ["cto", "cfo"]
    assert require_decision(store, pkg.id) is True


def test_cosign_same_actor_cannot_fill_two_roles(store):
    pkg = _raise_cosign(store)
    store.decide(pkg.id, "approve", by="alex", role="cto", now=T1)
    decided = store.decide(pkg.id, "approve", by="alex", role="cfo", now=T2)
    assert decided.state == PENDING
    assert require_decision(store, pkg.id) is False


def test_cosign_outsider_decision_never_counts(store):
    pkg = _raise_cosign(store)
    decided = store.decide(pkg.id, "approve", by="mallory", role="intern", now=T1)
    assert decided.state == PENDING
    store.decide(pkg.id, "approve", by="alex", role="cto", now=T1)
    decided = store.decide(pkg.id, "approve", by="blair", role="cfo", now=T2)
    assert decided.state == APPROVED
    assert [a.by for a in decided.attestations] == ["mallory", "alex", "blair"]


def test_cosign_conflicting_kinds_raise(store):
    pkg = _raise_cosign(store)
    store.decide(pkg.id, "approve", by="alex", role="cto", now=T1)
    with pytest.raises(ValueError):
        store.decide(pkg.id, "override", by="blair", role="cfo", note="x", now=T2)
    assert store.get_package(pkg.id).state == PENDING


def test_cosign_required_signer_deny_is_veto(store):
    pkg = _raise_cosign(store)
    store.decide(pkg.id, "approve", by="alex", role="cto", now=T1)
    decided = store.decide(pkg.id, "deny", by="blair", role="cfo", now=T2)
    assert decided.state == DENIED
    assert require_decision(store, pkg.id) is False


def test_decision_records_carry_actor_and_role(store):
    pkg = _raise(store)
    store.decide(pkg.id, "approve", by="court", role="founder", now=T1)
    loaded = store.get_package(pkg.id)
    assert loaded.decision.by == "court"
    assert loaded.decision.role == "founder"
    audit = [r for r in store.read_audit_log() if r["event"] == "decision:approve"]
    assert audit[0]["by"] == "court"
    assert audit[0]["role"] == "founder"


# ---------------------------------------------------------------------------
# Hash-chained audit log
# ---------------------------------------------------------------------------


def test_audit_chain_genesis_and_linkage(store):
    pkg = _raise(store)
    store.decide(pkg.id, "approve", by="court", now=T1)
    records = store.read_audit_log()
    assert records[0]["prev_hash"] == "0" * 64
    for prev, cur in zip(records, records[1:]):
        assert cur["prev_hash"] == prev["record_hash"]
    assert all(len(r["record_hash"]) == 64 for r in records)
    assert store.verify_audit_chain() is True
    assert verify_chain(records) is True


def test_audit_chain_tamper_detected(store):
    pkg = _raise(store)
    store.decide(pkg.id, "approve", by="court", note="ok", now=T1)
    records = store.read_audit_log()
    tampered = [dict(r) for r in records]
    tampered[1]["note"] = "forged"
    assert verify_chain(tampered) is False
    reordered = [records[1], records[0]] + records[2:]
    assert verify_chain(reordered) is False
    assert verify_chain(records) is True  # original untouched


def test_audit_chain_continues_across_instances(tmp_path):
    base = str(tmp_path / "esc")
    first = EscalationStore(base)
    pkg = _raise(first)
    second = EscalationStore(base)
    second.decide(pkg.id, "deny", by="court", now=T1)
    records = second.read_audit_log()
    assert [r["seq"] for r in records] == [1, 2, 3]
    assert second.verify_audit_chain() is True
