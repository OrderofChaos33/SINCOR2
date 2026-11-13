"""Settle-before-serve guard (dev-watch item 79, Payload Tools postmortem).

Hedges: (a) verify-pass/settle-reject matrix must block work; (b) the
nonce-reuse trap (fresh auth minted for the same intent after a
nonce_already_used settle rejection) must not be treated as a new payment;
(c) verify OK + settle confirmed is the only happy path.
"""

from __future__ import annotations

import pytest

from sincor2.x402_payments import (
    SETTLE_BEFORE_SERVE,
    intent_outcome,
    require_settled,
    reset_intent_registry,
)


@pytest.fixture(autouse=True)
def _fresh_registry():
    reset_intent_registry()
    yield
    reset_intent_registry()


def _ref(intent: str = "intent-abc", verify_ok: bool = True,
         settle: dict | None = None) -> dict:
    ref: dict = {
        "intent_hash": intent,
        "verify": {"ok": verify_ok, "status": "verified"},
        "settle": settle,
    }
    return ref


def test_settle_before_serve_flag_is_true():
    assert SETTLE_BEFORE_SERVE is True


def test_verify_ok_settle_rejects_wrong_token_name():
    # Postmortem case (a): token name had to be exactly "USD Coin", not "USDC".
    payment = _ref(settle={"ok": False, "status": "rejected",
                           "reason": "invalid_payload",
                           "detail": "token name must be 'USD Coin', got 'USDC'"})
    assert require_settled(payment) is False
    assert intent_outcome("intent-abc") == "rejected"


def test_verify_ok_settle_rejects_below_minimum():
    # Postmortem case (b): undocumented $0.001 amount minimum.
    payment = _ref(settle={"ok": False, "status": "rejected",
                           "reason": "invalid_payload",
                           "detail": "amount 0.0005 below minimum 0.001"})
    assert require_settled(payment) is False
    assert intent_outcome("intent-abc") == "rejected"


def test_nonce_reuse_trap_fresh_auth_same_intent_blocked():
    # First authorization settles -> rejected with nonce_already_used.
    first = _ref(intent="intent-1",
                 settle={"ok": False, "status": "rejected",
                         "reason": "nonce_already_used"})
    assert require_settled(first) is False
    # The "fix": mint a fresh authorization for the SAME intent without
    # checking whether the first one settled. This must NOT be treated as
    # a new payment — approving it would convert a replay rejection into
    # a double payment.
    fresh_auth = _ref(intent="intent-1", verify_ok=True, settle=None)
    assert require_settled(fresh_auth) is False
    # The intent stays marked rejected until a real settle receipt arrives.
    assert intent_outcome("intent-1") == "rejected"


def test_nonce_reuse_trap_fresh_auth_with_own_settle_recovers():
    # If the fresh authorization genuinely settles (its own receipt), the
    # intent legitimately moves to settled — the trap is about serving
    # WITHOUT a settle receipt, not about blocking real settlement.
    first = _ref(intent="intent-2",
                 settle={"ok": False, "status": "rejected",
                         "reason": "nonce_already_used"})
    assert require_settled(first) is False
    fresh = _ref(intent="intent-2",
                 settle={"ok": True, "status": "settled",
                         "tx_hash": "0xabc"})
    assert require_settled(fresh) is True
    assert intent_outcome("intent-2") == "settled"


def test_happy_path_verify_ok_settle_confirmed():
    payment = _ref(settle={"ok": True, "status": "settled",
                           "tx_hash": "0xdef"})
    assert require_settled(payment) is True
    assert intent_outcome("intent-abc") == "settled"


def test_verify_only_never_serves_adversarial_bypass():
    # Adversarial: a serve path wired to accept a verify receipt alone.
    # Even with a clean registry, verify-without-settle must fail.
    payment = _ref(intent="fresh-intent", verify_ok=True, settle=None)
    assert require_settled(payment) is False


def test_verify_failed_never_serves():
    payment = _ref(verify_ok=False,
                   settle={"ok": True, "status": "settled"})
    assert require_settled(payment) is False


def test_genuinely_new_intent_unaffected_by_rejected_intent():
    bad = _ref(intent="bad-intent",
               settle={"ok": False, "reason": "nonce_already_used"})
    assert require_settled(bad) is False
    good = _ref(intent="good-intent",
                settle={"ok": True, "status": "confirmed"})
    assert require_settled(good) is True


def test_non_dict_payment_fails_closed():
    assert require_settled(None) is False  # type: ignore[arg-type]
    assert require_settled("receipt") is False  # type: ignore[arg-type]
