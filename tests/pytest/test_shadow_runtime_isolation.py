"""Fail-closed integration contract for SINCOR2 shadow runtime isolation.

This suite intentionally requires the deployed mvp_app wiring, not a separately
constructed ShadowEffectBoundary fixture. The registry exercises proposal-only
agent effect entrypoints; legacy customer-facing routes still require their own
migration before app-wide isolation can be claimed.

Required production contract:

* create_app() installs app.extensions["sincor_shadow_runtime"];
* the runtime reports mode == "shadow", has no live_executor, signer, or
  write_credentials, and exposes effect_entrypoints: effect_type -> callable;
* those callables are production-owned proposal-only handlers, not test-only
  lambdas. Agent services/workers must be migrated to call them explicitly;
* invoking an entrypoint with an EffectIntent returns an EffectReceipt that
  records a proposal only, with executed == False and a shadow-only status.

The registry-completeness assertion is not proof that every existing route,
worker, scheduler, or legacy service is wired. Keep the effect-caller inventory
reviewed alongside this test and add route/worker-level invocations as those
callers are migrated. No test here sends real messages, changes customer data,
or submits/broadcasts transactions.
"""

from __future__ import annotations

import socket
import subprocess
import urllib.request
from collections.abc import Mapping
from typing import Any, Callable

import pytest

from sincor2.shadow_monitor.effect_boundary import (
    EFFECT_TYPES,
    RECEIPT_STATUSES,
    EffectIntent,
    EffectReceipt,
)


_EFFECT_PAYLOADS: dict[str, dict[str, Any]] = {
    "email.send": {"to": "ci-example@example.invalid", "body": "synthetic"},
    "social.post": {"text": "synthetic shadow test"},
    "crm.write": {"record_id": "ci-record", "field": "status", "value": "test"},
    "crm.delete": {"record_id": "ci-record"},
    "payment.transfer": {"asset": "TEST", "amount": "0", "destination": "test-only"},
    "trade.swap": {"asset_in": "TEST-A", "asset_out": "TEST-B", "amount": "0"},
    "contract.call": {"chain": "local-test", "contract": "0x" + "00" * 20, "calldata": "0x"},
    "message.send": {"recipient": "test-only", "body": "synthetic"},
}


def _make_intent(effect_type: str) -> EffectIntent:
    """Build a synthetic, hash-only intent with unique idempotency evidence."""
    return EffectIntent.from_payload(
        effect_type=effect_type,
        target="ci://shadow-runtime-test",
        payload=_EFFECT_PAYLOADS[effect_type],
        idempotency_key=f"ci-shadow-isolation:{effect_type}",
        trace_id=f"ci-trace:{effect_type}",
        agent_id="ci-shadow-test-agent",
        tenant="ci-shadow-test-tenant",
        risk_tier="low",
        estimated_cost=0.0,
    )


@pytest.fixture
def production_app(monkeypatch):
    """Load the Flask app served by railway_start.py / Gunicorn."""
    monkeypatch.setenv("AUTONOMOUS_AGENTS", "false")
    monkeypatch.setenv("OUTREACH_ENABLED", "false")
    from sincor2.mvp_app import app

    return app


@pytest.fixture
def shadow_runtime(production_app):
    """Obtain the runtime installed by the deployed application."""
    runtime = production_app.extensions.get("sincor_shadow_runtime")
    assert runtime is not None, (
        "create_app() must install app.extensions['sincor_shadow_runtime']; "
        "testing ShadowBoundary in isolation does not prove application wiring"
    )
    assert runtime.__class__.__module__.startswith("sincor2."), (
        "the registered shadow runtime must be a production implementation, "
        "not a test-only fake"
    )
    assert getattr(runtime, "mode", None) == "shadow", (
        "the application runtime must explicitly report effective shadow mode"
    )
    return runtime


@pytest.fixture
def outbound_attempt_trap(monkeypatch):
    """Arm a process-level egress/process trap around the tested effect calls.

    App construction happens before this fixture is armed; only the synthetic
    effect-entrypoint calls are guarded. Any attempted socket/network or child
    process operation is recorded even if application code catches the raised
    exception, so a swallowed transport failure still fails the assertion.
    """
    attempts: list[str] = []

    def blocked(operation: str) -> Callable[..., Any]:
        def _blocked(*args: Any, **kwargs: Any) -> Any:
            attempts.append(operation)
            raise AssertionError(f"shadow entrypoint attempted forbidden {operation}")

        return _blocked

    def arm() -> None:
        monkeypatch.setattr(socket.socket, "connect", blocked("socket.connect"))
        monkeypatch.setattr(socket.socket, "connect_ex", blocked("socket.connect_ex"))
        monkeypatch.setattr(socket, "create_connection", blocked("socket.create_connection"))
        monkeypatch.setattr(urllib.request, "urlopen", blocked("urllib.request.urlopen"))
        monkeypatch.setattr(subprocess, "Popen", blocked("subprocess.Popen"))
        monkeypatch.setattr(subprocess, "run", blocked("subprocess.run"))

        # Patch common HTTP clients when installed. Socket traps remain the
        # transport-level backstop for other libraries.
        try:
            import requests.sessions
        except ImportError:
            pass
        else:
            monkeypatch.setattr(
                requests.sessions.Session,
                "request",
                blocked("requests.Session.request"),
            )

        try:
            import httpx
        except ImportError:
            pass
        else:
            monkeypatch.setattr(httpx.Client, "send", blocked("httpx.Client.send"))
            monkeypatch.setattr(
                httpx.AsyncClient, "send", blocked("httpx.AsyncClient.send")
            )

    def assert_quiet() -> None:
        assert attempts == [], f"shadow effect escaped through: {attempts}"

    return arm, assert_quiet


def test_real_application_registers_shadow_runtime(shadow_runtime):
    """Fail if the Flask factory has not integrated PR #411's safety layer."""
    assert shadow_runtime.mode == "shadow"
    assert hasattr(shadow_runtime, "effect_entrypoints"), (
        "runtime must expose the production effect entrypoint registry used "
        "by routes/workers"
    )


def test_shadow_runtime_has_no_live_executor_signer_or_write_credentials(shadow_runtime):
    """The shadow worker must not receive financial or provider write authority."""
    for attribute in ("live_executor", "signer", "write_credentials"):
        assert hasattr(shadow_runtime, attribute), (
            f"runtime must expose {attribute!r} explicitly for this contract test"
        )

    assert shadow_runtime.live_executor is None
    assert shadow_runtime.signer is None
    assert not shadow_runtime.write_credentials


def test_every_declared_effect_type_has_a_production_entrypoint(shadow_runtime):
    """Require proposal-only handlers for every canonical effect class."""
    entrypoints = shadow_runtime.effect_entrypoints
    assert isinstance(entrypoints, Mapping), "effect_entrypoints must be a mapping"
    missing = set(EFFECT_TYPES) - set(entrypoints)
    assert not missing, f"effect types bypassing the shadow registry: {sorted(missing)}"
    extra = set(entrypoints) - set(EFFECT_TYPES)
    assert not extra, f"unreviewed effect types registered: {sorted(extra)}"

    non_callable = sorted(kind for kind in EFFECT_TYPES if not callable(entrypoints[kind]))
    assert not non_callable, f"effect entrypoints are not callable: {non_callable}"


def test_effect_entrypoints_only_propose_and_cannot_reach_external_sinks(
    shadow_runtime, outbound_attempt_trap
):
    """Exercise each application effect entrypoint under a deny-egress trap."""
    arm_traps, assert_no_attempts = outbound_attempt_trap
    entrypoints = shadow_runtime.effect_entrypoints
    missing = set(EFFECT_TYPES) - set(entrypoints)
    assert not missing, f"cannot exercise unregistered effect types: {sorted(missing)}"

    arm_traps()
    for effect_type in sorted(EFFECT_TYPES):
        intent = _make_intent(effect_type)
        receipt = entrypoints[effect_type](intent)

        assert isinstance(receipt, EffectReceipt), (
            f"{effect_type} did not return a typed shadow receipt"
        )
        assert receipt.effect_id == intent.effect_id
        assert receipt.executed is False, f"{effect_type} reported an execution"
        assert receipt.status in RECEIPT_STATUSES, (
            f"{effect_type} returned non-shadow status {receipt.status!r}"
        )

    assert_no_attempts()


def test_entrypoint_receipts_never_claim_delivery_or_settlement(shadow_runtime):
    """Keep proposal evidence distinct from customer-visible/business outcomes."""
    entrypoints = shadow_runtime.effect_entrypoints
    missing = set(EFFECT_TYPES) - set(entrypoints)
    assert not missing, f"cannot validate receipts for unregistered effects: {sorted(missing)}"

    forbidden_claims = {"sent", "delivered", "published", "updated", "paid", "settled"}
    for effect_type in sorted(EFFECT_TYPES):
        receipt = entrypoints[effect_type](_make_intent(effect_type))
        assert receipt.executed is False
        assert receipt.status not in forbidden_claims
        assert receipt.status in RECEIPT_STATUSES


def test_effect_entrypoint_rejects_type_confusion(shadow_runtime):
    """A caller cannot send one effect class through another class's handler."""
    handler = shadow_runtime.effect_entrypoints["email.send"]
    with pytest.raises(ValueError, match="does not match entrypoint"):
        handler(_make_intent("crm.write"))


def test_unknown_effect_lookup_fails_closed(shadow_runtime):
    with pytest.raises(ValueError, match="unknown shadow effect type"):
        shadow_runtime.entrypoint_for("shell.exec")


def test_live_profile_cannot_be_constructed_in_agent_runtime():
    """The app process has no config switch that creates a live executor."""
    from sincor2.shadow_monitor.runtime import build_shadow_runtime

    with pytest.raises(RuntimeError, match="separately deployed executor"):
        build_shadow_runtime("live")
