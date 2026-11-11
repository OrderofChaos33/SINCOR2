"""Full-loop proof (mocked platform + mocked facilitator):
(a) good result → escrow releases
(b) garbage result → verification fails → refund
(c) timeout → refund
plus the buyer→server loop wired end to end in-process.
"""
import time

from sincor_x402 import VerifiedBuyer, VerifiedEndpoint

from x402pkg_helpers import (AMOUNT_ATOMIC, endpoint_transport, make_payment_payload,
                      payment_header)


def test_unpaid_request_gets_402_with_own_accepts_and_escrow(endpoint):
    resp = endpoint.handle(headers={}, kwargs={"q": "hi"})
    assert resp.status == 402
    assert resp.body["x402Version"] == 1
    accepts = resp.body["accepts"]
    assert len(accepts) == 1
    assert accepts[0]["maxAmountRequired"] == AMOUNT_ATOMIC
    assert accepts[0]["payTo"].lower().startswith("0x09e289")
    assert resp.body["escrow"]["specHash"] == endpoint.terms.spec_hash


def test_good_result_releases_escrow(endpoint, platform, requirement):
    header = payment_header(make_payment_payload(requirement))
    resp = endpoint.handle(headers={"X-PAYMENT": header}, kwargs={"q": "hi"})
    assert resp.status == 200
    assert resp.body["result"]["answer"] == 42
    assert resp.body["verification"]["passed"] is True
    settlement = resp.body["settlement"]
    assert settlement["status"] == "released"
    escrow = platform.get_escrow(settlement["escrow_id"])
    assert escrow.status == "released"
    assert platform.released_total == int(AMOUNT_ATOMIC)
    assert platform.refunded_total == 0


def test_garbage_result_fails_verification_and_refunds(
        requirement, platform, facilitator, spec):
    def garbage(**kwargs):
        return {"hallucinated": "nonsense"}  # no "answer" key
    ep = VerifiedEndpoint(garbage, requirement=requirement, spec=spec,
                          platform=platform, facilitator=facilitator)
    header = payment_header(make_payment_payload(requirement))
    resp = ep.handle(headers={"X-PAYMENT": header}, kwargs={})
    assert resp.status == 422
    assert resp.body["error"] == "verification_failed"
    assert resp.body["settlement"]["status"] == "refunded"
    assert platform.refunded_total == int(AMOUNT_ATOMIC)
    assert platform.released_total == 0


def test_timeout_refunds(requirement, platform, facilitator, spec):
    def slow(**kwargs):
        time.sleep(2.0)
        return {"answer": 42}
    ep = VerifiedEndpoint(slow, requirement=requirement, spec=spec,
                          platform=platform, facilitator=facilitator,
                          timeout_seconds=0.2)
    header = payment_header(make_payment_payload(requirement))
    resp = ep.handle(headers={"X-PAYMENT": header}, kwargs={})
    assert resp.status == 504
    assert resp.body["error"] == "execution_timeout"
    assert resp.body["settlement"]["status"] == "refunded"
    assert platform.refunded_total == int(AMOUNT_ATOMIC)
    assert platform.released_total == 0


def test_work_exception_refunds(requirement, platform, facilitator, spec):
    def boom(**kwargs):
        raise RuntimeError("tool exploded")
    ep = VerifiedEndpoint(boom, requirement=requirement, spec=spec,
                          platform=platform, facilitator=facilitator)
    header = payment_header(make_payment_payload(requirement))
    resp = ep.handle(headers={"X-PAYMENT": header}, kwargs={})
    assert resp.status == 502
    assert resp.body["settlement"]["status"] == "refunded"
    assert platform.released_total == 0


def test_escrow_settles_exactly_once(platform, requirement):
    eid = platform.open_escrow(requirement=requirement,
                               payer="0xabc", payment_ref="ref1")
    assert platform.release(eid)["ok"] is True
    second = platform.release(eid)
    assert second["ok"] is False  # double-settle impossible
    assert platform.refund(eid)["ok"] is False
    assert platform.released_total == int(AMOUNT_ATOMIC)


def test_buyer_to_server_full_loop(endpoint, platform, signer):
    counter = {}
    buyer = VerifiedBuyer("0x1111111111111111111111111111111111111111",
                          signer, max_payment_usd=1.00,
                          transport=endpoint_transport(endpoint, counter))
    out = buyer.fetch("http://inprocess/summarize",
                      instruction_id="instr-loop-1",
                      body={"q": "hello"})
    assert out["ok"] is True and out["paid"] is True
    assert out["data"]["result"]["answer"] == 42
    assert signer.calls == 1
    assert counter["paid_calls"] == 1
    assert platform.released_total == int(AMOUNT_ATOMIC)
    assert platform.refunded_total == 0
