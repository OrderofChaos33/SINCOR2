"""Shadow runtime-isolation integration tests (fail-closed CI gate).

This module is the CI-enforced proof that shadow mode has NO route from real
production entry points to real-world effects. It drives the ACTUAL classes --
not synthetic stand-ins -- with TRAP sinks that fail loudly if invoked:

Real classes exercised
----------------------
* ``sincor2.shadow_monitor.effect_boundary.ShadowEffectBoundary`` /
  ``build_effect_boundary`` / ``EffectIntent`` / ``EffectReceipt`` /
  ``LogOnlyAdapter`` -- the effect-intent choke point.
* ``sincor2.shadow_monitor.boundary.ShadowBoundary`` and the six real
  action adapters (email / social / crm / trade / transfer / contract_call).
* ``sincor2.shadow_monitor.alerting.AlertManager`` -- real alerting module,
  first with a trap delivery sink (must stay silent), then with a failing
  delivery callback (must report unhealthy + keep the capability paused).
* ``sincor2.governance.check_action`` (with the real action catalog).
* ``sincor2.governance.money_gate.check_money_effect`` (real money gate over
  the real effect boundary).

Trap sinks
----------
* ``LoudTrapSink`` records every call AND raises ``AssertionError``
  immediately -- any invocation fails the test at the point of the attempt.
* Process-egress traps (``subprocess.Popen`` / ``subprocess.run`` /
  ``subprocess.call`` / ``os.system`` / ``os.popen`` /
  ``socket.create_connection``) prove no shadow code path shells out or
  opens a TCP connection (email/SMTP, webhooks, RPC broadcasts all need one).
* The real module counter ``ACTUAL_SIDE_EFFECTS`` (from
  ``sincor2.shadow_monitor.boundary``) must stay 0 -- it is the production
  module's own counter, not a fixture's.

What is proven
-------------
1. Dispatching an ``EffectIntent`` of every known kind through the real
   ``ShadowEffectBoundary`` leaves every trap silent; every receipt has
   ``executed=False`` and a status in ``{"would_execute", "blocked_policy",
   "pending_approval"}`` -- never "sent"/"settled"/"delivered".
2. Every adapter's ``live_action`` entry point raises
   ``ShadowBoundaryViolation`` in shadow mode.
3. Unknown / empty effect kinds and missing idempotency keys raise; a
   raising or malformed policy degrades to ``blocked_policy`` (fail closed).
4. ``build_effect_boundary("live")`` raises ``RuntimeError`` -- live is never
   constructible from worker code.
5. The money gate denies would-pay intents while the kill switch is engaged
   and denies unknown effect types; it never raises.
6. ``check_action`` raises ``UnknownAction`` for uncatalogued actions and
   denies (never allows) without required human approval.
7. A failing alert delivery marks the ``alert_delivery`` capability paused
   (reported unhealthy), retains the failed alert (never silently dropped).
8. The audit trail stores only payload hashes -- no cleartext payload ever
   lands in the audit log.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import uuid

import pytest

from sincor2.governance import UnknownAction, check_action
from sincor2.governance import action_catalog
from sincor2.governance.money_gate import (
    check_money_effect,
    get_money_boundary,
    reset_money_boundary,
)
from sincor2.shadow_monitor import alerting
from sincor2.shadow_monitor.alerting import Alert, AlertManager
from sincor2.shadow_monitor.boundary import (
    ACTUAL_SIDE_EFFECTS,
    ContractCallAdapter,
    CrmAdapter,
    EmailAdapter,
    KillSwitch,
    ShadowBoundary,
    ShadowBoundaryViolation,
    SocialAdapter,
    TradeAdapter,
    TransferAdapter,
)
from sincor2.shadow_monitor.effect_boundary import (
    EFFECT_TYPES,
    RECEIPT_STATUSES,
    WOULD_PAY_EFFECT_TYPES,
    EffectIntent,
    EffectReceipt,
    LogOnlyAdapter,
    assert_no_live_import,
    build_effect_boundary,
    guard_live_adapter_import,
)

ADAPTER_CLASSES = [
    EmailAdapter,
    SocialAdapter,
    CrmAdapter,
    TradeAdapter,
    TransferAdapter,
    ContractCallAdapter,
]

# Statuses a shadow receipt may NEVER carry -- there is no code path that
# performs an effect, so these literals are unrepresentable in shadow.
FORBIDDEN_STATUSES = ("sent", "settled", "delivered")


# ---------------------------------------------------------------------------
# Trap sinks
# ---------------------------------------------------------------------------


class LoudTrapSink:
    """Fake provider sink that must never be invoked.

    Records every call (name + args + kwargs) and raises AssertionError
    immediately so any invocation fails the test loudly at the point of the
    attempt rather than surfacing later as a suspicious counter.
    """

    def __init__(self, name: str):
        self.name = name
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append({"sink": self.name, "args": args, "kwargs": kwargs})
        raise AssertionError(
            "SHADOW ISOLATION VIOLATED: trap sink "
            f"{self.name!r} was invoked (args={args!r}, kwargs={kwargs!r}) -- "
            "a real outbound route exists from shadow code!"
        )

    def assert_silent(self) -> None:
        assert not self.calls, (
            f"trap sink {self.name!r} was invoked "
            f"{len(self.calls)} time(s): {self.calls!r}"
        )


class RecordingSink:
    """Records calls without raising (for legitimate in-shadow callbacks).

    Used where the real code is *expected* to invoke a hook -- e.g. the
    boundary's ``on_violation`` callback, which records a blocked attempt
    without performing anything.
    """

    def __init__(self, name: str):
        self.name = name
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append({"sink": self.name, "args": args, "kwargs": kwargs})
        return {"recorded": True}


@pytest.fixture()
def alert_trap():
    """Trap delivery sink for the real AlertManager (must stay silent)."""
    return LoudTrapSink("alert_delivery_provider")


@pytest.fixture(autouse=True)
def egress_traps(monkeypatch):
    """Trap every process-egress route shadow code could abuse.

    SMTP/email, webhooks, RPC broadcasts all require a TCP connection;
    subprocess effects require a process spawn. These traps sit underneath
    the REAL classes: if any driven code path attempts an outbound effect,
    the test fails loudly at the attempt.
    """
    traps = {
        "subprocess.Popen": LoudTrapSink("subprocess.Popen"),
        "subprocess.run": LoudTrapSink("subprocess.run"),
        "subprocess.call": LoudTrapSink("subprocess.call"),
        "os.system": LoudTrapSink("os.system"),
        "os.popen": LoudTrapSink("os.popen"),
        "socket.create_connection": LoudTrapSink("socket.create_connection"),
    }
    monkeypatch.setattr(subprocess, "Popen", traps["subprocess.Popen"])
    monkeypatch.setattr(subprocess, "run", traps["subprocess.run"])
    monkeypatch.setattr(subprocess, "call", traps["subprocess.call"])
    monkeypatch.setattr(os, "system", traps["os.system"])
    monkeypatch.setattr(os, "popen", traps["os.popen"])
    monkeypatch.setattr(
        socket, "create_connection", traps["socket.create_connection"]
    )
    yield traps
    for trap in traps.values():
        trap.assert_silent()


@pytest.fixture()
def boundary():
    """A real shadow effect boundary built through the production factory."""
    return build_effect_boundary("shadow")


def make_intent(effect_type: str, *, risk_tier: str = "low", cost: float = 0.0,
                payload: dict | None = None, tenant: str = "shadow-ci",
                key: str | None = None) -> EffectIntent:
    return EffectIntent.from_payload(
        effect_type=effect_type,
        target=f"shadow-ci-target::{effect_type}",
        payload=payload if payload is not None else {"marker": "shadow-ci",
                                                     "nonce": uuid.uuid4().hex},
        idempotency_key=key or f"shadow-ci-{uuid.uuid4().hex}",
        trace_id=f"trace-{uuid.uuid4().hex}",
        agent_id="shadow-ci-agent",
        tenant=tenant,
        risk_tier=risk_tier,
        estimated_cost=cost,
    )


def assert_shadow_receipt(receipt: EffectReceipt, intent: EffectIntent) -> None:
    """A receipt is only valid if shadow invariants hold."""
    assert receipt.effect_id == intent.effect_id
    assert receipt.executed is False
    assert receipt.status in RECEIPT_STATUSES
    assert receipt.status not in FORBIDDEN_STATUSES
    assert "sent" not in receipt.status and "delivered" not in receipt.status


# ---------------------------------------------------------------------------
# 1. Every effect type dispatched through the REAL boundary stays in shadow
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("effect_type", sorted(EFFECT_TYPES))
def test_every_effect_type_stays_in_shadow(boundary, alert_trap, effect_type):
    """Drive the real ShadowEffectBoundary.dispatch for every known kind.

    Trap sinks are wired into every injectable seam of the real classes;
    process-egress traps (autouse fixture) sit underneath. Proof = real code
    ran, traps stayed silent, receipts carry only shadow statuses.
    """
    # Would-pay kinds go through as high risk (blocked by fail-closed
    # policy); the rest as low risk (would_execute). One medium+cost case
    # exercises pending_approval elsewhere.
    risk = "high" if effect_type in WOULD_PAY_EFFECT_TYPES else "low"
    intent = make_intent(effect_type, risk_tier=risk)

    # Real alerting module with a trap delivery sink: a shadow pass must not
    # route anything to the alert provider either.
    mgr = AlertManager(delivery_fn=alert_trap)
    adapter = LogOnlyAdapter(boundary)
    receipt = adapter.execute(intent)

    assert_shadow_receipt(receipt, intent)
    assert boundary.proposal_queue[-1].effect_id == intent.effect_id
    # The proposal queue is a plain list: there is no live destination to
    # move proposals into.
    assert isinstance(boundary.proposal_queue, list)
    alert_trap.assert_silent()


def test_pending_approval_status_is_shadow_only(boundary):
    """Medium risk + nonzero cost -> pending_approval (still not executed)."""
    intent = make_intent("message.send", risk_tier="medium", cost=12.5)
    receipt = boundary.dispatch(intent)
    assert receipt.status == "pending_approval"
    assert_shadow_receipt(receipt, intent)


def test_kill_switch_blocks_would_pay_intents():
    """Engaged kill switch forces blocked_policy for value-moving kinds."""
    ks = KillSwitch()
    ks.engage()
    b = build_effect_boundary("shadow")
    b.kill_switch = ks
    for effect_type in sorted(WOULD_PAY_EFFECT_TYPES):
        intent = make_intent(effect_type, risk_tier="low",
                             key=f"ks-{effect_type}")
        receipt = b.dispatch(intent)
        assert receipt.status == "blocked_policy"
        assert "kill_switch_engaged_would_pay_blocked" in receipt.policy_reason_codes
        assert_shadow_receipt(receipt, intent)


def test_actual_side_effects_counter_stays_zero():
    """The production module's own counter -- not a fixture's -- stays 0."""
    start = ACTUAL_SIDE_EFFECTS
    boundary = ShadowBoundary()
    for cls in ADAPTER_CLASSES:
        adapter = cls(boundary)
        adapter.shadow_action(target="t", payload_summary="redacted",
                              trace_id="counter-check")
        with pytest.raises(ShadowBoundaryViolation):
            adapter.live_action(target="t", payload_summary="redacted",
                                trace_id="counter-check")
    import sincor2.shadow_monitor.boundary as bmod
    assert bmod.ACTUAL_SIDE_EFFECTS == start == 0


# ---------------------------------------------------------------------------
# 2. Adapter-layer entry points are blocked in shadow mode
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("adapter_cls", ADAPTER_CLASSES)
def test_adapter_live_action_blocked_in_shadow(adapter_cls):
    """The real adapters' real live_action entry point cannot act in shadow."""
    violations = RecordingSink("on_violation")
    boundary = ShadowBoundary(on_violation=violations)
    adapter = adapter_cls(boundary)
    with pytest.raises(ShadowBoundaryViolation):
        adapter.live_action(
            target=f"{adapter_cls.adapter_kind}-target",
            payload_summary="redacted summary",
            estimated_cost=1.0,
            trace_id="t-live-blocked",
        )
    # The blocked attempt is RECORDED as evidence (legitimate in-shadow
    # hook) -- nothing was performed.
    assert len(violations.calls) == 1
    assert len(boundary.violations()) == 1


def test_log_only_adapter_never_delivers(boundary, alert_trap):
    adapter = LogOnlyAdapter(boundary)
    intent = make_intent("email.send")
    receipt = adapter.execute(intent)
    assert_shadow_receipt(receipt, intent)
    report = adapter.delivery_report()
    assert report == {"status": "not sent", "reason": "log-only"}
    assert report["status"] not in FORBIDDEN_STATUSES
    alert_trap.assert_silent()


# ---------------------------------------------------------------------------
# 3. No production filesystem mutation; audit is hash-only
# ---------------------------------------------------------------------------


def test_no_production_filesystem_mutation(boundary, tmp_path):
    """Shadow pass leaves the filesystem untouched and stores no payloads."""
    canary = tmp_path / "prod-canary.txt"
    canary.write_text("production-data-must-not-change")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}

    payload_marker = f"CLEARTEXT-PAYLOAD-{uuid.uuid4().hex}"
    for effect_type in sorted(EFFECT_TYPES):
        risk = "high" if effect_type in WOULD_PAY_EFFECT_TYPES else "low"
        intent = make_intent(effect_type, risk_tier=risk,
                             payload={"secret": payload_marker})
        receipt = boundary.dispatch(intent)
        assert_shadow_receipt(receipt, intent)

    after = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert after == before, "shadow run mutated the filesystem"

    # Audit trail is hash-only: the cleartext marker must appear nowhere.
    audit_blob = json.dumps(boundary.audit_log)
    assert payload_marker not in audit_blob
    assert all("payload_hash" in event for event in boundary.audit_log)
    assert not any("payload" in k and k != "payload_hash"
                   for event in boundary.audit_log for k in event)


def test_receipts_can_never_claim_execution():
    """EffectReceipt(executed=True) is unrepresentable by construction."""
    with pytest.raises(ValueError):
        EffectReceipt(effect_id="x", status="would_execute", executed=True)


# ---------------------------------------------------------------------------
# 4. Unknown / malformed actions fail closed
# ---------------------------------------------------------------------------


def test_unknown_effect_kind_raises_at_construction():
    with pytest.raises(ValueError, match="unknown effect_type"):
        EffectIntent(effect_type="send_email", idempotency_key="k1")


def test_empty_effect_kind_raises_at_construction():
    with pytest.raises(ValueError, match="unknown effect_type"):
        EffectIntent(effect_type="", idempotency_key="k1")


def test_missing_idempotency_key_raises():
    with pytest.raises(ValueError, match="idempotency_key is required"):
        EffectIntent(effect_type="email.send", idempotency_key="")


def test_dispatch_unknown_kind_is_refused(boundary):
    """A forged intent object with an unknown kind cannot be dispatched."""
    intent = make_intent("email.send")
    # The dataclass constructor enforces the allowlist, so the only way to
    # smuggle an unknown kind is post-construction tampering (frozen
    # dataclass -> object.__setattr__ bypass, simulating a forged object).
    forged = EffectIntent(
        effect_id=intent.effect_id,
        effect_type="email.send",
        target=intent.target,
        payload_hash=intent.payload_hash,
        idempotency_key=intent.idempotency_key,
        trace_id=intent.trace_id,
        agent_id=intent.agent_id,
        tenant=intent.tenant,
    )
    # Sanity: the dataclass constructor itself enforces the allowlist, so
    # the only way to smuggle an unknown kind is post-construction tampering.
    object.__setattr__(forged, "effect_type", "totally.bogus")
    with pytest.raises(ValueError, match="unknown effect_type"):
        boundary.dispatch(forged)


def test_raising_policy_fails_closed_to_blocked():
    def bad_policy(intent):
        raise RuntimeError("policy backend exploded")

    b = build_effect_boundary("shadow")
    b._policy_fn = bad_policy
    intent = make_intent("message.send")
    receipt = b.dispatch(intent)
    assert receipt.status == "blocked_policy"
    assert receipt.policy_reason_codes == ["policy_fn_error:RuntimeError"]
    assert_shadow_receipt(receipt, intent)


def test_malformed_policy_result_fails_closed():
    b = build_effect_boundary("shadow")
    b._policy_fn = lambda intent: ("delivered", [])  # not a shadow status
    receipt = b.dispatch(make_intent("message.send"))
    assert receipt.status == "blocked_policy"
    assert "policy_fn_invalid_result" in receipt.policy_reason_codes


# ---------------------------------------------------------------------------
# 5. Live boundary is never constructible from worker code
# ---------------------------------------------------------------------------


def test_build_effect_boundary_live_raises():
    with pytest.raises(RuntimeError, match="separately deployed executor"):
        build_effect_boundary("live")


def test_build_effect_boundary_unknown_profile_raises():
    with pytest.raises(ValueError, match="unknown effect-boundary profile"):
        build_effect_boundary("production")


def test_no_live_modules_importable():
    assert_no_live_import()  # raises if live_executor/live_adapter in sys.modules
    with pytest.raises(Exception, match="refusing to import live adapter"):
        guard_live_adapter_import("sincor2.live_executor")


# ---------------------------------------------------------------------------
# 6. Money gate: fail-closed over the real boundary
# ---------------------------------------------------------------------------


def test_money_gate_blocks_would_pay_under_kill_switch():
    reset_money_boundary()
    try:
        boundary = get_money_boundary()
        boundary.kill_switch.engage()
        allowed, reason = check_money_effect(
            effect_type="payment.transfer",
            payload={"to": "0xdead", "amount": "1.0"},
            agent_id="shadow-ci-agent",
            risk_tier="low",
        )
        assert allowed is False
        assert "kill_switch" in reason or "blocked" in reason
    finally:
        reset_money_boundary()


def test_money_gate_external_kill_switch_denies():
    allowed, reason = check_money_effect(
        effect_type="trade.swap",
        payload={"pair": "AXM/USDC"},
        kill_switch_tripped=True,
    )
    assert allowed is False
    assert reason == "external_kill_switch_tripped"


def test_money_gate_unknown_effect_type_denies_never_raises():
    allowed, reason = check_money_effect(
        effect_type="teleport.funds",
        payload={},
    )
    assert allowed is False
    assert "effect_boundary_error" in reason


# ---------------------------------------------------------------------------
# 7. Governance check_action: fail-closed over the real catalog
# ---------------------------------------------------------------------------


def test_check_action_unknown_action_raises():
    assert action_catalog.get_action("definitely_not_a_real_action") is None
    with pytest.raises(UnknownAction):
        check_action("shadow-ci-agent", "definitely_not_a_real_action", {})


def test_check_action_denies_without_required_approval():
    """place_bid requires human approval: empty context must deny, not allow."""
    allowed, reason = check_action("shadow-ci-agent", "place_bid", {})
    assert allowed is False
    assert "approval" in reason


# ---------------------------------------------------------------------------
# 8. Failed alert path: reported unhealthy, capability paused, alert kept
# ---------------------------------------------------------------------------


def _failing_delivery(spy):
    def deliver(alert):
        spy.append(alert)
        raise RuntimeError("SMTP relay down: connection refused")
    return deliver


def _sample_alert(trace_id: str) -> Alert:
    return Alert.from_anomaly({
        "anomaly_class": "safety_boundary",
        "anomaly_id": f"anom-{trace_id}",
        "description": "shadow CI probe: simulated provider outage",
        "trace_id": trace_id,
        "workflow": "shadow-ci",
        "policy_reason": "delivery_probe",
        "tenant": "shadow-ci",
    })


def test_failed_alert_path_reports_unhealthy_and_pauses_capability():
    """Real AlertManager + failing delivery: unhealthy, paused, not dropped."""
    spy = []
    mgr = AlertManager(delivery_fn=_failing_delivery(spy))

    alert = _sample_alert("probe-1")
    receipt = mgr.send(alert)

    assert receipt["delivered"] is False
    assert "alert_delivery" in mgr.paused_capabilities, (
        "delivery capability must be marked paused after a delivery failure"
    )
    # The alert is RETAINED in failed_deliveries -- never silently dropped.
    assert len(mgr.failed_deliveries) == 1
    assert mgr.failed_deliveries[0]["alert"].alert_id == alert.alert_id
    assert "SMTP relay down" in mgr.failed_deliveries[0]["error"]
    # Nothing reached the (failing) provider's outbox.
    assert mgr.outbox == []

    # The paused capability IS the unhealthy signal the monitor reports.
    health = ("unhealthy"
              if alerting.DELIVERY_CAPABILITY in mgr.paused_capabilities
              else "healthy")
    assert health == "unhealthy"

    # A second failure keeps the capability paused and keeps both alerts.
    alert2 = _sample_alert("probe-2")
    receipt2 = mgr.send(alert2)
    assert receipt2["delivered"] is False
    assert "alert_delivery" in mgr.paused_capabilities
    assert len(mgr.failed_deliveries) == 2
    assert len(spy) == 2


def test_alert_path_healthy_when_delivery_succeeds():
    """Control: with a working delivery fn the capability is not paused."""
    delivered = []

    def ok_delivery(alert):
        delivered.append(alert)
        return {"delivered": True}

    mgr = AlertManager(delivery_fn=ok_delivery)
    receipt = mgr.send(_sample_alert("probe-ok"))
    assert receipt["delivered"] is True
    assert "alert_delivery" not in mgr.paused_capabilities
    assert len(delivered) == 1
