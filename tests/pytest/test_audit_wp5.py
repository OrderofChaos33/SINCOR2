"""WP5 audit trail tests.

(a) audit chain tamper detection
(b) audit store failure -> AuditFailureError (fail-closed)
(c) lifecycle records: all six kinds, redacted payloads
(d) require_audit_available gate for consequential actions
"""

import pytest

from sincor2.shadow_monitor.audit import (
    AUDIT_RECORD_KINDS,
    AuditChainTampered,
    AuditStore,
    is_consequential,
    redacted_hash,
    require_audit_available,
)
from sincor2.shadow_monitor.effect_boundary import AuditFailureError


def _effect(effect_type="email.send", risk="low"):
    return {"effect_type": effect_type, "risk_tier": risk}


def test_six_lifecycle_kinds_defined():
    assert set(AUDIT_RECORD_KINDS) == {
        "proposal",
        "policy_decision",
        "approval",
        "adapter_attempt",
        "provider_ack",
        "final_state",
    }


def test_append_and_verify_chain():
    store = AuditStore()
    for kind in AUDIT_RECORD_KINDS:
        store.append(
            kind=kind,
            effect_id="eff-1",
            effect_type="email.send",
            originator="agent",
            payload={"to": "user@example.com", "body": "secret content"},
            detail=f"{kind} recorded",
        )
    assert store.verify_chain() is True
    assert store.lifecycle_complete("eff-1") is True


def test_payload_is_redacted_never_stored():
    store = AuditStore()
    secret = "super-secret-pii-value-12345"
    record = store.append(
        kind="proposal",
        effect_id="eff-2",
        effect_type="email.send",
        originator="agent",
        payload={"body": secret},
    )
    # The raw secret must not appear anywhere in the stored record.
    import dataclasses

    blob = str(dataclasses.asdict(record))
    assert secret not in blob
    # But the hash is deterministic for the same payload.
    assert record.payload_hash == redacted_hash({"body": secret})


def test_chain_tamper_detection():
    store = AuditStore()
    store.append(kind="proposal", effect_id="eff-3", detail="first")
    store.append(kind="policy_decision", effect_id="eff-3", detail="second")
    # Tamper: mutate the second record's detail in place.
    second = store.records()[1]
    object.__setattr__(second, "detail", "forged detail")
    with pytest.raises(AuditChainTampered):
        store.verify_chain()


def test_chain_prev_hash_tamper_detection():
    store = AuditStore()
    store.append(kind="proposal", effect_id="eff-4", detail="first")
    r = store.append(kind="policy_decision", effect_id="eff-4", detail="second")
    object.__setattr__(r, "prev_hash", "forged-prev")
    with pytest.raises(AuditChainTampered):
        store.verify_chain()


def test_store_failure_raises_audit_failure_error():
    store = AuditStore()
    store.mark_unavailable("disk full (simulated)")
    with pytest.raises(AuditFailureError):
        store.append(kind="proposal", effect_id="eff-5", detail="should fail")


def test_persist_fn_failure_marks_unavailable():
    def bad_persist(record):
        raise OSError("disk gone")

    store = AuditStore(persist_fn=bad_persist)
    with pytest.raises(AuditFailureError):
        store.append(kind="proposal", effect_id="eff-6", detail="x")
    assert store.available() is False


def test_is_consequential():
    assert is_consequential("payment.transfer", "low") is True
    assert is_consequential("trade.swap", "low") is True
    assert is_consequential("contract.call", "low") is True
    assert is_consequential("email.send", "high") is True
    assert is_consequential("email.send", "critical") is True
    assert is_consequential("email.send", "low") is False
    assert is_consequential("email.send", "medium") is False


def test_require_audit_available_passes_for_non_consequential():
    # Non-consequential actions don't need the gate.
    require_audit_available(None, effect_type="email.send", risk_tier="low")


def test_require_audit_available_fails_closed():
    store = AuditStore()
    store.mark_unavailable("outage")
    with pytest.raises(AuditFailureError):
        require_audit_available(
            store, effect_type="payment.transfer", risk_tier="low"
        )
    with pytest.raises(AuditFailureError):
        require_audit_available(None, effect_type="email.send", risk_tier="high")


def test_require_audit_available_passes_when_healthy():
    store = AuditStore()
    require_audit_available(store, effect_type="payment.transfer", risk_tier="low")


def test_boundary_dispatch_fails_closed_on_store_outage():
    """Consequential dispatch with dead stores -> AuditFailureError."""
    from sincor2.shadow_monitor.effect_boundary import (
        EffectIntent,
        ShadowEffectBoundary,
    )

    boundary = ShadowEffectBoundary(
        store_health_fn=lambda: {"policy": False, "idempotency": True, "audit": True}
    )
    intent = EffectIntent.from_payload(
        effect_type="payment.transfer",
        target="ci://test",
        payload={"amount": "1"},
        idempotency_key="idem-wp5-1",
        risk_tier="low",
    )
    with pytest.raises(AuditFailureError, match="fail-closed"):
        boundary.dispatch(intent)


def test_boundary_dispatch_ok_when_stores_healthy():
    from sincor2.shadow_monitor.effect_boundary import (
        EffectIntent,
        ShadowEffectBoundary,
    )

    boundary = ShadowEffectBoundary(
        store_health_fn=lambda: {"policy": True, "idempotency": True, "audit": True}
    )
    intent = EffectIntent.from_payload(
        effect_type="payment.transfer",
        target="ci://test",
        payload={"amount": "1"},
        idempotency_key="idem-wp5-2",
        risk_tier="low",
    )
    receipt = boundary.dispatch(intent)
    assert receipt.executed is False


def test_boundary_non_consequential_ignores_store_outage():
    """Low-risk non-value-moving actions don't hit the store gate."""
    from sincor2.shadow_monitor.effect_boundary import (
        EffectIntent,
        ShadowEffectBoundary,
    )

    boundary = ShadowEffectBoundary(
        store_health_fn=lambda: {"policy": False, "idempotency": False, "audit": False}
    )
    intent = EffectIntent.from_payload(
        effect_type="email.send",
        target="ci://test",
        payload={"body": "hi"},
        idempotency_key="idem-wp5-3",
        risk_tier="low",
    )
    receipt = boundary.dispatch(intent)
    assert receipt.status == "would_execute"
