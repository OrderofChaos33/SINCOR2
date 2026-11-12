"""(d) Buyer safety: retry storms create exactly ONE payment —
instruction-scoped idempotency + per-payee velocity caps.
"""
import pytest

from sincor_x402 import (AmbiguousPayment, IdempotencyConflict,
                         PaymentCapExceeded, VelocityCap, VelocityCapExceeded,
                         VerifiedBuyer)
from sincor_x402.types import PaymentRequirement, USDC_BASE_ADDRESS

from x402pkg_helpers import BUYER, endpoint_transport

URL = "http://inprocess/summarize"


def _buyer(endpoint, signer, **kw):
    counter = kw.pop("counter", {})
    buyer = VerifiedBuyer(BUYER, signer, max_payment_usd=1.00,
                          transport=endpoint_transport(endpoint, counter),
                          **kw)
    return buyer, counter


def test_retry_storm_after_success_pays_exactly_once(endpoint, signer):
    buyer, counter = _buyer(endpoint, signer)
    first = buyer.fetch(URL, instruction_id="storm-1", body={"q": "x"})
    assert first["paid"] is True
    for _ in range(39):  # the historical bug produced 40 payments here
        again = buyer.fetch(URL, instruction_id="storm-1", body={"q": "x"})
        assert again is first or again == first
    assert signer.calls == 1
    assert buyer.signatures_created == 1
    assert counter["paid_calls"] == 1


def test_retry_storm_after_ambiguous_outcome_never_repays(endpoint, signer):
    counter = {}
    real_transport = endpoint_transport(endpoint, counter)

    def ambiguous_transport(method, url, body, headers):
        status, hdrs, raw = real_transport(method, url, body, headers)
        if any(k.lower() == "x-payment" for k in headers):
            return 500, {}, b"gateway exploded"  # payment may have settled
        return status, hdrs, raw

    buyer = VerifiedBuyer(BUYER, signer, transport=ambiguous_transport)
    with pytest.raises(AmbiguousPayment):
        buyer.fetch(URL, instruction_id="storm-2", body={"q": "x"})
    for _ in range(39):
        with pytest.raises(AmbiguousPayment):
            buyer.fetch(URL, instruction_id="storm-2", body={"q": "x"})
    assert signer.calls == 1               # still exactly one signature
    assert counter["paid_calls"] == 1      # and exactly one paid HTTP attempt


def test_midflight_failure_resends_same_signed_payload(endpoint, signer):
    counter = {}
    real_transport = endpoint_transport(endpoint, counter)
    state = {"dropped": False}

    def flaky_transport(method, url, body, headers):
        if any(k.lower() == "x-payment" for k in headers) \
                and not state["dropped"]:
            state["dropped"] = True
            counter.setdefault("paid_headers", []).append(headers["X-PAYMENT"])
            counter["paid_calls"] = counter.get("paid_calls", 0) + 1
            raise ConnectionError("connection dropped after send")
        return real_transport(method, url, body, headers)

    buyer = VerifiedBuyer(BUYER, signer, transport=flaky_transport)
    with pytest.raises(ConnectionError):
        buyer.fetch(URL, instruction_id="storm-3", body={"q": "x"})
    out = buyer.fetch(URL, instruction_id="storm-3", body={"q": "x"})
    assert out["ok"] is True
    assert signer.calls == 1  # resent the SAME payload — no new payment
    headers_sent = counter["paid_headers"]
    assert len(headers_sent) == 2 and headers_sent[0] == headers_sent[1]


def test_instruction_reuse_with_different_request_conflicts(endpoint, signer):
    buyer, counter = _buyer(endpoint, signer)
    buyer.fetch(URL, instruction_id="reuse-1", body={"q": "a"})
    with pytest.raises(IdempotencyConflict):
        buyer.fetch(URL, instruction_id="reuse-1", body={"q": "DIFFERENT"})
    assert signer.calls == 1
    assert counter["paid_calls"] == 1


def test_velocity_cap_count_blocks_fourth_distinct_instruction(
        endpoint, signer):
    cap = VelocityCap(max_payments=3, max_amount_atomic=10**9,
                      window_seconds=60)
    buyer, counter = _buyer(endpoint, signer, velocity_cap=cap)
    for i in range(3):
        buyer.fetch(URL, instruction_id=f"vel-{i}", body={"q": str(i)})
    with pytest.raises(VelocityCapExceeded):
        buyer.fetch(URL, instruction_id="vel-3", body={"q": "3"})
    # The capped attempt never signed and never hit the paid route.
    assert signer.calls == 3
    assert counter["paid_calls"] == 3


def test_velocity_cap_amount_blocks_cumulative_overrun(endpoint, signer):
    cap = VelocityCap(max_payments=100, max_amount_atomic=15000,
                      window_seconds=60)  # each call is 10000 atomic
    buyer, _ = _buyer(endpoint, signer, velocity_cap=cap)
    buyer.fetch(URL, instruction_id="amt-0", body={"q": "0"})
    with pytest.raises(VelocityCapExceeded):
        buyer.fetch(URL, instruction_id="amt-1", body={"q": "1"})
    assert signer.calls == 1


def test_velocity_cap_is_per_payee():
    cap = VelocityCap(max_payments=1, max_amount_atomic=10**9)
    cap.check_and_record("0xPayeeA", 100)
    cap.check_and_record("0xPayeeB", 100)  # different payee: unaffected
    with pytest.raises(VelocityCapExceeded):
        cap.check_and_record("0xPayeeA", 100)


def test_payment_cap_refuses_before_signing(endpoint, signer):
    stingy = VerifiedBuyer(BUYER, signer, max_payment_usd=0.001,  # call is $0.01
                           transport=endpoint_transport(endpoint, {}))
    with pytest.raises(PaymentCapExceeded):
        stingy.fetch(URL, instruction_id="cap-1", body={"q": "x"})
    assert signer.calls == 0


def test_free_200_needs_no_payment(endpoint, signer):
    def free_transport(method, url, body, headers):
        return 200, {}, b'{"free": true}'
    buyer = VerifiedBuyer(BUYER, signer, transport=free_transport)
    out = buyer.fetch(URL, instruction_id="free-1", body=None)
    assert out == {"ok": True, "paid": False, "data": {"free": True}}
    assert signer.calls == 0


def test_unsupported_accepts_entry_rejected(signer):
    def weird_transport(method, url, body, headers):
        import json as _json
        body402 = {"x402Version": 1, "accepts": [{
            "scheme": "exact", "network": "solana",
            "maxAmountRequired": "1000", "asset": "SomeToken",
            "payTo": "xyz", "maxTimeoutSeconds": 60}]}
        return 402, {}, _json.dumps(body402).encode()
    buyer = VerifiedBuyer(BUYER, signer, transport=weird_transport)
    with pytest.raises(Exception):
        buyer.fetch(URL, instruction_id="unsup-1", body={})
    assert signer.calls == 0
