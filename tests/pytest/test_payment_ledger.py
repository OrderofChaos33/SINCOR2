"""Tests for the canonical payment ledger (PAY-01).

Self-contained: uses temp sqlite files, no app, no network.
Run: PYTHONPATH=src:. <venv>/bin/python -m pytest tests/pytest/test_payment_ledger.py --noconftest -q
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from sincor2.payment_ledger import (
    DuplicatePaymentError,
    FinalityNotConfirmed,
    IllegalPaymentTransition,
    PaymentConflictError,
    PaymentLedgerStore,
    PaymentNotFound,
    PaymentRecord,
    PaymentState,
    allowed_transitions,
    from_a2a_payment,
    from_agent_billing,
    from_checkout_row,
    from_legacy_row,
    from_stripe_event,
    from_x402_challenge,
    is_paid_state,
    list_alerts,
    record_alert,
)


@pytest.fixture()
def store(tmp_path: Path) -> PaymentLedgerStore:
    return PaymentLedgerStore(tmp_path / "ledger.db")


def _verified_onchain_record(pid: str = "pay-test-1") -> PaymentRecord:
    rec = PaymentRecord(
        payment_id=pid,
        owner="0xPayer",
        amount_atomic=500_000_000,
        amount_display=500.0,
        currency="AXM",
        provider="a2a",
        provider_evidence={"tx_hash": "0x" + "ab" * 32},
    )
    rec.transition(PaymentState.PENDING_VERIFICATION, actor="t", reason="t")
    rec.transition(PaymentState.VERIFIED, actor="t", reason="t")
    return rec


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


def test_illegal_transitions_raise():
    rec = PaymentRecord(
        payment_id="x", owner="o", amount_atomic=1, amount_display=0.0,
        currency="USD", provider="stripe",
    )
    with pytest.raises(IllegalPaymentTransition):
        rec.transition(PaymentState.FULFILLED, actor="t")  # created -> fulfilled
    with pytest.raises(IllegalPaymentTransition):
        rec.transition(PaymentState.SETTLED, actor="t")  # created -> settled


def test_refunded_is_terminal():
    rec = PaymentRecord(
        payment_id="x", owner="o", amount_atomic=1, amount_display=0.0,
        currency="USD", provider="stripe",
    )
    for s, actor in [
        (PaymentState.PENDING_VERIFICATION, "t"),
        (PaymentState.VERIFIED, "t"),
        (PaymentState.SETTLED, "t"),
        (PaymentState.REFUNDED, "t"),
    ]:
        rec.transition(s, actor=actor)
    with pytest.raises(IllegalPaymentTransition):
        rec.transition(PaymentState.SETTLED, actor="t")  # refunded -> settled


def test_full_happy_path_order():
    rec = _verified_onchain_record()
    rec.transition(PaymentState.AWAITING_FINALITY, actor="t")
    rec.transition(PaymentState.SETTLED, actor="t")
    rec.transition(PaymentState.FULFILLED, actor="t")
    assert [h.to_state for h in rec.history] == [
        "pending_verification", "verified", "awaiting_finality", "settled", "fulfilled",
    ]
    assert is_paid_state(rec.state)
    assert not is_paid_state(PaymentState.VERIFIED)  # verified is NOT paid


def test_allowed_transitions_table_shape():
    table = allowed_transitions()
    assert "awaiting_finality" in table["verified"]
    assert "settled" in table["awaiting_finality"]
    assert table["refunded"] == []


# ---------------------------------------------------------------------------
# Concurrency: exactly one transition wins
# ---------------------------------------------------------------------------


def test_concurrent_transitions_exactly_one_wins(store: PaymentLedgerStore):
    store.ingest(_verified_onchain_record("pay-race"))
    wins: list[int] = []
    errors: list[str] = []

    def attempt(i: int) -> None:
        try:
            time.sleep(0.001 * (i % 5))
            store.transition_payment(
                "pay-race", PaymentState.AWAITING_FINALITY,
                actor=f"racer-{i}", reason="race",
            )
            wins.append(i)
        except (PaymentConflictError, IllegalPaymentTransition) as exc:
            errors.append(type(exc).__name__)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(wins) == 1, f"expected exactly one winner, got {wins}"
    final = store.get("pay-race")
    assert final.state == PaymentState.AWAITING_FINALITY
    assert final.version == 3  # 2 mapper transitions + 1 winning transition
    # History has exactly one awaiting_finality entry — no double-write.
    assert sum(1 for h in final.history if h.to_state == "awaiting_finality") == 1


def test_expected_version_conflict(store: PaymentLedgerStore):
    store.ingest(_verified_onchain_record("pay-ver"))
    rec = store.get("pay-ver")
    with pytest.raises(PaymentConflictError):
        store.transition_payment(
            "pay-ver", PaymentState.AWAITING_FINALITY,
            actor="t", expected_version=rec.version + 99,
        )


# ---------------------------------------------------------------------------
# Double-ingest / skip-finality guards
# ---------------------------------------------------------------------------


def test_double_ingest_same_tx_rejected(store: PaymentLedgerStore):
    store.ingest(_verified_onchain_record("pay-dup-1"))
    twin = _verified_onchain_record("pay-dup-2")  # same tx_hash in evidence
    with pytest.raises(DuplicatePaymentError):
        store.ingest(twin)
    assert store.find_by_tx_hash("a2a", "0x" + "ab" * 32).payment_id == "pay-dup-1"


def test_skip_finality_blocked(store: PaymentLedgerStore):
    """verified -> settled on an onchain payment without finality must fail."""
    store.ingest(_verified_onchain_record("pay-skip"))
    with pytest.raises(FinalityNotConfirmed):
        store.transition_payment("pay-skip", PaymentState.SETTLED, actor="attacker")
    assert store.get("pay-skip").state == PaymentState.VERIFIED


def test_unknown_payment_raises(store: PaymentLedgerStore):
    with pytest.raises(PaymentNotFound):
        store.get("nope")
    with pytest.raises(PaymentNotFound):
        store.transition_payment("nope", PaymentState.FAILED, actor="t")


# ---------------------------------------------------------------------------
# History immutability
# ---------------------------------------------------------------------------


def test_history_append_only_no_update_or_delete(store: PaymentLedgerStore, tmp_path: Path):
    store.ingest(_verified_onchain_record("pay-hist"))
    store.transition_payment("pay-hist", PaymentState.AWAITING_FINALITY, actor="t")

    raw = sqlite3.connect(str(tmp_path / "ledger.db"))
    with pytest.raises(sqlite3.IntegrityError):
        raw.execute("UPDATE payment_history SET to_state='settled' WHERE payment_id='pay-hist'")
    with pytest.raises(sqlite3.IntegrityError):
        raw.execute("DELETE FROM payment_history WHERE payment_id='pay-hist'")
    raw.close()

    # History still intact after the rejected tampering attempts.
    hist = store.get("pay-hist").history
    assert [h.to_state for h in hist] == ["pending_verification", "verified", "awaiting_finality"]


# ---------------------------------------------------------------------------
# Restartability
# ---------------------------------------------------------------------------


def test_store_survives_restart(tmp_path: Path):
    db = tmp_path / "ledger.db"
    s1 = PaymentLedgerStore(db)
    s1.ingest(_verified_onchain_record("pay-restart"))
    s1.transition_payment("pay-restart", PaymentState.AWAITING_FINALITY, actor="t")
    del s1

    s2 = PaymentLedgerStore(db)  # new process, same file
    rec = s2.get("pay-restart")
    assert rec.state == PaymentState.AWAITING_FINALITY
    assert len(rec.history) == 3
    assert rec.provider_evidence["tx_hash"] == "0x" + "ab" * 32


# ---------------------------------------------------------------------------
# Mappers — realistic fixtures, no data loss
# ---------------------------------------------------------------------------


def _stripe_fixture() -> dict:
    return {
        "event": "checkout.session.completed",
        "session_id": "cs_test_a1b2c3d4e5",
        "customer_email": "buyer@example.com",
        "plan_name": "Professional",
        "plan_id": "professional",
        "amount": 997.0,
        "currency": "usd",
        "payment_status": "completed",
        "subscription_id": "sub_9f8e7d",
    }


def test_from_stripe_event():
    rec = from_stripe_event(_stripe_fixture())
    assert rec.provider == "stripe"
    assert rec.owner == "buyer@example.com"
    assert rec.amount_atomic == 99700
    assert rec.amount_display == 997.0
    assert rec.currency == "USD"
    assert rec.provider_evidence["stripe_session_id"] == "cs_test_a1b2c3d4e5"
    assert rec.provider_evidence["subscription_id"] == "sub_9f8e7d"
    assert rec.entitlements["order_id"] == "STRIPE-cs_test_a1b2c3d4e5"
    assert rec.state == PaymentState.VERIFIED  # completed -> verified, never settled
    assert not is_paid_state(rec.state)


def test_from_stripe_event_failed():
    fx = _stripe_fixture()
    fx["event"] = "payment_intent.payment_failed"
    fx["payment_status"] = "failed"
    rec = from_stripe_event(fx)
    assert rec.state == PaymentState.FAILED


def _x402_fixture() -> dict:
    return {
        "challenge_id": "ch_9f2c41",
        "resource_id": "bi-report",
        "amount_atomic": 500_000_000,
        "amount_display": 500.0,
        "payer_wallet": "0xPayerWallet00000000000000000000000000000001",
        "status": "fulfilled",
        "tx_hash": "0x" + "cd" * 32,
        "access_token": "tok_SECRET_must_not_leak",
        "created_at": "2026-10-09T20:00:00+00:00",
        "expires_at": "2026-10-09T21:00:00+00:00",
        "fulfilled_at": "2026-10-09T20:05:00+00:00",
    }


def test_from_x402_challenge():
    rec = from_x402_challenge(_x402_fixture())
    assert rec.provider == "x402"
    assert rec.owner == "0xPayerWallet00000000000000000000000000000001"
    assert rec.amount_atomic == 500_000_000
    assert rec.provider_evidence["tx_hash"] == "0x" + "cd" * 32
    assert rec.provider_evidence["challenge_id"] == "ch_9f2c41"
    assert "access_token" not in rec.provider_evidence  # secrets never mirrored
    assert rec.entitlements["resource_id"] == "bi-report"
    assert rec.state == PaymentState.VERIFIED  # fulfilled+tx -> verified, not settled


def test_from_x402_challenge_fulfilled_without_tx_quarantined():
    fx = _x402_fixture()
    fx["tx_hash"] = None
    rec = from_x402_challenge(fx)
    assert rec.state == PaymentState.QUARANTINED


def _a2a_fixture() -> dict:
    return {
        "task_id": "task-42",
        "bid_id": "bid-7f3a",
        "agent_id": "agent-neo",
        "amount_wei": 1_000_000_000_000_000_000,
        "token": "AXM",
        "tx_hash": "0x" + "ef" * 32,
        "status": "confirmed",
        "commitment": "0xcommitment",
    }


def test_from_a2a_payment():
    rec = from_a2a_payment(_a2a_fixture())
    assert rec.provider == "a2a"
    assert rec.owner == "agent-neo"
    assert rec.amount_atomic == 10**18
    assert rec.currency == "AXM"
    assert rec.provider_evidence["tx_hash"] == "0x" + "ef" * 32
    assert rec.provider_evidence["task_id"] == "task-42"
    assert rec.entitlements["bid_id"] == "bid-7f3a"
    assert rec.state == PaymentState.VERIFIED


def test_from_a2a_payment_slashed_failed():
    fx = _a2a_fixture()
    fx["status"] = "slashed"
    assert from_a2a_payment(fx).state == PaymentState.FAILED


def _billing_fixture() -> dict:
    return {
        "ts": "2026-10-09T20:00:00+00:00",
        "type": "platform_payment",
        "tx_hash": "0x" + "11" * 32,
        "payer": "0xPayer00000000000000000000000000000000000002",
        "token": "AXM",
        "amount_atomic": 250_000_000,
        "amount_display": 250.0,
        "treasury": "0x09e2890000000000000000000000000000009612ac",
        "product_name": "Business Intelligence Report",
        "plan_id": "report",
        "payment_id": "pay-billing-1",
    }


def test_from_agent_billing():
    rec = from_agent_billing(_billing_fixture())
    assert rec.provider == "manual"
    assert rec.owner == "0xPayer00000000000000000000000000000000000002"
    assert rec.amount_atomic == 250_000_000
    assert rec.provider_evidence["tx_hash"] == "0x" + "11" * 32
    assert rec.provider_evidence["treasury"].endswith("9612ac")
    assert rec.state == PaymentState.VERIFIED


def test_from_agent_billing_without_tx_quarantined():
    fx = _billing_fixture()
    fx["tx_hash"] = None
    assert from_agent_billing(fx).state == PaymentState.QUARANTINED


def test_from_checkout_row():
    row = {
        "payment_id": "co_77aa",
        "plan_id": "intel",
        "product_name": "Professional",
        "token": "AXM",
        "amount_atomic": 149_000_000,
        "amount_display": 149.0,
        "usd_reference": 149.0,
        "payer_wallet": "0xWallet000000000000000000000000000000000003",
        "status": "completed",
        "tx_hash": "0x" + "22" * 32,
        "idempotency_key": "idem-1",
    }
    rec = from_checkout_row(row)
    assert rec.provider == "checkout"
    assert rec.state == PaymentState.VERIFIED
    assert rec.provider_evidence["idempotency_key"] == "idem-1"
    assert rec.provider_evidence["tx_hash"] == "0x" + "22" * 32


def test_from_legacy_row_always_quarantined_never_paid():
    rec = from_legacy_row({"weird": "shape", "amount": 12345, "note": "migrated 2024"},
                          source="orders_migration")
    assert rec.state == PaymentState.QUARANTINED
    assert not is_paid_state(rec.state)
    assert rec.provider_evidence["raw"]["weird"] == "shape"
    assert rec.provider_evidence["source"] == "orders_migration"
    # Quarantine exits are only re-verify or fail — never a paid state.
    exits = allowed_transitions()["quarantined"]
    assert set(exits) == {"pending_verification", "failed"}


def test_mappers_ingest_end_to_end(store: PaymentLedgerStore):
    for rec in [
        from_stripe_event(_stripe_fixture()),
        from_x402_challenge(_x402_fixture()),
        from_a2a_payment(_a2a_fixture()),
        from_agent_billing(_billing_fixture()),
    ]:
        store.ingest(rec)
    assert store.count() == 4
    loaded = store.get("x402-ch_9f2c41")
    assert loaded.provider_evidence["tx_hash"] == "0x" + "cd" * 32


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


def test_alert_roundtrip(store: PaymentLedgerStore):
    alert = record_alert(store, "pay-1", "test_kind", "something happened",
                         {"k": "v"})
    alerts = list_alerts(store, "pay-1")
    assert len(alerts) == 1
    assert alerts[0].alert_id == alert.alert_id
    assert alerts[0].kind == "test_kind"
    assert alerts[0].details == {"k": "v"}
    assert list_alerts(store, "other") == []


def test_serialization_roundtrip():
    rec = _verified_onchain_record()
    clone = PaymentRecord.from_dict(rec.to_dict())
    assert clone.payment_id == rec.payment_id
    assert clone.state == rec.state
    assert len(clone.history) == len(rec.history)
    assert clone.provider_evidence == rec.provider_evidence
