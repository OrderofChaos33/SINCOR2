"""Regression tests for red-team Hunt 15: W-75.

W-75 — W-64 residual: retry after AmbiguousPayment re-signed a FRESH
authorization (fresh nonce) and double-paid — the server settled two
authorizations with two distinct nonces and two signer invocations.

Fix: the buyer keeps a per-URL payment-attempt journal.  A retry of the
SAME logical payment reuses the in-flight signed authorization bytes
instead of minting a fresh nonce.  The onchain nonce can only be spent
once, so reuse can never double-pay.

Self-contained: all transport mocked, deterministic fake signer.
Run: PYTHONPATH=src:. <venv>/bin/python -m pytest tests/pytest/test_hunt15_w75.py -q
"""

from __future__ import annotations

import json
import time

import pytest

from sincor2.x402_buyer import (
    AmbiguousPayment,
    PaymentTransportError,
    SignedPayment,
    X402Buyer,
    BASE_CHAIN_REF,
    USDC_BASE_ADDRESS,
)


WALLET = "0x" + "11" * 20
PAY_TO = "0x" + "22" * 20
URL = "https://service.example/api/data"


class _CountingSigner:
    def __init__(self):
        self.calls = 0

    def __call__(self, typed_data):
        self.calls += 1
        assert typed_data["primaryType"] == "TransferWithAuthorization"
        # Deterministic per-nonce signature so reuse is observable.
        nonce = typed_data["message"]["nonce"]
        return "0x" + nonce[2:4] * 65


def _challenge_402(amount_atomic=5000, with_nonce=True, **overrides):
    now = int(time.time())
    body = {
        "x402Version": 1,
        "scheme": "exact",
        "network": BASE_CHAIN_REF,
        "maxAmountRequired": amount_atomic,
        "asset": USDC_BASE_ADDRESS,
        "payTo": PAY_TO,
        "validAfter": 0,
        "validBefore": now + 600,
        "resource": "/api/data",
    }
    if with_nonce:
        body["nonce"] = "0x" + "cc" * 32
    body.update(overrides)
    return body


def _resp(status, body_dict=None):
    return (status, {}, json.dumps(body_dict or {}).encode())


class _ScriptedBuyer(X402Buyer):
    """Mock transport: script of responses or exceptions to raise."""

    def __init__(self, *a, script=None, signer=None, **kw):
        kw.setdefault("wallet_address", WALLET)
        kw.setdefault("signer_fn", signer or _CountingSigner())
        super().__init__(*a, **kw)
        self._script = list(script or [])
        self.requests = []

    def _do_request(self, url, method, body, headers):
        self.requests.append({"url": url, "headers": dict(headers)})
        action = self._script.pop(0)
        if isinstance(action, BaseException):
            raise action
        return action

    def paid_header(self, i):
        return self.requests[i]["headers"]["X-PAYMENT"]


def _make(signer, script):
    return _ScriptedBuyer(script=script, signer=signer)


class TestW75RetryJournal:
    def test_retry_after_ambiguous_reuses_authorization(self):
        # THE discriminating test: without the journal, attempt 2 mints a
        # fresh nonce (signer called twice, two spendable authorizations).
        signer = _CountingSigner()
        buyer = _make(signer, script=[
            _resp(402, _challenge_402(with_nonce=False)),  # attempt 1 unpaid
            RuntimeError("response dropped after settle"),  # attempt 1 paid
        ])
        with pytest.raises(AmbiguousPayment):
            buyer.fetch(URL)
        assert signer.calls == 1

        buyer._script.extend([
            _resp(402, _challenge_402(with_nonce=False)),  # attempt 2 unpaid
            _resp(200, {"data": "ok"}),                    # attempt 2 paid
        ])
        out = buyer.fetch(URL)
        assert out["ok"] is True and out["paid"] is True
        # No fresh signature was minted for the retry...
        assert signer.calls == 1
        # ...and the exact same signed bytes went out on both paid legs:
        # one nonce, one authorization — the server cannot settle twice.
        assert buyer.paid_header(3) == buyer.paid_header(1)

    def test_success_clears_journal(self):
        signer = _CountingSigner()
        buyer = _make(signer, script=[
            _resp(402, _challenge_402(with_nonce=False)),
            _resp(200, {"data": "one"}),
            _resp(402, _challenge_402(with_nonce=False)),
            _resp(200, {"data": "two"}),
        ])
        assert buyer.fetch(URL)["data"] == {"data": "one"}
        assert buyer.fetch(URL)["data"] == {"data": "two"}
        # Two genuinely separate logical payments -> two signatures.
        assert signer.calls == 2
        assert buyer.paid_header(3) != buyer.paid_header(1)

    def test_changed_challenge_signs_fresh(self):
        signer = _CountingSigner()
        buyer = _make(signer, script=[
            _resp(402, _challenge_402(amount_atomic=5000, with_nonce=False)),
            RuntimeError("dropped"),
        ])
        with pytest.raises(AmbiguousPayment):
            buyer.fetch(URL)
        buyer._script.extend([
            _resp(402, _challenge_402(amount_atomic=9000, with_nonce=False)),
            _resp(200, {"data": "ok"}),
        ])
        buyer.fetch(URL)
        # Different amount = different logical payment = fresh signature.
        assert signer.calls == 2
        assert buyer.paid_header(3) != buyer.paid_header(1)

    def test_expired_inflight_signs_fresh(self):
        signer = _CountingSigner()
        buyer = _make(signer, script=[
            _resp(402, _challenge_402(with_nonce=False)),
            _resp(200, {"data": "ok"}),
        ])
        past = int(time.time()) - 60
        expired = SignedPayment(
            signature="0x" + "ee" * 65,
            authorization={
                "from": WALLET, "to": PAY_TO, "value": 5000,
                "validAfter": 0, "validBefore": past,
                "nonce": "0x" + "ee" * 32,
            },
        )
        req = X402Buyer.parse_402(_challenge_402(with_nonce=False))
        buyer._record_inflight(URL, req, expired)
        buyer.fetch(URL)
        assert signer.calls == 1
        assert "ee" * 32 not in buyer.paid_header(1)

    def test_transport_error_retry_reuses(self):
        # Provably-pre-send failure: retry is safe AND reuses the bytes.
        signer = _CountingSigner()
        buyer = _make(signer, script=[
            _resp(402, _challenge_402(with_nonce=False)),
            PaymentTransportError("connection refused pre-send"),
        ])
        with pytest.raises(PaymentTransportError):
            buyer.fetch(URL)
        buyer._script.extend([
            _resp(402, _challenge_402(with_nonce=False)),
            _resp(200, {"data": "ok"}),
        ])
        buyer.fetch(URL)
        assert signer.calls == 1
        assert buyer.paid_header(3) == buyer.paid_header(1)

    def test_server_nonce_preferred_and_journaled(self):
        # Server-provided nonces are still preferred; the journal keeps the
        # exact server-issued authorization for the retry.
        signer = _CountingSigner()
        buyer = _make(signer, script=[
            _resp(402, _challenge_402(with_nonce=True)),
            RuntimeError("dropped"),
        ])
        with pytest.raises(AmbiguousPayment):
            buyer.fetch(URL)
        buyer._script.extend([
            _resp(402, _challenge_402(with_nonce=True)),
            _resp(200, {"data": "ok"}),
        ])
        buyer.fetch(URL)
        assert signer.calls == 1
        header = json.loads(buyer.paid_header(3))
        assert header["payload"]["authorization"]["nonce"] == "0x" + "cc" * 32
