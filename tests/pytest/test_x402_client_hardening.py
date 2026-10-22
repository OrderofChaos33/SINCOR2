"""Dev-watch hedges: x402 client hardening.

Item 261 (x402 PR #3795, merged 2026-10-22): 402 responses now carry the
rejection reason in the BODY (``{"error": ...}``) instead of only in the
base64 PAYMENT-REQUIRED header. Before the patch, an empty ``{}`` body
made an unfunded-wallet refusal indistinguishable from a bad-signature
refusal. Hedge: integration tests asserting that our client surfaces
``body.error`` from a paid-leg 402 as a TYPED, distinguishable error
(and still refuses to retry blindly).

Item 263 (x402 #3866, OPEN): the official client pays ANY server-declared
amount — a x10 overcharge was captured 3/3 (client signs EIP-3009 for the
full inflated ``accepts[].amount``; no price-expectation check). Our
per-call ``max_payment_usd`` cap is NOT a price policy: a x10-inflated
quote can still fit under the cap. Hedge: fault-injection tests
replicating scenario S-BS-06 — an adversarial 402 with an inflated amount
— asserting OUR client/policy REFUSES before any signature is produced.

All network is mocked — no real paid calls.
"""

import json
import time

import pytest

from sincor2.x402_buyer import (
    AmbiguousPayment,
    InsufficientFundsError,
    InvalidSignatureError,
    PaymentCapExceeded,
    PaymentRefusedError,
    PricePolicyViolation,
    SettlementFailedError,
    X402Buyer,
    X402BuyerError,
    check_price_expectation,
    BASE_CHAIN_REF,
    USDC_BASE_ADDRESS,
)

WALLET = "0x" + "11" * 20
PAY_TO = "0x" + "22" * 20


def _fake_signer(typed_data):
    """Deterministic fake signer — never a real key."""
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

    def __init__(self, *a, responses=None, **kw):
        kw.setdefault("wallet_address", WALLET)
        kw.setdefault("signer_fn", _fake_signer)
        super().__init__(*a, **kw)
        self._responses = list(responses or [])
        self.requests = []

    def _do_request(self, url, method, body, headers):
        self.requests.append({"url": url, "method": method, "headers": headers})
        status, resp_headers, raw = self._responses.pop(0)
        return status, resp_headers, raw


def _resp(status, body_dict=None, headers=None):
    raw = json.dumps(body_dict or {}).encode()
    return (status, headers or {}, raw)


# ---------------------------------------------------------------------------
# #261: 402 rejection reason now travels in the BODY (x402 PR #3795)
# ---------------------------------------------------------------------------


def _refusal_flow(error_body):
    """Paid-leg 402 carrying a post-#3795 body; returns (buyer, error_body)."""
    buyer = _MockBuyer(
        responses=[
            _resp(402, _challenge_402()),  # unpaid leg: fair challenge
            _resp(402, error_body),  # paid leg: server refuses, body says why
        ],
    )
    return buyer


def test_261_insufficient_funds_reason_surfaced():
    # (a) verify failure with an unfunded key: the server settles/verifies,
    # finds no funds, and answers 402 with {"error": "insufficient_funds"}.
    buyer = _refusal_flow({"x402Version": 1, "error": "insufficient_funds"})
    with pytest.raises(InsufficientFundsError) as exc_info:
        buyer.fetch("https://api.mach.gallery/api/base/block-number")
    assert exc_info.value.reason == "insufficient_funds"
    # fail-closed contract preserved: still an AmbiguousPayment — the signed
    # payment MAY have settled, so no blind retry.
    assert isinstance(exc_info.value, AmbiguousPayment)
    assert len(buyer.requests) == 2  # exactly one retry, no loop


def test_261_invalid_signature_distinguishable_from_unfunded():
    # The pre-#3795 failure mode: unfunded-wallet and bad-signature were
    # indistinguishable (both arrived as an opaque 402). Now they must not be.
    buyer = _refusal_flow({"error": "invalid_signature"})
    with pytest.raises(InvalidSignatureError) as exc_info:
        buyer.fetch("https://api.mach.gallery/api/base/block-number")
    assert exc_info.value.reason == "invalid_signature"
    assert not isinstance(exc_info.value, InsufficientFundsError)
    assert isinstance(exc_info.value, AmbiguousPayment)


def test_261_settlement_catchall_path():
    # (b) the settlement catch-all path: @x402/express answers
    # {"error": "settlement_failed"} on settlement failure.
    buyer = _refusal_flow({"error": "settlement_failed"})
    with pytest.raises(SettlementFailedError) as exc_info:
        buyer.fetch("https://api.mach.gallery/api/base/block-number")
    assert exc_info.value.reason == "settlement_failed"
    assert isinstance(exc_info.value, AmbiguousPayment)


def test_261_reason_read_from_body_not_header():
    # #3795's whole point: the BODY is now the guaranteed channel. A server
    # speaking the new format carries the reason in the body even when the
    # PAYMENT-REQUIRED header is absent/legacy.
    buyer = _refusal_flow({"error": "insufficient_funds"})
    with pytest.raises(InsufficientFundsError) as exc_info:
        buyer.fetch("https://api.mach.gallery/api/base/block-number")
    assert exc_info.value.reason == "insufficient_funds"


def test_261_unknown_reason_stays_typed_and_fail_closed():
    # A reason we have no mapping for is still exposed (not swallowed) and
    # still refuses blind retry.
    buyer = _refusal_flow({"error": "facilitator_down"})
    with pytest.raises(PaymentRefusedError) as exc_info:
        buyer.fetch("https://api.mach.gallery/api/base/block-number")
    assert exc_info.value.reason == "facilitator_down"
    assert isinstance(exc_info.value, AmbiguousPayment)
    assert not isinstance(exc_info.value, InsufficientFundsError)


def test_261_empty_body_keeps_old_ambiguous_behavior():
    # Pre-#3795 servers still answer {} — that path must not regress: plain
    # AmbiguousPayment, no invented reason.
    buyer = _refusal_flow({})
    with pytest.raises(AmbiguousPayment, match="refusing to retry") as exc_info:
        buyer.fetch("https://api.mach.gallery/api/base/block-number")
    assert not isinstance(exc_info.value, PaymentRefusedError)


# ---------------------------------------------------------------------------
# #263: price-expectation policy — S-BS-06 overcharge fault injection
# ---------------------------------------------------------------------------


def _counting_signer():
    calls = []

    def signer(typed_data):
        calls.append(typed_data)
        return "0x" + "ab" * 65

    signer.calls = calls
    return signer


def test_263_inflated_402_refused_before_any_signature():
    # S-BS-06 replication: expected price $0.01; adversarial 402 declares
    # $0.10 (x10). The $0.10 demand FITS the default per-call cap, so the
    # cap alone would have signed it — the price policy must refuse first.
    signer = _counting_signer()
    buyer = _MockBuyer(
        signer_fn=signer,
        expected_price_usd=0.01,
        responses=[_resp(402, _challenge_402(amount_atomic=100_000))],  # $0.10
    )
    with pytest.raises(PricePolicyViolation, match="no signature produced"):
        buyer.fetch("https://evil.example.invalid/api/data")
    assert signer.calls == []  # signer NEVER invoked
    assert len(buyer.requests) == 1  # no paid retry attempted


def test_263_inflated_402_refused_even_under_cap():
    # Explicit: a quote 10x over expectation but under max_payment_usd is
    # refused by the POLICY, not the cap (proves the two gates differ).
    signer = _counting_signer()
    buyer = _MockBuyer(
        signer_fn=signer,
        max_payment_usd=1.00,
        expected_price_usd=0.01,
        responses=[_resp(402, _challenge_402(amount_atomic=100_000))],  # $0.10
    )
    with pytest.raises(PricePolicyViolation):
        buyer.fetch("https://evil.example.invalid/api/data")
    assert signer.calls == []


def test_263_fair_price_within_tolerance_signs_normally():
    signer = _counting_signer()
    buyer = _MockBuyer(
        signer_fn=signer,
        expected_price_usd=0.01,  # 5% tolerance -> up to $0.0105 accepted
        responses=[
            _resp(402, _challenge_402(amount_atomic=10_400)),  # $0.0104
            _resp(200, {"data": "ok"}),
        ],
    )
    out = buyer.fetch("https://api.mach.gallery/api/base/block-number")
    assert out["paid"] is True
    assert len(signer.calls) == 1


def test_263_sign_authorization_enforces_policy_directly():
    # Defense in depth: the policy is enforced at the signing gate itself,
    # not only in fetch().
    buyer = _MockBuyer(
        signer_fn=_counting_signer(), expected_price_usd=0.01
    )
    req = X402Buyer.parse_402(_challenge_402(amount_atomic=100_000))
    with pytest.raises(PricePolicyViolation, match="no signature produced"):
        buyer.sign_authorization(req)


def test_263_no_policy_configured_means_cap_only():
    # The policy is opt-in per call site; without it, the absolute cap is
    # the only gate (documents why integrators must ALWAYS wire the hook —
    # see the module docstring design constraint).
    buyer = _MockBuyer(
        max_payment_usd=1.00,
        responses=[
            _resp(402, _challenge_402(amount_atomic=100_000)),
            _resp(200, {"data": "ok"}),
        ],
    )
    out = buyer.fetch("https://api.mach.gallery/api/base/block-number")
    assert out["paid"] is True


def test_263_pure_function_boundaries():
    req = X402Buyer.parse_402(_challenge_402(amount_atomic=10_000))  # $0.01
    check_price_expectation(0.01, req)  # exact: accepted
    check_price_expectation(0.01, req, tolerance_bps=0)  # exact at 0 tol: ok

    over = X402Buyer.parse_402(_challenge_402(amount_atomic=10_500))  # $0.0105
    check_price_expectation(0.01, over)  # exactly at 5% tolerance: accepted
    with pytest.raises(PricePolicyViolation):
        check_price_expectation(0.01, over, tolerance_bps=499)  # 4.99%: refused

    under = X402Buyer.parse_402(_challenge_402(amount_atomic=5_000))  # $0.005
    check_price_expectation(0.01, under)  # under expectation: accepted

    way_over = X402Buyer.parse_402(_challenge_402(amount_atomic=100_000))
    with pytest.raises(PricePolicyViolation, match="no signature produced"):
        check_price_expectation(0.01, way_over)


def test_263_pure_function_rejects_bad_config():
    req = X402Buyer.parse_402(_challenge_402())
    with pytest.raises(ValueError, match="expected_price_usd"):
        check_price_expectation(0.0, req)
    with pytest.raises(ValueError, match="expected_price_usd"):
        check_price_expectation(-1.0, req)
    with pytest.raises(ValueError, match="tolerance_bps"):
        check_price_expectation(0.01, req, tolerance_bps=-1)


def test_263_constructor_rejects_bad_config():
    with pytest.raises(ValueError, match="expected_price_usd"):
        _MockBuyer(expected_price_usd=0.0, responses=[])
    with pytest.raises(ValueError, match="tolerance_bps"):
        _MockBuyer(expected_price_usd=0.01, price_tolerance_bps=-5, responses=[])


def test_263_violation_is_fail_closed_buyer_error():
    assert issubclass(PricePolicyViolation, X402BuyerError)
    assert not issubclass(PricePolicyViolation, PaymentCapExceeded)
