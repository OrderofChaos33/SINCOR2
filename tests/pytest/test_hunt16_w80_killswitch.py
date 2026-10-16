"""W-80: the treasury kill switch must gate Stripe provisioning and billing
fulfillment (fail-closed).

The HALT file / kill_switch_tripped() path existed but was never consulted on
the Stripe/billing money paths — tripping TREASURY_EXEC_HALT could not stop
them. These tests prove the wiring: halt tripped -> both paths refuse;
halt untripped -> both proceed exactly as before; malformed halt state ->
fail closed (tripped), never fail open.
"""
from __future__ import annotations

import pytest

import sincor2.agents.treasury_execution_agent as tea
from sincor2.governance.money_gate import (
    check_money_effect,
    reset_money_boundary,
    treasury_halt_tripped,
)


@pytest.fixture(autouse=True)
def _fresh_boundary():
    reset_money_boundary()
    yield
    reset_money_boundary()


@pytest.fixture
def _isolated_halt(tmp_path, monkeypatch):
    monkeypatch.setattr(tea, "HALT_FILE", tmp_path / "HALT")
    monkeypatch.setattr(tea, "HALT_ARMED", tmp_path / "HALT.armed")
    monkeypatch.setattr(tea, "AUDIT_LOG", tmp_path / "audit.jsonl")
    monkeypatch.setattr(tea, "INTENT_QUEUE", tmp_path / "queue.jsonl")
    return tmp_path


def _stripe_gate_call(**over):
    """Mirror the check_money_effect call in stripe_routes._process_payment_event."""
    kw = dict(
        effect_type="payment.transfer",
        payload={"event": "payment_completed", "amount_total": 5000,
                 "session_id": "test-sess"},
        agent_id="stripe_payment_event",
        risk_tier="high",
        idempotency_key="stripe-payment_completed-test-sess",
        target="stripe_event_provisioning",
        estimated_cost=50.0,
    )
    kw.update(over)
    return check_money_effect(**kw)


def _billing_gate_call(**over):
    """Mirror the check_money_effect call in billing.trigger_fulfillment."""
    kw = dict(
        effect_type="payment.transfer",
        payload={"order_id": "ord-test", "amount": 99,
                 "order_type": "subscription", "product_name": "Pro"},
        agent_id="billing_fulfillment",
        risk_tier="high",
        idempotency_key="fulfill-ord-test",
        target="order_fulfillment",
        estimated_cost=99.0,
    )
    kw.update(over)
    return check_money_effect(**kw)


# -- halt tripped: both money paths refuse --------------------------------------


def test_halt_tripped_denies_stripe_provisioning(_isolated_halt):
    tea.trip_kill_switch("w80 test")
    allowed, reason = _stripe_gate_call()
    assert allowed is False
    assert reason == "external_kill_switch_tripped"


def test_halt_tripped_denies_billing_fulfillment(_isolated_halt):
    tea.trip_kill_switch("w80 test")
    allowed, reason = _billing_gate_call()
    assert allowed is False
    assert reason == "external_kill_switch_tripped"


# -- halt untripped: behavior identical to before --------------------------------


def test_halt_untripped_stripe_provisioning_allowed(_isolated_halt):
    allowed, reason = _stripe_gate_call()
    assert allowed is True, reason
    assert reason.startswith("effect_boundary:would_execute")


def test_halt_untripped_billing_fulfillment_allowed(_isolated_halt):
    allowed, reason = _billing_gate_call()
    assert allowed is True, reason
    assert reason.startswith("effect_boundary:would_execute")


# -- treasury_halt_tripped(): fail-closed on every malformed state --------------


def test_halt_absent_means_not_tripped(_isolated_halt):
    assert treasury_halt_tripped() is False


def test_halt_present_with_marker_means_tripped(_isolated_halt):
    tea.trip_kill_switch("w80 test")
    assert treasury_halt_tripped() is True


def test_halt_malformed_content_fails_closed(_isolated_halt, tmp_path):
    (tmp_path / "HALT").write_text("garbage without marker\n", encoding="utf-8")
    assert treasury_halt_tripped() is True


def test_halt_missing_while_armed_fails_closed(_isolated_halt, tmp_path):
    # Armed sentinel exists but the halt file was deleted (tamper) -> tripped.
    (tmp_path / "HALT.armed").write_text("2026-10-14T00:00:00+00:00\n",
                                         encoding="utf-8")
    assert treasury_halt_tripped() is True


def test_halt_check_error_fails_closed(_isolated_halt, monkeypatch):
    def _boom():
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(tea, "kill_switch_tripped", _boom)
    assert treasury_halt_tripped() is True


def test_malformed_halt_denies_money_gate(_isolated_halt, tmp_path):
    (tmp_path / "HALT").write_text("tampered\n", encoding="utf-8")
    allowed, reason = _stripe_gate_call()
    assert allowed is False
    assert reason == "external_kill_switch_tripped"


# -- explicit override still honored --------------------------------------------


def test_explicit_true_still_denies_without_halt_file(_isolated_halt):
    allowed, reason = _stripe_gate_call(kill_switch_tripped=True)
    assert allowed is False
    assert reason == "external_kill_switch_tripped"


def test_explicit_false_bypasses_autodetect(_isolated_halt):
    # Documented escape hatch: an explicit False skips the HALT read.
    # No production caller uses it; the treasury agent passes the live value.
    tea.trip_kill_switch("w80 test")
    allowed, reason = _stripe_gate_call(kill_switch_tripped=False)
    assert allowed is True, reason


def test_halt_helper_never_raises(_isolated_halt, monkeypatch):
    monkeypatch.setattr(tea, "kill_switch_tripped",
                        lambda: (_ for _ in ()).throw(OSError("nope")))
    assert treasury_halt_tripped() is True  # fail closed, no raise
