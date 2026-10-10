"""Tests for sincor2.effect_gateway — the typed effect gateway.

Adversarial posture: every test below tries to get an effect past the
gateway without proper authorization — malformed intents, missing context,
missing policy, unknown effect types, replayed idempotency keys,
tenant confusion, and an engaged kill switch. In ALL deny cases the fake
adapter must record ZERO calls.
"""

import pytest

from sincor2.effect_gateway import (
    AuditTrail,
    Decision,
    EffectAdapter,
    EffectGateway,
    EffectIntent,
    EffectType,
    RuntimeContext,
    build_intent,
    default_policy,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

VALID_PAYLOADS = {
    EffectType.EMAIL_SEND: {"to": "a@example.com", "subject": "s", "body": "b"},
    EffectType.SMS_SEND: {"to": "+15551234567", "body": "b"},
    EffectType.MESSAGE_SEND: {"to": "peer-abc", "body": "b"},
    EffectType.CONTENT_PUBLISH: {"channel": "blog", "content": "x"},
    EffectType.CRM_WRITE: {"record": {"id": "1"}},
    EffectType.CHAIN_WRITE: {
        "chain_id": 8453,
        "to": "0x0000000000000000000000000000000000000001",
        "calldata_or_value": "0x",
    },
    EffectType.FILE_WRITE: {"path": "/tmp/sincor-test.txt"},
    EffectType.PROCESS_EXEC: {"argv": ["echo", "hi"]},
    EffectType.SCHEDULER_ENQUEUE: {"job": "daily_digest"},
    EffectType.HTTP_EXTERNAL: {"url": "https://example.com/hook", "method": "POST"},
    EffectType.PAYMENT_CHARGE: {
        "amount": 100,
        "currency": "USD",
        "destination": "acct_1",
    },
}


class FakeAdapter(EffectAdapter):
    """Records every execute() call; returns a canned receipt."""

    def __init__(self, effect_type):
        self._effect_type = effect_type
        self.calls = []

    @property
    def effect_type(self):
        return self._effect_type

    def execute(self, intent, context):
        self.calls.append((intent, context))
        return {"ok": True, "adapter": self._effect_type.value}


def make_gateway(*effect_types, policy_fn=None, kill_switch=False):
    gw = EffectGateway(policy_fn=policy_fn)
    adapters = {}
    for et in effect_types:
        adapter = FakeAdapter(et)
        gw.register_adapter(adapter)
        adapters[et] = adapter
    return gw, adapters


_DEFAULT_POLICY = object()


def make_context(policy=_DEFAULT_POLICY, kill_switch=False, tenant="tenant-1"):
    if policy is _DEFAULT_POLICY:
        policy = {
            "allowed_effect_types": [t.value for t in EffectType],
            "roles": ["executor"],
        }
    return RuntimeContext(
        principal="agent-1",
        tenant=tenant,
        role="executor",
        policy=policy,
        kill_switch_engaged=kill_switch,
    )


def make_intent(effect_type, key="key-1", tenant="tenant-1", **overrides):
    params = dict(
        effect_type=effect_type,
        actor="agent-1",
        tenant=tenant,
        payload=dict(VALID_PAYLOADS[effect_type]),
        idempotency_key=key,
        purpose="test",
        trace_id="trace-1",
    )
    params.update(overrides)
    return EffectIntent(**params)


ALL_TYPES = list(EffectType)


# ---------------------------------------------------------------------------
# Deny matrix: every effect type x every deny condition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("effect_type", ALL_TYPES)
def test_deny_no_context_zero_adapter_calls(effect_type):
    gw, adapters = make_gateway(effect_type)
    intent = make_intent(effect_type, key="k-noctx")
    receipt = gw.dispatch(intent, None)
    assert receipt.executed is False
    assert receipt.status == "denied"
    assert "missing_context" in receipt.reason_codes
    assert adapters[effect_type].calls == []
    assert len(gw.audit_trail) == 1  # denied dispatches are still audited


@pytest.mark.parametrize("effect_type", ALL_TYPES)
def test_deny_no_policy_zero_adapter_calls(effect_type):
    gw, adapters = make_gateway(effect_type)
    intent = make_intent(effect_type, key="k-nopolicy")
    ctx = make_context(policy=None)  # no policy snapshot
    receipt = gw.dispatch(intent, ctx)
    assert receipt.executed is False
    assert "missing_policy" in receipt.reason_codes
    assert adapters[effect_type].calls == []


@pytest.mark.parametrize("effect_type", ALL_TYPES)
def test_deny_unknown_effect_type_zero_adapter_calls(effect_type):
    gw, adapters = make_gateway(effect_type)
    intent = make_intent(effect_type, key="k-unknown")
    # Adversarial: mutate a frozen intent to carry a bogus effect type,
    # simulating a confused/malicious caller bypassing construction checks.
    object.__setattr__(intent, "effect_type", "TOTALLY_BOGUS_TYPE")
    receipt = gw.dispatch(intent, make_context())
    assert receipt.executed is False
    assert "unknown_effect_type" in receipt.reason_codes
    assert adapters[effect_type].calls == []


@pytest.mark.parametrize("effect_type", ALL_TYPES)
def test_deny_duplicate_idempotency_key(effect_type):
    gw, adapters = make_gateway(effect_type)
    ctx = make_context()
    first = gw.dispatch(make_intent(effect_type, key="k-dup"), ctx)
    assert first.executed is True
    assert len(adapters[effect_type].calls) == 1
    # Replay with the SAME key (even identical payload): denied, no re-exec.
    replay = gw.dispatch(make_intent(effect_type, key="k-dup"), ctx)
    assert replay.executed is False
    assert replay.status == "denied"
    assert "duplicate_idempotency_key" in replay.reason_codes
    assert len(adapters[effect_type].calls) == 1  # not called again


@pytest.mark.parametrize("effect_type", ALL_TYPES)
def test_deny_idempotency_key_conflict(effect_type):
    # Same key, DIFFERENT payload: denied with a distinct reason code —
    # this is the tamper/replay-confusion case.
    gw, adapters = make_gateway(effect_type)
    ctx = make_context()
    assert gw.dispatch(make_intent(effect_type, key="k-conflict"), ctx).executed
    tampered_payload = dict(VALID_PAYLOADS[effect_type])
    # mutate one required field value
    first_field = next(iter(tampered_payload))
    tampered_payload[first_field] = "TAMPERED"
    tampered = EffectIntent(
        effect_type=effect_type,
        actor="agent-1",
        tenant="tenant-1",
        payload=tampered_payload,
        idempotency_key="k-conflict",
    )
    receipt = gw.dispatch(tampered, ctx)
    assert receipt.executed is False
    assert "idempotency_key_conflict" in receipt.reason_codes
    assert len(adapters[effect_type].calls) == 1


@pytest.mark.parametrize("effect_type", ALL_TYPES)
def test_deny_kill_switch_all_types(effect_type):
    gw, adapters = make_gateway(effect_type)
    ctx = make_context(kill_switch=True)
    receipt = gw.dispatch(make_intent(effect_type, key="k-kill"), ctx)
    assert receipt.executed is False
    assert "kill_switch_engaged" in receipt.reason_codes
    assert adapters[effect_type].calls == []


@pytest.mark.parametrize("effect_type", ALL_TYPES)
def test_deny_tenant_mismatch(effect_type):
    gw, adapters = make_gateway(effect_type)
    ctx = make_context(tenant="other-tenant")  # intent says tenant-1
    receipt = gw.dispatch(make_intent(effect_type, key="k-tenant"), ctx)
    assert receipt.executed is False
    assert "tenant_mismatch" in receipt.reason_codes
    assert adapters[effect_type].calls == []


@pytest.mark.parametrize("effect_type", ALL_TYPES)
def test_deny_effect_type_not_permitted(effect_type):
    gw, adapters = make_gateway(effect_type)
    ctx = make_context(policy={"allowed_effect_types": [], "roles": ["executor"]})
    receipt = gw.dispatch(make_intent(effect_type, key="k-notperm"), ctx)
    assert receipt.executed is False
    assert "effect_type_not_permitted" in receipt.reason_codes
    assert adapters[effect_type].calls == []


# ---------------------------------------------------------------------------
# Allow path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("effect_type", ALL_TYPES)
def test_allowed_intent_adapter_called_exactly_once(effect_type):
    gw, adapters = make_gateway(effect_type)
    ctx = make_context()
    intent = make_intent(effect_type, key="k-allow")
    receipt = gw.dispatch(intent, ctx)
    assert receipt.executed is True
    assert receipt.status == "allowed"
    assert receipt.intent_id == intent.intent_id
    assert len(adapters[effect_type].calls) == 1
    called_intent, called_ctx = adapters[effect_type].calls[0]
    assert called_intent.intent_id == intent.intent_id
    assert called_ctx.principal == "agent-1"
    assert receipt.adapter_receipt == {
        "ok": True,
        "adapter": effect_type.value,
    }


def test_high_risk_type_requires_privileged_role():
    gw, adapters = make_gateway(EffectType.CHAIN_WRITE)
    ctx = make_context(policy={
        "allowed_effect_types": ["CHAIN_WRITE"],
        "roles": ["reader"],  # not executor/admin/treasury_ops
    })
    receipt = gw.dispatch(make_intent(EffectType.CHAIN_WRITE, key="k-role"), ctx)
    assert receipt.executed is False
    assert "high_risk_role_required" in receipt.reason_codes
    assert adapters[EffectType.CHAIN_WRITE].calls == []


def test_no_adapter_registered_denies():
    gw, _ = make_gateway()  # nothing registered
    ctx = make_context()
    receipt = gw.dispatch(make_intent(EffectType.EMAIL_SEND, key="k-noadapter"), ctx)
    assert receipt.executed is False
    assert "no_adapter_registered" in receipt.reason_codes


def test_per_dispatch_adapter_override():
    gw, _ = make_gateway()
    override_adapter = FakeAdapter(EffectType.SMS_SEND)
    ctx = make_context()
    receipt = gw.dispatch(
        make_intent(EffectType.SMS_SEND, key="k-override"),
        ctx,
        adapters={EffectType.SMS_SEND: override_adapter},
    )
    assert receipt.executed is True
    assert len(override_adapter.calls) == 1


def test_adapter_exception_becomes_failed_receipt():
    class BoomAdapter(FakeAdapter):
        def execute(self, intent, context):
            self.calls.append((intent, context))
            raise RuntimeError("provider exploded")

    gw = EffectGateway()
    adapter = BoomAdapter(EffectType.EMAIL_SEND)
    gw.register_adapter(adapter)
    receipt = gw.dispatch(make_intent(EffectType.EMAIL_SEND, key="k-boom"), make_context())
    assert receipt.executed is False
    assert receipt.status == "adapter_failed"
    assert "adapter_error" in receipt.reason_codes


def test_duplicate_adapter_registration_rejected():
    gw, _ = make_gateway(EffectType.EMAIL_SEND)
    with pytest.raises(ValueError):
        gw.register_adapter(FakeAdapter(EffectType.EMAIL_SEND))


# ---------------------------------------------------------------------------
# Policy robustness: broken policies deny, never allow
# ---------------------------------------------------------------------------


def test_policy_fn_raising_denies():
    def bad_policy(intent, context):
        raise RuntimeError("policy store down")

    gw, adapters = make_gateway(EffectType.EMAIL_SEND, policy_fn=bad_policy)
    receipt = gw.dispatch(make_intent(EffectType.EMAIL_SEND, key="k-badpol"), make_context())
    assert receipt.executed is False
    assert any(c.startswith("policy_fn_error") for c in receipt.reason_codes)
    assert adapters[EffectType.EMAIL_SEND].calls == []


def test_policy_fn_malformed_result_denies():
    gw, adapters = make_gateway(
        EffectType.EMAIL_SEND, policy_fn=lambda i, c: "yes-please"
    )
    receipt = gw.dispatch(make_intent(EffectType.EMAIL_SEND, key="k-malpol"), make_context())
    assert receipt.executed is False
    assert "policy_fn_invalid_result" in receipt.reason_codes
    assert adapters[EffectType.EMAIL_SEND].calls == []


# ---------------------------------------------------------------------------
# Audit fail-closed + intent validation
# ---------------------------------------------------------------------------


def test_audit_failure_denies_before_adapter_runs():
    gw, adapters = make_gateway(EffectType.EMAIL_SEND)
    gw.audit_trail._fail_next_append = True  # simulate storage failure
    receipt = gw.dispatch(make_intent(EffectType.EMAIL_SEND, key="k-auditfail"), make_context())
    assert receipt.executed is False
    assert "audit_failure" in receipt.reason_codes
    assert adapters[EffectType.EMAIL_SEND].calls == []


def test_malformed_payload_rejected_at_construction():
    with pytest.raises(ValueError):
        EffectIntent(
            effect_type=EffectType.EMAIL_SEND,
            actor="agent-1",
            tenant="t",
            payload={"to": "a@b.c"},  # missing subject/body
            idempotency_key="k",
        )


def test_unknown_effect_type_rejected_at_construction():
    with pytest.raises(ValueError):
        build_intent("NOT_A_TYPE", actor="a", tenant="t",
                     payload={}, idempotency_key="k")


def test_missing_idempotency_key_rejected_at_construction():
    with pytest.raises(ValueError):
        make_intent(EffectType.EMAIL_SEND, key="")


def test_dispatch_rejects_non_intent():
    gw, _ = make_gateway(EffectType.EMAIL_SEND)
    with pytest.raises(TypeError):
        gw.dispatch({"not": "an intent"}, make_context())


def test_audit_trail_records_every_dispatch():
    gw, adapters = make_gateway(EffectType.EMAIL_SEND)
    ctx = make_context()
    gw.dispatch(make_intent(EffectType.EMAIL_SEND, key="k-a1"), ctx)  # allowed
    gw.dispatch(make_intent(EffectType.EMAIL_SEND, key="k-a1"), ctx)  # duplicate
    gw.dispatch(make_intent(EffectType.EMAIL_SEND, key="k-a2"), None)  # no ctx
    entries = gw.audit_trail.entries()
    # Allowed dispatches log twice: the authorization (pre-execution,
    # fail-closed) and the outcome. Denied dispatches log once.
    assert len(entries) == 4
    assert [e["status"] for e in entries] == [
        "allowed_authorized",
        "allowed",
        "denied",
        "denied",
    ]
    assert entries[0]["executed"] is False  # authorization, not yet executed
    assert entries[1]["executed"] is True
    # audit entries carry hashes, never raw payloads
    assert "payload" not in entries[0]
    assert entries[0]["payload_hash"]


def test_decision_helpers():
    assert Decision.allow("ok").allowed is True
    assert Decision.deny("no").allowed is False
    assert default_policy(
        make_intent(EffectType.CRM_WRITE, key="k-dp"), make_context()
    ).allowed is True
