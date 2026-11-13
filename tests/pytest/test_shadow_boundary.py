"""Tests for the Phase 1 shadow-mode boundary enforcement.

Verifies: every adapter type is blocked in shadow mode, proposed actions use
would_* verbs, violations carry evidence, credentials reject secrets, the
heartbeat/check_effective_mode expose drift, the kill switch blocks,
ACTUAL_SIDE_EFFECTS stays 0, and unwrapped adapters are detected.
"""

import threading

import pytest

from sincor2.shadow_monitor.boundary import (
    ACTUAL_SIDE_EFFECTS,
    ActionKind,
    ContractCallAdapter,
    CredentialValidationError,
    CrmAdapter,
    EffectiveModeError,
    EmailAdapter,
    KillSwitch,
    ProposedAction,
    ShadowBoundary,
    ShadowBoundaryViolation,
    ShadowCredentials,
    SocialAdapter,
    TradeAdapter,
    TransferAdapter,
    _perform_live_side_effect,
    check_effective_mode,
    emit_heartbeat,
)

ADAPTER_CLASSES = [
    EmailAdapter,
    SocialAdapter,
    CrmAdapter,
    TradeAdapter,
    TransferAdapter,
    ContractCallAdapter,
]


def make_boundary(**kwargs):
    kwargs.setdefault("kill_switch", KillSwitch())  # isolated per test
    return ShadowBoundary(**kwargs)


# ---------------------------------------------------------------------------
# 1. Every adapter type is blocked in shadow mode
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("adapter_cls", ADAPTER_CLASSES)
def test_every_adapter_live_action_blocked_in_shadow(adapter_cls):
    boundary = make_boundary()
    adapter = adapter_cls(boundary)
    with pytest.raises(ShadowBoundaryViolation):
        adapter.live_action(
            target=f"{adapter_cls.adapter_kind}-target",
            payload_summary="redacted summary",
            estimated_cost=1.0,
            trace_id="t-1",
        )
    assert len(boundary.violations()) == 1


# ---------------------------------------------------------------------------
# 2. Proposed-action recording uses would_* verbs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("adapter_cls", ADAPTER_CLASSES)
def test_shadow_action_records_would_verbs(adapter_cls):
    boundary = make_boundary()
    adapter = adapter_cls(boundary)
    action = adapter.shadow_action(
        target="some-target", payload_summary="redacted", trace_id="t-would"
    )
    assert action.action_kind.value.startswith("would_")
    assert "sent" not in action.action_kind.value
    assert "updated" not in action.action_kind.value
    assert "settled" not in action.action_kind.value
    assert action.trace_id == "t-would"


def test_proposed_action_verbs_are_the_three_allowed():
    allowed = {ActionKind.WOULD_SEND, ActionKind.WOULD_UPDATE, ActionKind.WOULD_PAY}
    for kind in ActionKind:
        assert kind in allowed


def test_adapter_kind_maps_to_expected_verb():
    boundary = make_boundary()
    assert EmailAdapter(boundary).action_kind is ActionKind.WOULD_SEND
    assert SocialAdapter(boundary).action_kind is ActionKind.WOULD_SEND
    assert CrmAdapter(boundary).action_kind is ActionKind.WOULD_UPDATE
    assert TradeAdapter(boundary).action_kind is ActionKind.WOULD_PAY
    assert TransferAdapter(boundary).action_kind is ActionKind.WOULD_PAY
    assert ContractCallAdapter(boundary).action_kind is ActionKind.WOULD_PAY


# ---------------------------------------------------------------------------
# 3. ShadowBoundaryViolation raised with evidence preserved
# ---------------------------------------------------------------------------


def test_violation_carries_proposed_action_evidence():
    boundary = make_boundary()
    adapter = TradeAdapter(boundary)
    with pytest.raises(ShadowBoundaryViolation) as exc_info:
        adapter.live_action(target="0xdeadbeef", payload_summary="swap 10 AXM", trace_id="t-ev")
    violation = exc_info.value
    assert violation.proposed_action is not None
    assert isinstance(violation.proposed_action, ProposedAction)
    assert violation.proposed_action.action_kind is ActionKind.WOULD_PAY
    assert violation.proposed_action.target == "0xdeadbeef"
    assert violation.proposed_action.trace_id == "t-ev"
    assert violation.recorded_at > 0


def test_violation_hook_called():
    seen = []
    boundary = make_boundary(on_violation=seen.append)
    with pytest.raises(ShadowBoundaryViolation):
        EmailAdapter(boundary).live_action(target="a@b.c", payload_summary="x", trace_id="t-h")
    assert len(seen) == 1
    assert isinstance(seen[0], ShadowBoundaryViolation)


# ---------------------------------------------------------------------------
# 4. Shadow credentials reject private keys
# ---------------------------------------------------------------------------


def test_credentials_reject_hex_private_key():
    with pytest.raises(CredentialValidationError):
        ShadowCredentials(credential_label="0x" + "ab" * 32)


def test_credentials_reject_pem_private_key():
    with pytest.raises(CredentialValidationError):
        ShadowCredentials(
            credential_label="-----BEGIN PRIVATE KEY-----\nMIIB...\n-----END PRIVATE KEY-----"
        )


def test_credentials_reject_secret_key_prefix():
    with pytest.raises(CredentialValidationError):
        ShadowCredentials(credential_label="sk-live-abc123XYZ")


def test_credentials_reject_long_base64_blob():
    with pytest.raises(CredentialValidationError):
        ShadowCredentials(credential_label="dGhpcyBpcyBhIHZlcnkgbG9uZyBiYXNlNjQgc2VjcmV0IGtleSBibG9i")


def test_credentials_require_read_only_scope():
    with pytest.raises(CredentialValidationError):
        ShadowCredentials(credential_label="deployer-label", scope="read-write")


def test_credentials_accept_plain_labels():
    creds = ShadowCredentials(credential_label="shadow-read-only")
    assert creds.scope == "read-only"
    creds.validate()  # no raise


def test_adapter_construction_validates_credentials():
    boundary = make_boundary()
    # A forged instance bypassing __post_init__ (carrying an actual hex key)
    # fails when the adapter validates it at construction.
    forged = ShadowCredentials.__new__(ShadowCredentials)
    object.__setattr__(forged, "credential_label", "0x" + "cd" * 32)
    object.__setattr__(forged, "scope", "read-only")
    with pytest.raises(CredentialValidationError):
        EmailAdapter(boundary, credentials=forged)
    # And direct construction with a key-like label is rejected outright.
    with pytest.raises(CredentialValidationError):
        EmailAdapter(
            boundary,
            credentials=ShadowCredentials(credential_label="sk-test-abc123XYZ"),
        )


# ---------------------------------------------------------------------------
# 5. Heartbeat shows shadow mode
# ---------------------------------------------------------------------------


def test_heartbeat_reports_shadow_mode():
    boundary = make_boundary()
    hb = emit_heartbeat(boundary)
    assert hb["effective_mode"] == "shadow"
    assert hb["policy_version"] == "shadow-boundary/v1"
    assert isinstance(hb["enabled_capabilities"], list)
    assert hb["kill_switch_state"] == "disarmed"
    assert hb["timestamp"] > 0


def test_heartbeat_reflects_kill_switch_state():
    ks = KillSwitch()
    boundary = make_boundary(kill_switch=ks)
    ks.engage()
    assert emit_heartbeat(boundary)["kill_switch_state"] == "armed"
    ks.disengage()
    assert emit_heartbeat(boundary)["kill_switch_state"] == "disarmed"


# ---------------------------------------------------------------------------
# 6. check_effective_mode alerts on non-shadow
# ---------------------------------------------------------------------------


def test_check_effective_mode_alerts_on_live_boundary():
    boundary = make_boundary(shadow=False)
    alerts = []
    with pytest.raises(EffectiveModeError) as exc_info:
        check_effective_mode(boundary, on_alert=alerts.append)
    assert "not 'shadow'" in str(exc_info.value)
    assert len(alerts) == 1
    assert "not 'shadow'" in alerts[0]


def test_check_effective_mode_ok_in_shadow():
    boundary = make_boundary()
    for cls in ADAPTER_CLASSES:
        cls(boundary)
    result = check_effective_mode(boundary)
    assert result["effective_mode"] == "shadow"


# ---------------------------------------------------------------------------
# 7. Kill switch blocks proposed-action recording for high-risk kinds
# ---------------------------------------------------------------------------


def test_kill_switch_blocks_would_pay_recording():
    ks = KillSwitch()
    boundary = make_boundary(kill_switch=ks)
    ks.engage()
    adapter = TradeAdapter(boundary)
    with pytest.raises(ShadowBoundaryViolation) as exc_info:
        adapter.shadow_action(target="0xpool", payload_summary="swap", trace_id="t-ks")
    assert "kill switch" in str(exc_info.value).lower()
    assert boundary.proposed_actions() == []


def test_kill_switch_default_disarmed_records_normally():
    boundary = make_boundary()
    action = CrmAdapter(boundary).shadow_action(
        target="contact-1", payload_summary="field change", trace_id="t-ks2"
    )
    assert action in boundary.proposed_actions()


def test_kill_switch_is_thread_safe():
    ks = KillSwitch()
    errors = []

    def toggle():
        try:
            for _ in range(200):
                ks.engage()
                ks.disengage()
        except Exception as e:  # pragma: no cover - diagnostic
            errors.append(e)

    threads = [threading.Thread(target=toggle) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert ks.state() in ("armed", "disarmed")
    assert ks.engagement_count() == 8 * 200


# ---------------------------------------------------------------------------
# 8. ACTUAL_SIDE_EFFECTS stays 0
# ---------------------------------------------------------------------------


def test_actual_side_effects_stays_zero_in_shadow():
    import sincor2.shadow_monitor.boundary as mod

    boundary = make_boundary()
    for cls in ADAPTER_CLASSES:
        adapter = cls(boundary)
        adapter.shadow_action(target="t", payload_summary="s", trace_id="z")
        with pytest.raises(ShadowBoundaryViolation):
            adapter.live_action(target="t", payload_summary="s", trace_id="z")
    assert mod.ACTUAL_SIDE_EFFECTS == 0


def test_live_only_path_unreachable_in_shadow():
    import sincor2.shadow_monitor.boundary as mod

    before = mod.ACTUAL_SIDE_EFFECTS
    with pytest.raises(ShadowBoundaryViolation):
        _perform_live_side_effect("attempt", shadow=True)
    assert mod.ACTUAL_SIDE_EFFECTS == before == 0


# ---------------------------------------------------------------------------
# 9. Unauthorized (unwrapped) adapter detected
# ---------------------------------------------------------------------------


def test_unwrapped_adapter_kind_detected():
    class RogueAdapter:
        adapter_kind = "rogue-unwrapped"
        action_kind = ActionKind.WOULD_PAY

    assert not ShadowBoundary.is_wrapped(RogueAdapter.adapter_kind)
    boundary = make_boundary()
    with pytest.raises(EffectiveModeError) as exc_info:
        check_effective_mode(
            boundary, expected_adapter_kinds=["rogue-unwrapped"]
        )
    assert "rogue-unwrapped" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 10. Boundary bookkeeping
# ---------------------------------------------------------------------------


def test_proposed_actions_are_immutable_evidence():
    boundary = make_boundary()
    action = EmailAdapter(boundary).shadow_action(
        target="a@b.c", payload_summary="newsletter", trace_id="t-imm"
    )
    with pytest.raises(Exception):
        action.target = "mutated"  # frozen dataclass
    assert boundary.proposed_actions()[0].target == "a@b.c"


def test_violation_and_proposal_lists_are_thread_safe():
    boundary = make_boundary()
    adapter = EmailAdapter(boundary)

    def work():
        for i in range(50):
            adapter.shadow_action(target="t", payload_summary="s", trace_id=f"th-{i}")
            try:
                adapter.live_action(target="t", payload_summary="s", trace_id=f"th-{i}")
            except ShadowBoundaryViolation:
                pass

    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(boundary.proposed_actions()) == 200
    assert len(boundary.violations()) == 200


# ---------------------------------------------------------------------------
# 11. EffectIntent: frozen, hash-only payload evidence
# ---------------------------------------------------------------------------

import dataclasses
import hashlib
import importlib.util
import inspect
import json
import sys
import types

from sincor2.shadow_monitor.effect_boundary import (
    EFFECT_TYPES,
    RECEIPT_STATUSES,
    AuditFailureError,
    EffectIntent,
    EffectReceipt,
    IdempotencyConflict,
    ImportBoundaryViolation,
    LogOnlyAdapter,
    ShadowEffectBoundary,
    assert_no_live_import,
    build_effect_boundary,
    canonical_intent_hash,
    canonical_payload_hash,
    guard_live_adapter_import,
    verify_approval,
)


def make_effect_boundary(**kwargs):
    kwargs.setdefault("kill_switch", KillSwitch())  # isolated per test
    return ShadowEffectBoundary(**kwargs)


def make_intent(**overrides):
    defaults = dict(
        effect_type="email.send",
        target="ops@example.com",
        payload={"to": "ops@example.com", "subject": "hello", "body": "s3cr3t-body"},
        idempotency_key="key-1",
        trace_id="trace-1",
        agent_id="agent-1",
        tenant="tenant-1",
        risk_tier="low",
        estimated_cost=0.0,
    )
    defaults.update(overrides)
    return EffectIntent.from_payload(**defaults)


def test_effect_intent_hash_is_sha256_of_canonical_json():
    payload = {"b": 2, "a": 1, "nested": {"z": [3, 2, 1]}}
    intent = make_intent(payload=payload)
    expected = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert intent.payload_hash == expected
    assert canonical_payload_hash(payload) == expected
    # Key order must not change the hash (canonical form).
    assert canonical_payload_hash({"a": 1, "b": 2}) == canonical_payload_hash({"b": 2, "a": 1})


def test_effect_intent_never_carries_full_payload():
    intent = make_intent()
    dumped = json.dumps(dataclasses.asdict(intent))
    assert "s3cr3t-body" not in dumped  # the literal payload is nowhere in the intent
    assert "payload" not in dataclasses.asdict(intent)  # only payload_hash exists
    assert len(intent.payload_hash) == 64  # sha256 hex


def test_effect_intent_is_frozen():
    intent = make_intent()
    with pytest.raises(Exception):
        intent.target = "mutated"  # frozen dataclass
    with pytest.raises(Exception):
        intent.payload_hash = "00" * 32


def test_effect_intent_rejects_unknown_effect_type():
    with pytest.raises(ValueError):
        make_intent(effect_type="rocket.launch")


def test_effect_intent_rejects_unknown_risk_tier():
    with pytest.raises(ValueError):
        make_intent(risk_tier="yolo")


def test_effect_receipt_executed_true_rejected():
    with pytest.raises(ValueError):
        EffectReceipt(effect_id="x", status="would_execute", executed=True)


def test_effect_receipt_status_restricted_to_shadow_literals():
    assert set(RECEIPT_STATUSES) == {"would_execute", "blocked_policy", "pending_approval"}
    for forbidden in ("sent", "settled", "delivered"):
        assert forbidden not in RECEIPT_STATUSES
        with pytest.raises(ValueError):
            EffectReceipt(effect_id="x", status=forbidden)


# ---------------------------------------------------------------------------
# 12. dispatch: policy evaluation, audit, queue, receipt
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("effect_type", sorted(EFFECT_TYPES))
def test_every_effect_type_stops_in_shadow(effect_type):
    boundary = make_effect_boundary()
    intent = make_intent(effect_type=effect_type, idempotency_key=f"k-{effect_type}")
    receipt = boundary.dispatch(intent)
    assert receipt.executed is False
    assert receipt.status in RECEIPT_STATUSES
    assert receipt.effect_id == intent.effect_id
    assert boundary.proposal_queue[0] is intent or boundary.proposal_queue[0] == intent
    assert len(boundary.proposal_queue) == 1


@pytest.mark.parametrize("tier", ["high", "critical"])
def test_default_policy_denies_high_and_critical(tier):
    boundary = make_effect_boundary()
    receipt = boundary.dispatch(make_intent(risk_tier=tier, idempotency_key=f"deny-{tier}"))
    assert receipt.status == "blocked_policy"
    assert receipt.executed is False
    assert any("denied" in code for code in receipt.policy_reason_codes)


def test_policy_denial_stays_denied_across_retries():
    boundary = make_effect_boundary()
    first = boundary.dispatch(make_intent(risk_tier="high", idempotency_key="deny-retry"))
    # Identical retry returns the ORIGINAL denial receipt -- no promotion, no dup.
    second = boundary.dispatch(make_intent(risk_tier="high", idempotency_key="deny-retry"))
    assert first.status == "blocked_policy"
    assert second.status == "blocked_policy"
    assert second.effect_id == first.effect_id
    assert len(boundary.proposal_queue) == 1
    assert len(boundary.audit_log) == 1


def test_default_policy_medium_with_cost_needs_approval():
    boundary = make_effect_boundary()
    receipt = boundary.dispatch(
        make_intent(risk_tier="medium", estimated_cost=25.0, idempotency_key="med-cost")
    )
    assert receipt.status == "pending_approval"
    assert receipt.executed is False


def test_default_policy_low_allows_as_would_execute():
    boundary = make_effect_boundary()
    receipt = boundary.dispatch(make_intent(risk_tier="low", idempotency_key="low-ok"))
    assert receipt.status == "would_execute"
    assert receipt.executed is False


def test_dispatch_unknown_effect_type_raises_valueerror():
    boundary = make_effect_boundary()
    intent = make_intent()
    object.__setattr__(intent, "effect_type", "rocket.launch")  # bypass frozen ctor check
    with pytest.raises(ValueError):
        boundary.dispatch(intent)
    assert boundary.proposal_queue == []
    assert boundary.audit_log == []


def test_missing_policy_result_becomes_blocked_policy():
    boundary = make_effect_boundary(policy_fn=lambda intent: None)
    receipt = boundary.dispatch(make_intent(risk_tier="low", idempotency_key="badpol-1"))
    assert receipt.status == "blocked_policy"
    assert receipt.policy_reason_codes == ["policy_fn_invalid_result"]


def test_malformed_policy_result_becomes_blocked_policy():
    boundary = make_effect_boundary(policy_fn=lambda intent: ("sent", []))
    receipt = boundary.dispatch(make_intent(risk_tier="low", idempotency_key="badpol-2"))
    assert receipt.status == "blocked_policy"


def test_raising_policy_fn_becomes_blocked_policy():
    def boom(intent):
        raise RuntimeError("policy service down")

    boundary = make_effect_boundary(policy_fn=boom)
    receipt = boundary.dispatch(make_intent(risk_tier="low", idempotency_key="badpol-3"))
    assert receipt.status == "blocked_policy"
    assert any("policy_fn_error" in code for code in receipt.policy_reason_codes)


def test_audit_event_carries_hash_not_payload():
    boundary = make_effect_boundary()
    boundary.dispatch(make_intent(idempotency_key="audit-1"))
    (event,) = boundary.audit_log
    assert event["payload_hash"] == make_intent(idempotency_key="audit-1").payload_hash
    assert "payload" not in event  # only payload_hash
    assert "s3cr3t-body" not in json.dumps(event)
    assert event["decision"] == "would_execute"


def test_audit_failure_raises_and_issues_no_receipt():
    class BrokenLog(list):
        def append(self, item):
            raise OSError("disk full")

    boundary = make_effect_boundary(audit_log=BrokenLog())
    with pytest.raises(AuditFailureError):
        boundary.dispatch(make_intent(idempotency_key="audit-fail"))
    # Fail-closed: no receipt issued, nothing queued, nothing remembered.
    assert boundary.proposal_queue == []
    assert boundary.seen_idempotency_keys() == []


# ---------------------------------------------------------------------------
# 13. Kill switch blocks would-pay dispatch
# ---------------------------------------------------------------------------


def test_kill_switch_blocks_would_pay_dispatch():
    ks = KillSwitch()
    boundary = make_effect_boundary(kill_switch=ks)
    ks.engage()
    receipt = boundary.dispatch(
        make_intent(effect_type="payment.transfer", risk_tier="low", idempotency_key="ks-pay")
    )
    assert receipt.status == "blocked_policy"
    assert receipt.executed is False
    assert "kill_switch_engaged_would_pay_blocked" in receipt.policy_reason_codes
    # Non-value-moving kinds are unaffected by the kill switch.
    email_receipt = boundary.dispatch(
        make_intent(effect_type="email.send", risk_tier="low", idempotency_key="ks-mail")
    )
    assert email_receipt.status == "would_execute"


def test_kill_switch_disengaged_allows_low_risk_payment():
    boundary = make_effect_boundary()
    receipt = boundary.dispatch(
        make_intent(effect_type="payment.transfer", risk_tier="low", idempotency_key="ks-off")
    )
    assert receipt.status == "would_execute"


# ---------------------------------------------------------------------------
# 14. Idempotency
# ---------------------------------------------------------------------------


def test_idempotent_retry_returns_original_no_duplicate():
    boundary = make_effect_boundary()
    first = boundary.dispatch(make_intent(idempotency_key="idem-1"))
    # A *different* intent object with the same key and identical payload.
    retry = make_intent(idempotency_key="idem-1")
    assert retry.effect_id != first.effect_id  # distinct intent objects...
    second = boundary.dispatch(retry)
    assert second.effect_id == first.effect_id  # ...but the ORIGINAL receipt returns
    assert second is first
    assert len(boundary.proposal_queue) == 1
    assert len(boundary.audit_log) == 1


def test_idempotency_conflict_on_different_payload():
    boundary = make_effect_boundary()
    boundary.dispatch(make_intent(idempotency_key="idem-2", payload={"v": 1}))
    with pytest.raises(IdempotencyConflict):
        boundary.dispatch(make_intent(idempotency_key="idem-2", payload={"v": 2}))
    assert len(boundary.proposal_queue) == 1  # conflicting retry queued nothing


def test_idempotency_scoped_per_tenant_and_effect_type():
    boundary = make_effect_boundary()
    boundary.dispatch(make_intent(idempotency_key="shared", tenant="t-a"))
    boundary.dispatch(make_intent(idempotency_key="shared", tenant="t-b"))
    boundary.dispatch(
        make_intent(idempotency_key="shared", tenant="t-a", effect_type="social.post")
    )
    assert len(boundary.proposal_queue) == 3  # separate scopes, separate entries


# ---------------------------------------------------------------------------
# 15. Approval hash / edited-proposal invalidation
# ---------------------------------------------------------------------------


def test_verify_approval_true_for_untampered_intent():
    boundary = make_effect_boundary()
    intent = make_intent(idempotency_key="appr-1")
    receipt = boundary.dispatch(intent)
    assert receipt.approval_hash == canonical_intent_hash(intent)
    assert verify_approval(intent, receipt) is True


def test_edited_proposal_invalidates_approval():
    boundary = make_effect_boundary()
    intent = make_intent(idempotency_key="appr-2")
    receipt = boundary.dispatch(intent)
    # Any field change -- here the target -- must invalidate.
    edited = dataclasses.replace(intent, target="attacker@evil.example")
    assert verify_approval(edited, receipt) is False
    # And a hash-mismatch on risk tier too.
    edited2 = dataclasses.replace(intent, risk_tier="critical")
    assert verify_approval(edited2, receipt) is False


def test_verify_approval_false_without_approval_hash():
    intent = make_intent(idempotency_key="appr-3")
    receipt = EffectReceipt(effect_id=intent.effect_id, status="blocked_policy", approval_hash=None)
    assert verify_approval(intent, receipt) is False


# ---------------------------------------------------------------------------
# 16. Factory, import-boundary guards, structural controls
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("profile", [None, "", "shadow"])
def test_build_effect_boundary_shadow_profiles(profile):
    boundary = build_effect_boundary(profile)
    assert isinstance(boundary, ShadowEffectBoundary)


def test_build_effect_boundary_live_raises_runtimeerror():
    with pytest.raises(RuntimeError) as exc_info:
        build_effect_boundary("live")
    assert "separately deployed executor" in str(exc_info.value)


def test_build_effect_boundary_unknown_profile_raises_valueerror():
    with pytest.raises(ValueError):
        build_effect_boundary("staging")


def test_assert_no_live_import_passes_clean():
    assert_no_live_import()  # no raise in a clean worker process


def test_assert_no_live_import_detects_live_module():
    fake = types.ModuleType("sincor2.shadow_monitor.live_executor")
    sys.modules["sincor2.shadow_monitor.live_executor"] = fake
    try:
        with pytest.raises(ImportBoundaryViolation):
            assert_no_live_import()
    finally:
        del sys.modules["sincor2.shadow_monitor.live_executor"]
    assert_no_live_import()  # clean again afterwards


@pytest.mark.parametrize(
    "name",
    [
        "sincor2.shadow_monitor.live_executor",
        "sincor2.live_adapter",
        "LIVE_EXECUTOR",
    ],
)
def test_guard_live_adapter_import_raises(name):
    with pytest.raises(ImportBoundaryViolation):
        guard_live_adapter_import(name)


def test_guard_live_adapter_import_allows_shadow_names():
    guard_live_adapter_import("sincor2.shadow_monitor.boundary")
    guard_live_adapter_import("sincor2.shadow_monitor.effect_boundary")


def test_importlib_cannot_resolve_live_executor():
    # The live executor is a separate deployment: it is not on this path.
    assert importlib.util.find_spec("sincor2.shadow_monitor.live_executor") is None
    with pytest.raises(ImportBoundaryViolation):
        guard_live_adapter_import("sincor2.shadow_monitor.live_executor")


def test_no_live_destinations_in_boundary_source():
    source = inspect.getsource(ShadowEffectBoundary)
    for token in ("live_executor", "live_adapter", "live_queue", "live_dispatch"):
        assert token not in source
    # The proposal queue is a plain list -- there is no live queue to drain into.
    assert isinstance(make_effect_boundary().proposal_queue, list)


# ---------------------------------------------------------------------------
# 17. Log-only adapter (provider-disabled)
# ---------------------------------------------------------------------------


def test_log_only_adapter_execute_returns_would_execute():
    adapter = LogOnlyAdapter(make_effect_boundary())
    receipt = adapter.execute(make_intent(idempotency_key="logonly-1"))
    assert receipt.status == "would_execute"
    assert receipt.executed is False


def test_log_only_adapter_delivery_report_never_claims_sent():
    adapter = LogOnlyAdapter(make_effect_boundary())
    adapter.execute(make_intent(idempotency_key="logonly-2"))
    report = adapter.delivery_report()
    assert report == {"status": "not sent", "reason": "log-only"}
    assert report["status"] == "not sent"
    assert report["status"] != "sent"
    assert "delivered" not in report.values()


# ---------------------------------------------------------------------------
# 18. Restart / retry / kill-switch promotion resistance
# ---------------------------------------------------------------------------


def test_restart_from_persisted_queue_cannot_promote_to_live():
    boundary = make_effect_boundary()
    intent = make_intent(idempotency_key="persist-1")
    receipt = boundary.dispatch(intent)
    snapshot = boundary.snapshot()
    persisted = json.loads(json.dumps(snapshot))  # round-trip through JSON, like disk

    # Tampered snapshot claiming executed=True must be refused at restore.
    persisted["receipts"][0]["executed"] = True
    with pytest.raises(ValueError):
        ShadowEffectBoundary.restore(persisted)


def test_restore_preserves_shadow_invariant_and_idempotency():
    boundary = make_effect_boundary()
    intent = make_intent(idempotency_key="persist-2")
    original = boundary.dispatch(intent)
    persisted = json.loads(json.dumps(boundary.snapshot()))

    restored = ShadowEffectBoundary.restore(persisted)
    assert len(restored.proposal_queue) == 1
    (restored_receipt,) = [
        r for _, r in [restored._seen[k] for k in restored.seen_idempotency_keys()]
    ]
    assert restored_receipt.executed is False
    assert restored_receipt.status == original.status
    # Retry after restart returns the original receipt -- still would_execute, never sent.
    retry = restored.dispatch(make_intent(idempotency_key="persist-2"))
    assert retry.effect_id == original.effect_id
    assert retry.status == "would_execute"
    assert retry.executed is False
    assert len(restored.proposal_queue) == 1


def test_retry_same_key_returns_would_execute_not_sent():
    boundary = make_effect_boundary()
    first = boundary.dispatch(make_intent(idempotency_key="retry-1"))
    second = boundary.dispatch(make_intent(idempotency_key="retry-1"))
    assert second.status == "would_execute"
    assert "sent" not in second.status
    assert second.executed is False
    assert first is second


def test_kill_switch_engaged_then_dispatch_yields_blocked_policy():
    ks = KillSwitch()
    boundary = make_effect_boundary(kill_switch=ks)
    ks.engage()
    for effect_type in ("payment.transfer", "trade.swap", "contract.call"):
        receipt = boundary.dispatch(
            make_intent(
                effect_type=effect_type,
                risk_tier="low",
                idempotency_key=f"ks-{effect_type}",
            )
        )
        assert receipt.status == "blocked_policy"
        assert receipt.executed is False
