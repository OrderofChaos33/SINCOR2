"""Batch-settlement vouchers (dev-watch item 81, Coinbase 2026-10-06).

Hedges: (a) a valid voucher redeems exactly once; (b) replay across
batches is rejected as already-redeemed; (c) expired vouchers rejected;
(d) a reused nonce with a different intent is rejected. Redemption is
idempotent and NEVER double-pays. Vouchers are promises, not settlement —
redemption does not relax settle-before-serve.
"""

from __future__ import annotations

import time

import pytest

from sincor2.x402_payments import (
    Voucher,
    redeem_voucher,
    reset_voucher_registry,
    validate_voucher,
)


@pytest.fixture(autouse=True)
def _fresh_registry():
    reset_voucher_registry()
    yield
    reset_voucher_registry()


def _voucher(voucher_id: str = "v-1", nonce: str = "n-1",
             intent: str = "ih-1", amount: float = 0.001,
             expiry_ts: float | None = None) -> Voucher:
    return Voucher(
        voucher_id=voucher_id,
        payer="0xPayer",
        pay_to="0xSeller",
        amount=amount,
        token="USDC",
        nonce=nonce,
        expiry_ts=expiry_ts if expiry_ts is not None else time.time() + 3600,
        intent_hash=intent,
    )


def test_valid_voucher_redeems_once():
    result = redeem_voucher(_voucher(), batch_id="batch-1")
    assert result["ok"] is True
    record = result["redemption"]
    assert record["voucher_id"] == "v-1"
    assert record["batch_id"] == "batch-1"
    # Redemption is NOT settlement: the record says so explicitly.
    assert record["settled"] is False
    assert result["settle_pending"] is True


def test_replay_across_batches_rejected_as_already_redeemed():
    first = redeem_voucher(_voucher(), batch_id="batch-1")
    assert first["ok"] is True
    original = first["redemption"]
    # Same voucher replayed in a later batch: rejected, original record
    # returned, never double-pays.
    replay = redeem_voucher(_voucher(), batch_id="batch-2")
    assert replay["ok"] is False
    assert replay["error"] == "already_redeemed"
    assert replay["double_pay"] is False
    assert replay["redemption"] == original
    assert replay["redemption"]["batch_id"] == "batch-1"


def test_double_redeem_race_never_double_pays():
    # Adversarial: two rapid redeem calls for the same voucher (race).
    r1 = redeem_voucher(_voucher(), batch_id="batch-1")
    r2 = redeem_voucher(_voucher(), batch_id="batch-1")
    assert r1["ok"] is True
    assert r2["ok"] is False
    assert r2["error"] == "already_redeemed"
    assert r2["double_pay"] is False
    # Exactly one redemption record exists.
    assert r2["redemption"] == r1["redemption"]


def test_expired_voucher_rejected():
    v = _voucher(expiry_ts=time.time() - 1)
    check = validate_voucher(v)
    assert check == {"ok": False, "error": "voucher_expired"}
    result = redeem_voucher(v)
    assert result["ok"] is False
    assert result["error"] == "voucher_expired"


def test_reused_nonce_different_intent_rejected():
    assert redeem_voucher(_voucher(voucher_id="v-1", nonce="n-1",
                                   intent="ih-1"))["ok"] is True
    # Same nonce, different voucher id and intent: nonce replay across
    # batches — rejected.
    check = validate_voucher(_voucher(voucher_id="v-2", nonce="n-1",
                                      intent="ih-2"))
    assert check == {"ok": False, "error": "nonce_already_redeemed"}
    result = redeem_voucher(_voucher(voucher_id="v-2", nonce="n-1",
                                     intent="ih-2"))
    assert result["ok"] is False
    assert result["error"] == "nonce_already_redeemed"
    assert result["double_pay"] is False


def test_zero_amount_rejected():
    result = redeem_voucher(_voucher(amount=0.0))
    assert result["ok"] is False
    assert result["error"] == "invalid_amount"


def test_missing_nonce_rejected():
    result = redeem_voucher(_voucher(nonce=""))
    assert result["ok"] is False
    assert result["error"] == "nonce_required"


def test_redemption_does_not_create_settle_receipt():
    # The hedge contract: redeeming must not mint anything require_settled()
    # would accept. require_settled() needs a payment reference with a
    # settle receipt; a redemption record has none and "settled": False.
    from sincor2.x402_payments import require_settled
    result = redeem_voucher(_voucher())
    assert result["ok"] is True
    pseudo_payment = {"intent_hash": "ih-1",
                      "verify": {"ok": True},
                      "settle": result["redemption"]}
    assert require_settled(pseudo_payment) is False
