"""Dev-watch items 309 + 310: x402 facilitator error shapes.

309 (x402 Go #3937): facilitators that answer verification failures with
a bare HTTP 500 drop the typed error. The client must classify failures
by the error REASON parsed from the response BODY — never by HTTP status
alone:
- deterministically-invalid payments (bad signature, expired
  authorization, insufficient balance) are never retried as if transient;
- a bare 500 (no parseable reason) is UNKNOWN — never "invalid".

310 (x402 Go #3778): a facilitator answers settlement_pending with a 500
and drops the broadcast hash. The client must never discard a non-2xx
body: when it carries a settlement_pending reason with a broadcast hash,
the client recovers the hash, reconciles it onchain (via the injected
reconciler) BEFORE any retry, and never submits a second payment for a
pending settlement.

The failure-class matrix is the contract: every verification-failure
class maps to exactly one client action — CORRECT_AND_RETRY, ABORT, or
HOLD. All network is mocked; no real paid calls.
"""

import json
import time

import pytest

import sincor2.x402_buyer as x402_buyer_module
from sincor2.x402_buyer import (
    AmbiguousPayment,
    ExpiredAuthorizationError,
    FacilitatorFailure,
    FailureAction,
    InsufficientFundsError,
    InvalidSignatureError,
    PaymentRefusedError,
    SettlementFailedError,
    SettlementPendingError,
    UnknownFacilitatorFailure,
    X402Buyer,
    classify_paid_leg_failure,
    failure_action_for_error,
    BASE_CHAIN_REF,
    USDC_BASE_ADDRESS,
)

WALLET = "0x" + "11" * 20
PAY_TO = "0x" + "22" * 20
TX_HASH = "0x" + "ab" * 32


def _fake_signer(typed_data):
    assert typed_data["domain"]["verifyingContract"] == USDC_BASE_ADDRESS
    assert typed_data["primaryType"] == "TransferWithAuthorization"
    return "0x" + "ab" * 65


def _challenge_402(amount_atomic=5000, **overrides):
    now = int(time.time())
    body = {
        "x402Version": 1,
        "scheme": "exact",
        "network": BASE_CHAIN_REF,
        "maxAmountRequired": amount_atomic,
        "asset": USDC_BASE_ADDRESS,
        "payTo": PAY_TO,
        "nonce": "0x" + "cc" * 32,
        "validAfter": 0,
        "validBefore": now + 600,
        "resource": "/api/base/block-number",
        "description": "test",
    }
    body.update(overrides)
    return body


class _MockBuyer(X402Buyer):
    """Overrides _do_request with a scripted response queue."""

    def __init__(self, *a, responses=None, signer_fn=None, **kw):
        kw.setdefault("wallet_address", WALLET)
        kw.setdefault("signer_fn", signer_fn or _fake_signer)
        super().__init__(*a, **kw)
        self._responses = list(responses or [])
        self.requests = []

    def _do_request(self, url, method, body, headers):
        self.requests.append({"url": url, "method": method, "headers": headers})
        status, resp_headers, raw = self._responses.pop(0)
        return status, resp_headers, raw


def _resp(status, body_dict=None, headers=None):
    raw = body_dict if isinstance(body_dict, bytes) else json.dumps(body_dict or {}).encode()
    return (status, headers or {}, raw)


def _counting_signer():
    calls = []

    def signer(typed_data):
        calls.append(typed_data)
        return _fake_signer(typed_data)

    return signer, calls


URL = "https://api.mach.gallery/api/base/block-number"


# ---------------------------------------------------------------------------
# 309 — failure-class matrix (pure classification, reason beats status)
# ---------------------------------------------------------------------------

MATRIX = [
    # (status, body, expected klass, expected action)
    (402, {"error": "invalid_signature"}, "invalid_signature", FailureAction.ABORT),
    (500, {"error": "invalid_signature"}, "invalid_signature", FailureAction.ABORT),
    (500, {"error": "bad_signature"}, "invalid_signature", FailureAction.ABORT),
    (503, {"error": "signature_invalid"}, "invalid_signature", FailureAction.ABORT),
    (402, {"error": "insufficient_funds"}, "insufficient_funds", FailureAction.ABORT),
    (500, {"error": "insufficient_balance"}, "insufficient_funds", FailureAction.ABORT),
    (402, {"error": "insufficient_amount"}, "insufficient_funds", FailureAction.ABORT),
    (402, {"error": "authorization_expired"}, "expired_authorization", FailureAction.CORRECT_AND_RETRY),
    (500, {"error": "expired"}, "expired_authorization", FailureAction.CORRECT_AND_RETRY),
    (500, {"error": "authorization expired"}, "expired_authorization", FailureAction.CORRECT_AND_RETRY),
    (402, {"error": "settlement_failed"}, "settlement_failed", FailureAction.HOLD),
    (500, {"error": "settle_failed"}, "settlement_failed", FailureAction.HOLD),
    (500, {"error": "settlement_pending", "txHash": TX_HASH}, "settlement_pending", FailureAction.HOLD),
    (402, {"error": "settlement_pending", "txHash": TX_HASH}, "settlement_pending", FailureAction.HOLD),
    # Bare / unparseable / unrecognized failures are UNKNOWN — never invalid.
    (500, {}, "unknown", FailureAction.HOLD),
    (500, b"not json{{{", "unknown", FailureAction.HOLD),
    (503, {"error": "upstream_down"}, "unknown", FailureAction.HOLD),
    (400, {"error": "some_new_reason"}, "unknown", FailureAction.HOLD),
]


@pytest.mark.parametrize("status,body,klass,action", MATRIX)
def test_309_failure_class_matrix(status, body, klass, action):
    failure = classify_paid_leg_failure(status, _resp(status, body)[2])
    assert isinstance(failure, FacilitatorFailure)
    assert failure.klass == klass
    assert failure.action is action
    assert failure.status == status


def test_309_matrix_covers_each_action_exactly_once_per_class():
    # Every class in the matrix maps to exactly one action (no class is
    # reachable with two different actions).
    by_class = {}
    for _status, _body, klass, action in MATRIX:
        by_class.setdefault(klass, set()).add(action)
    assert all(len(actions) == 1 for actions in by_class.values())
    assert set(by_class) == {
        "invalid_signature",
        "insufficient_funds",
        "expired_authorization",
        "settlement_failed",
        "settlement_pending",
        "unknown",
    }


def test_309_bare_500_is_unknown_never_invalid():
    failure = classify_paid_leg_failure(500, b"{}")
    assert failure.klass == "unknown"
    assert failure.action is FailureAction.HOLD
    assert failure.reason == ""


def test_309_reason_normalization_matches_402_classifier():
    # Same spellings the item-261 body classifier accepts.
    failure = classify_paid_leg_failure(500, _resp(500, {"error": "Invalid Signature"})[2])
    assert failure.klass == "invalid_signature"


# ---------------------------------------------------------------------------
# 310 — broadcast-hash recovery from non-2xx bodies
# ---------------------------------------------------------------------------


def test_310_pending_hash_recovered_from_body():
    failure = classify_paid_leg_failure(
        500, _resp(500, {"error": "settlement_pending", "txHash": TX_HASH})[2]
    )
    assert failure.klass == "settlement_pending"
    assert failure.tx_hash == TX_HASH


@pytest.mark.parametrize(
    "body",
    [
        {"error": "settlement_pending", "tx_hash": TX_HASH},
        {"error": "settlement_pending", "transactionHash": TX_HASH},
        {"error": "settlement_pending", "settlement": {"txHash": TX_HASH}},
        {"error": "settlement_pending", "txHash": TX_HASH.upper().replace("0X", "0x")},
    ],
)
def test_310_hash_key_variants(body):
    failure = classify_paid_leg_failure(500, _resp(500, body)[2])
    assert failure.tx_hash == TX_HASH


def test_310_garbage_hash_not_trusted():
    failure = classify_paid_leg_failure(
        500, _resp(500, {"error": "settlement_pending", "txHash": "0xZZZ"})[2]
    )
    assert failure.klass == "settlement_pending"
    assert failure.tx_hash is None


def test_310_pending_without_hash_still_holds():
    failure = classify_paid_leg_failure(500, _resp(500, {"error": "settlement_pending"})[2])
    assert failure.klass == "settlement_pending"
    assert failure.action is FailureAction.HOLD
    assert failure.tx_hash is None


# ---------------------------------------------------------------------------
# failure_action_for_error — typed errors map to the same matrix actions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc,action",
    [
        (InvalidSignatureError("invalid_signature", "m"), FailureAction.ABORT),
        (InsufficientFundsError("insufficient_funds", "m"), FailureAction.ABORT),
        (ExpiredAuthorizationError("authorization_expired", "m"), FailureAction.CORRECT_AND_RETRY),
        (SettlementFailedError("settlement_failed", "m"), FailureAction.HOLD),
        (SettlementPendingError("settlement_pending", "m", tx_hash=TX_HASH), FailureAction.HOLD),
        (PaymentRefusedError("some_new_reason", "m"), FailureAction.HOLD),
        (AmbiguousPayment("m"), FailureAction.HOLD),
    ],
)
def test_failure_action_for_error(exc, action):
    assert failure_action_for_error(exc) is action


# ---------------------------------------------------------------------------
# fetch() wiring — 309: reason beats status; invalid never retried
# ---------------------------------------------------------------------------


def test_309_fetch_bare_500_is_unknown_hold_not_invalid():
    buyer = _MockBuyer(
        responses=[_resp(402, _challenge_402()), _resp(500, {})],
    )
    with pytest.raises(UnknownFacilitatorFailure) as exc_info:
        buyer.fetch(URL)
    assert not isinstance(exc_info.value, InvalidSignatureError)
    assert not isinstance(exc_info.value, InsufficientFundsError)
    assert failure_action_for_error(exc_info.value) is FailureAction.HOLD
    assert len(buyer.requests) == 2  # no retry of any kind


def test_309_fetch_500_with_invalid_signature_reason_aborts():
    signer, signed_calls = _counting_signer()
    buyer = _MockBuyer(
        signer_fn=signer,
        responses=[
            _resp(402, _challenge_402()),
            _resp(500, {"error": "invalid_signature"}),
        ],
    )
    with pytest.raises(InvalidSignatureError) as exc_info:
        buyer.fetch(URL)
    assert exc_info.value.reason == "invalid_signature"
    assert failure_action_for_error(exc_info.value) is FailureAction.ABORT
    assert len(buyer.requests) == 2  # deterministically invalid: no retry
    assert len(signed_calls) == 1


def test_309_fetch_500_with_insufficient_balance_aborts():
    buyer = _MockBuyer(
        responses=[
            _resp(402, _challenge_402()),
            _resp(500, {"error": "insufficient_balance"}),
        ],
    )
    with pytest.raises(InsufficientFundsError):
        buyer.fetch(URL)
    assert len(buyer.requests) == 2


def test_309_fetch_500_settlement_failed_holds():
    buyer = _MockBuyer(
        responses=[
            _resp(402, _challenge_402()),
            _resp(500, {"error": "settlement_failed"}),
        ],
    )
    with pytest.raises(SettlementFailedError) as exc_info:
        buyer.fetch(URL)
    assert failure_action_for_error(exc_info.value) is FailureAction.HOLD
    assert len(buyer.requests) == 2


# ---------------------------------------------------------------------------
# fetch() wiring — 309: expired authorization = correct-and-retry, once
# ---------------------------------------------------------------------------


def test_309_fetch_expired_authorization_corrected_and_retried_once():
    signer, signed_calls = _counting_signer()
    buyer = _MockBuyer(
        signer_fn=signer,
        responses=[
            _resp(402, _challenge_402()),
            _resp(500, {"error": "authorization_expired"}),
            _resp(200, {"block_number": 999}),
        ],
    )
    out = buyer.fetch(URL)
    assert out["ok"] and out["paid"] is True
    assert len(buyer.requests) == 3  # unpaid + paid + ONE corrected paid retry
    assert len(signed_calls) == 2  # original + corrected signature
    first = json.loads(buyer.requests[1]["headers"]["X-PAYMENT"])["payload"]["authorization"]
    second = json.loads(buyer.requests[2]["headers"]["X-PAYMENT"])["payload"]["authorization"]
    # Correction is expiry-only: same nonce, same amount, same recipient —
    # the corrected authorization cannot double-pay alongside the expired
    # one (the token contract refuses the expired one after validBefore).
    assert second["nonce"] == first["nonce"]
    assert second["value"] == first["value"]
    assert second["to"] == first["to"]
    assert second["validBefore"] > first["validBefore"]
    assert second["validBefore"] > int(time.time())


def test_309_fetch_expired_twice_never_loops():
    signer, signed_calls = _counting_signer()
    buyer = _MockBuyer(
        signer_fn=signer,
        responses=[
            _resp(402, _challenge_402()),
            _resp(500, {"error": "authorization_expired"}),
            _resp(500, {"error": "authorization_expired"}),
        ],
    )
    with pytest.raises(ExpiredAuthorizationError) as exc_info:
        buyer.fetch(URL)
    assert failure_action_for_error(exc_info.value) is FailureAction.CORRECT_AND_RETRY
    assert len(buyer.requests) == 3  # exactly one correction, no loop
    assert len(signed_calls) == 2


# ---------------------------------------------------------------------------
# fetch() wiring — 310: pending settlement reconciled, never re-paid
# ---------------------------------------------------------------------------


def test_310_fetch_pending_500_recovers_hash_reconciles_no_second_payment():
    signer, signed_calls = _counting_signer()
    reconciliations = []

    def reconciler(tx_hash):
        reconciliations.append(tx_hash)
        return {"state": "pending", "confirmations": 0}

    buyer = _MockBuyer(
        signer_fn=signer,
        settlement_reconciler=reconciler,
        responses=[
            _resp(402, _challenge_402()),
            _resp(500, {"error": "settlement_pending", "txHash": TX_HASH}),
        ],
    )
    with pytest.raises(SettlementPendingError) as exc_info:
        buyer.fetch(URL)
    exc = exc_info.value
    assert exc.tx_hash == TX_HASH  # hash recovered from the 500 body
    assert reconciliations == [TX_HASH]  # reconciled onchain before any retry
    assert exc.reconciliation == {"state": "pending", "confirmations": 0}
    assert failure_action_for_error(exc) is FailureAction.HOLD
    assert len(buyer.requests) == 2  # NO second payment submitted
    assert len(signed_calls) == 1  # NO second authorization signed


def test_310_fetch_pending_on_402_also_reconciled():
    reconciliations = []

    def reconciler(tx_hash):
        reconciliations.append(tx_hash)
        return {"state": "confirmed"}

    buyer = _MockBuyer(
        settlement_reconciler=reconciler,
        responses=[
            _resp(402, _challenge_402()),
            _resp(402, {"error": "settlement_pending", "txHash": TX_HASH}),
        ],
    )
    with pytest.raises(SettlementPendingError) as exc_info:
        buyer.fetch(URL)
    assert exc_info.value.tx_hash == TX_HASH
    assert reconciliations == [TX_HASH]
    assert len(buyer.requests) == 2


def test_310_fetch_pending_without_reconciler_still_carries_hash():
    buyer = _MockBuyer(
        responses=[
            _resp(402, _challenge_402()),
            _resp(500, {"error": "settlement_pending", "txHash": TX_HASH}),
        ],
    )
    with pytest.raises(SettlementPendingError) as exc_info:
        buyer.fetch(URL)
    assert exc_info.value.tx_hash == TX_HASH
    assert exc_info.value.reconciliation is None
    assert len(buyer.requests) == 2


def test_310_fetch_pending_reconciler_failure_never_retries():
    def broken_reconciler(tx_hash):
        raise RuntimeError("rpc down")

    buyer = _MockBuyer(
        settlement_reconciler=broken_reconciler,
        responses=[
            _resp(402, _challenge_402()),
            _resp(500, {"error": "settlement_pending", "txHash": TX_HASH}),
        ],
    )
    with pytest.raises(SettlementPendingError) as exc_info:
        buyer.fetch(URL)
    # Reconciliation failure is recorded on the error, never turned into
    # a retry or a second payment.
    assert "error" in (exc_info.value.reconciliation or {})
    assert len(buyer.requests) == 2


# ---------------------------------------------------------------------------
# Repair round (adversarial review NOT CONFIRMED on 110be4d8):
# FLAW 1 — the corrected validity window must STRICTLY extend the rejected
# one; at the 1h cap boundary that is impossible, so the client must HOLD
# (raise the expired-authorization error) instead of resending a window
# that merely equals — or is shorter than — the rejected one.
# FLAW 2 — the W-75 in-flight journal must survive a FAILED re-sign: it is
# replaced only after the corrected signature exists, never cleared first.
# The buyer's clock is frozen so the boundary arithmetic is exact.
# ---------------------------------------------------------------------------

FROZEN_NOW = 1_800_000_000


class _FrozenClock:
    @staticmethod
    def time():
        return FROZEN_NOW


def _freeze_buyer_clock(monkeypatch):
    monkeypatch.setattr(x402_buyer_module, "time", _FrozenClock)


def _paid_authorization(buyer, request_index):
    header = buyer.requests[request_index]["headers"]["X-PAYMENT"]
    return json.loads(header)["payload"]["authorization"]


@pytest.mark.parametrize("offset", [3599, 3600])
def test_309_repair_window_at_cap_cannot_extend_holds(monkeypatch, offset):
    # rejected_vb = now+3599: the old formula's corrected window EQUALS
    # the rejected one. rejected_vb = now+3600: it is strictly SHORTER.
    # Neither may be resent — the failure is uncorrectable, so fetch()
    # raises the expired-authorization error with NO second request and
    # NO second signature.
    _freeze_buyer_clock(monkeypatch)
    signer, signed_calls = _counting_signer()
    rejected_vb = FROZEN_NOW + offset
    buyer = _MockBuyer(
        signer_fn=signer,
        responses=[
            _resp(402, _challenge_402(validBefore=rejected_vb)),
            _resp(500, {"error": "authorization_expired"}),
            _resp(200, {"block_number": 999}),
        ],
    )
    raised = None
    try:
        buyer.fetch(URL)
    except ExpiredAuthorizationError as exc:
        raised = exc
    if raised is None and len(buyer.requests) == 3:
        # A corrected authorization was resent: capture both windows so
        # the failure shows the broken invariant directly.
        resent_vb = int(_paid_authorization(buyer, 2)["validBefore"])
        assert resent_vb > rejected_vb, (
            f"corrected validBefore {resent_vb} does not strictly extend "
            f"rejected validBefore {rejected_vb}"
        )
    assert raised is not None, (
        "uncorrectable window must HOLD with ExpiredAuthorizationError, "
        "not resend a non-extended window"
    )
    assert failure_action_for_error(raised) is FailureAction.CORRECT_AND_RETRY
    assert len(buyer.requests) == 2  # NO second payment request was sent
    assert len(signed_calls) == 1  # NO corrected authorization was signed


def test_309_repair_window_just_inside_cap_still_extends(monkeypatch):
    # rejected_vb = now+2999: a strict extension fits exactly at the cap
    # (now+3599), so the correction still goes through — the HOLD above
    # must not over-block correctable expiries.
    _freeze_buyer_clock(monkeypatch)
    signer, signed_calls = _counting_signer()
    rejected_vb = FROZEN_NOW + 2999
    buyer = _MockBuyer(
        signer_fn=signer,
        responses=[
            _resp(402, _challenge_402(validBefore=rejected_vb)),
            _resp(500, {"error": "authorization_expired"}),
            _resp(200, {"block_number": 999}),
        ],
    )
    out = buyer.fetch(URL)
    assert out["ok"] and out["paid"] is True
    assert len(buyer.requests) == 3
    assert len(signed_calls) == 2
    corrected_vb = int(_paid_authorization(buyer, 2)["validBefore"])
    assert corrected_vb == FROZEN_NOW + 3599
    assert corrected_vb > rejected_vb


def test_309_repair_failed_resign_leaves_inflight_journal_intact(monkeypatch):
    # Signer succeeds on call 1, raises on call 2 (the corrected re-sign).
    # The W-75 journal record of the already-transmitted authorization
    # must survive byte-identical: a later caller retry is forced to
    # REUSE it instead of minting a brand-new spendable authorization.
    _freeze_buyer_clock(monkeypatch)
    signer_calls = []

    def flaky_signer(typed_data):
        signer_calls.append(typed_data)
        if len(signer_calls) == 2:
            raise RuntimeError("signer unavailable")
        return _fake_signer(typed_data)

    buyer = _MockBuyer(
        signer_fn=flaky_signer,
        responses=[
            _resp(402, _challenge_402(validBefore=FROZEN_NOW + 600)),
            _resp(500, {"error": "authorization_expired"}),
            _resp(402, _challenge_402(validBefore=FROZEN_NOW + 600)),
            _resp(200, {"block_number": 7}),
        ],
    )
    with pytest.raises(RuntimeError):
        buyer.fetch(URL)
    transmitted = json.loads(buyer.requests[1]["headers"]["X-PAYMENT"])["payload"]
    entry = buyer._inflight.get(URL)
    assert entry is not None, "in-flight journal entry was destroyed by a failed re-sign"
    assert entry[1].signature == transmitted["signature"]
    assert entry[1].authorization == transmitted["authorization"]
    # Caller retry reuses the journaled authorization — no fresh mint.
    out = buyer.fetch(URL)
    assert out["ok"] and out["paid"] is True
    assert len(signer_calls) == 2  # the retry did NOT sign again
    retried = json.loads(buyer.requests[3]["headers"]["X-PAYMENT"])["payload"]
    assert retried["authorization"] == transmitted["authorization"]
    assert retried["signature"] == transmitted["signature"]


def test_309_repair_unknown_500_with_tx_hash_surfaces_hash():
    # Optional repair: a bare 500 carrying a well-formed broadcast hash
    # but no recognizable reason stays UNKNOWN/HOLD, but the hash is a
    # reconciliation lead and must survive onto the raised error instead
    # of being dropped.
    buyer = _MockBuyer(
        responses=[
            _resp(402, _challenge_402()),
            _resp(500, {"txHash": TX_HASH}),
        ],
    )
    with pytest.raises(UnknownFacilitatorFailure) as exc_info:
        buyer.fetch(URL)
    assert failure_action_for_error(exc_info.value) is FailureAction.HOLD
    assert TX_HASH in str(exc_info.value)
    assert len(buyer.requests) == 2
