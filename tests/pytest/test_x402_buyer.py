"""Tests for x402 buyer client. All network is mocked — no real paid calls."""

import json
import time

import pytest

from sincor2.x402_buyer import (
    AmbiguousPayment,
    MalformedChallenge,
    PaymentCapExceeded,
    SignedPayment,
    StaleChallenge,
    UnsupportedPayment,
    X402Buyer,
    X402BuyerError,
    BASE_CHAIN_REF,
    USDC_BASE_ADDRESS,
)

WALLET = "0x" + "11" * 20
PAY_TO = "0x" + "22" * 20


def _fake_signer(typed_data):
    """Deterministic fake signer — never a real key."""
    assert typed_data["domain"]["verifyingContract"] == USDC_BASE_ADDRESS
    assert typed_data["primaryType"] == "TransferWithAuthorization"
    return "0x" + "ab" * 65


def _make_buyer(**kw):
    kw.setdefault("wallet_address", WALLET)
    kw.setdefault("signer_fn", _fake_signer)
    return X402Buyer(**kw)


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
# 402 parsing
# ---------------------------------------------------------------------------


def test_parse_valid_402():
    req = X402Buyer.parse_402(_challenge_402())
    assert req.amount_atomic == 5000
    assert req.amount_usd == pytest.approx(0.005)
    assert req.network == BASE_CHAIN_REF
    assert req.pay_to == PAY_TO


def test_parse_nested_payment_format():
    # our seller nests under "payment"
    req = X402Buyer.parse_402({"payment": _challenge_402()})
    assert req.amount_atomic == 5000


def test_parse_accepts_list_format():
    req = X402Buyer.parse_402({"accepts": [_challenge_402()]})
    assert req.amount_atomic == 5000


def test_parse_missing_fields():
    with pytest.raises(MalformedChallenge, match="missing fields"):
        X402Buyer.parse_402({})


def test_parse_wrong_network_rejected():
    with pytest.raises(UnsupportedPayment, match="unsupported network"):
        X402Buyer.parse_402(_challenge_402(network="eip155:1"))


def test_parse_wrong_asset_rejected():
    with pytest.raises(UnsupportedPayment, match="unsupported asset"):
        X402Buyer.parse_402(_challenge_402(asset="0x" + "99" * 20))


def test_parse_numeric_chain_id_normalized():
    req = X402Buyer.parse_402(_challenge_402(network=8453))
    assert req.network == BASE_CHAIN_REF


# ---------------------------------------------------------------------------
# signing + safety gates
# ---------------------------------------------------------------------------


def test_sign_produces_eip712_payment():
    buyer = _make_buyer()
    req = X402Buyer.parse_402(_challenge_402())
    signed = buyer.sign_authorization(req)
    assert isinstance(signed, SignedPayment)
    assert signed.signature.startswith("0x")
    assert signed.authorization["to"] == PAY_TO
    assert signed.authorization["value"] == 5000
    assert signed.authorization["from"] == WALLET


def test_cap_enforcement():
    buyer = _make_buyer(max_payment_usd=0.01)
    # $0.10 demand > $0.01 cap
    req = X402Buyer.parse_402(_challenge_402(amount_atomic=100_000))
    with pytest.raises(PaymentCapExceeded, match="no signature produced"):
        buyer.sign_authorization(req)


def test_stale_valid_before_rejected():
    buyer = _make_buyer()
    past = int(time.time()) - 60
    req = X402Buyer.parse_402(_challenge_402(validBefore=past))
    with pytest.raises(StaleChallenge, match="in the past"):
        buyer.sign_authorization(req)


def test_absurd_valid_before_rejected():
    buyer = _make_buyer()
    far = int(time.time()) + 7200
    req = X402Buyer.parse_402(_challenge_402(validBefore=far))
    with pytest.raises(StaleChallenge, match="more than 1h"):
        buyer.sign_authorization(req)


def test_zero_amount_rejected():
    buyer = _make_buyer()
    req = X402Buyer.parse_402(_challenge_402(amount_atomic=0))
    with pytest.raises(MalformedChallenge, match="non-positive"):
        buyer.sign_authorization(req)


def test_bad_wallet_rejected():
    with pytest.raises(ValueError, match="0x address"):
        X402Buyer("not-an-address", _fake_signer)


def test_signer_must_return_hex():
    buyer = _make_buyer(signer_fn=lambda td: "nope")
    req = X402Buyer.parse_402(_challenge_402())
    with pytest.raises(X402BuyerError, match="0x signature"):
        buyer.sign_authorization(req)


# ---------------------------------------------------------------------------
# fetch flow (mocked HTTP)
# ---------------------------------------------------------------------------


def test_fetch_free_200_no_payment():
    buyer = _MockBuyer(
        responses=[_resp(200, {"block": 123})],
    )
    out = buyer.fetch("https://example.invalid/free")
    assert out["ok"] and out["paid"] is False
    assert out["data"] == {"block": 123}


def test_fetch_402_sign_retry_200():
    buyer = _MockBuyer(
        responses=[
            _resp(402, _challenge_402()),
            _resp(200, {"block_number": 999}),
        ],
    )
    out = buyer.fetch("https://api.mach.gallery/api/base/block-number")
    assert out["ok"] and out["paid"] is True
    assert out["amount_usd"] == pytest.approx(0.005)
    assert out["data"] == {"block_number": 999}
    # second request carried the payment header
    assert "X-PAYMENT" in buyer.requests[1]["headers"]
    hdr = json.loads(buyer.requests[1]["headers"]["X-PAYMENT"])
    assert hdr["payload"]["signature"].startswith("0x")


def test_fetch_402_after_signed_retry_raises_no_loop():
    buyer = _MockBuyer(
        responses=[
            _resp(402, _challenge_402()),
            _resp(402, _challenge_402()),  # server still wants payment
        ],
    )
    with pytest.raises(AmbiguousPayment, match="refusing to retry"):
        buyer.fetch("https://api.mach.gallery/api/base/block-number")
    assert len(buyer.requests) == 2  # exactly one retry, no loop


def test_fetch_unexpected_retry_status_raises():
    buyer = _MockBuyer(
        responses=[
            _resp(402, _challenge_402()),
            _resp(500, {}),
        ],
    )
    with pytest.raises(AmbiguousPayment, match="ambiguous retry status 500"):
        buyer.fetch("https://api.mach.gallery/api/base/block-number")


def test_fetch_non_402_initial_status_raises():
    buyer = _MockBuyer(responses=[_resp(500, {})])
    with pytest.raises(X402BuyerError, match="unexpected status 500"):
        buyer.fetch("https://example.invalid/x")


def test_fetch_over_cap_never_signs():
    signed_calls = []

    def counting_signer(td):
        signed_calls.append(td)
        return "0x" + "ab" * 65

    buyer = _MockBuyer(
        wallet_address=WALLET,
        signer_fn=counting_signer,
        max_payment_usd=0.001,
        responses=[_resp(402, _challenge_402(amount_atomic=5000))],  # $0.005
    )
    with pytest.raises(PaymentCapExceeded):
        buyer.fetch("https://api.mach.gallery/api/base/block-number")
    assert signed_calls == []  # signer never invoked
    assert len(buyer.requests) == 1  # no retry attempted


def test_fetch_402_in_header_fallback():
    hdr = json.dumps(_challenge_402())
    buyer = _MockBuyer(
        responses=[
            (402, {"PAYMENT-REQUIRED": hdr}, b"not-json{{{"),
            _resp(200, {"ok": True}),
        ],
    )
    out = buyer.fetch("https://api.mach.gallery/api/base/block-number")
    assert out["paid"] is True


# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------


def test_preflight_ok():
    buyer = _MockBuyer(responses=[_resp(200, {"service": "up"})])
    out = buyer.preflight("https://api.mach.gallery/api/preflight")
    assert out["ok"] is True


def test_preflight_failure():
    buyer = _MockBuyer(responses=[_resp(503, {})])
    with pytest.raises(X402BuyerError, match="preflight failed"):
        buyer.preflight("https://api.mach.gallery/api/preflight")
