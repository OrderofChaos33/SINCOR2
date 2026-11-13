"""W-146 regression tests: one payment authorization funds exactly one
escrow — the payment_ref is consumed server-side.

Red-team hunt 46 CONFIRMED the replay on the unfixed package: the same
X-PAYMENT header presented twice opened two escrows and released 2000
from one 1000-unit authorization; the ref was derived from builtin
hash() (process-randomized) and never stored on the escrow record.
These tests pin the fixed behavior.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sincor_x402 import DuplicatePaymentRef
from sincor_x402.facilitator import LocalFacilitator

from x402pkg_helpers import AMOUNT_ATOMIC, make_payment_payload, payment_header

SRC = Path(__file__).resolve().parent.parent / "src"


def _payload_with_nonce(requirement, nonce_hex):
    payload = make_payment_payload(requirement)
    payload["payload"]["authorization"]["nonce"] = "0x" + nonce_hex
    return payload


def test_same_payment_twice_second_rejected_exactly_one_escrow(
        endpoint, platform, requirement):
    header = payment_header(make_payment_payload(requirement))
    r1 = endpoint.handle(headers={"X-PAYMENT": header}, kwargs={"q": "hi"})
    assert r1.status == 200
    r2 = endpoint.handle(headers={"X-PAYMENT": header}, kwargs={"q": "hi"})
    assert r2.status == 409
    assert r2.body["ok"] is False
    assert r2.body["error"] == "payment_ref_consumed"
    # Exactly one escrow exists and exactly one payment's worth released.
    assert len(platform._escrows) == 1
    assert platform.released_total == int(AMOUNT_ATOMIC)
    assert platform.refunded_total == 0


def test_payment_ref_stored_on_escrow_record(endpoint, platform, requirement):
    header = payment_header(make_payment_payload(requirement))
    resp = endpoint.handle(headers={"X-PAYMENT": header}, kwargs={"q": "hi"})
    assert resp.status == 200
    escrow = platform.get_escrow(resp.body["settlement"]["escrow_id"])
    assert escrow.payment_ref
    assert escrow.payment_ref.startswith("localref_")


def test_platform_rejects_reused_payment_ref_directly(platform, requirement):
    eid = platform.open_escrow(requirement=requirement, payer="0xabc",
                               payment_ref="ref-once")
    assert platform.get_escrow(eid).payment_ref == "ref-once"
    with pytest.raises(DuplicatePaymentRef):
        platform.open_escrow(requirement=requirement, payer="0xabc",
                             payment_ref="ref-once")
    assert len(platform._escrows) == 1


def test_consumed_ref_stays_consumed_after_release_and_refund(
        platform, requirement):
    eid = platform.open_escrow(requirement=requirement, payer="0xabc",
                               payment_ref="ref-release")
    assert platform.release(eid)["ok"] is True
    with pytest.raises(DuplicatePaymentRef):
        platform.open_escrow(requirement=requirement, payer="0xabc",
                             payment_ref="ref-release")

    eid2 = platform.open_escrow(requirement=requirement, payer="0xabc",
                                payment_ref="ref-refund")
    assert platform.refund(eid2)["ok"] is True
    with pytest.raises(DuplicatePaymentRef):
        platform.open_escrow(requirement=requirement, payer="0xabc",
                             payment_ref="ref-refund")


def test_distinct_payments_open_distinct_escrows(
        endpoint, platform, requirement):
    h1 = payment_header(_payload_with_nonce(requirement, "11" * 32))
    h2 = payment_header(_payload_with_nonce(requirement, "22" * 32))
    r1 = endpoint.handle(headers={"X-PAYMENT": h1}, kwargs={"q": "a"})
    r2 = endpoint.handle(headers={"X-PAYMENT": h2}, kwargs={"q": "b"})
    assert r1.status == 200 and r2.status == 200
    assert len(platform._escrows) == 2
    assert platform.released_total == 2 * int(AMOUNT_ATOMIC)


def test_payment_ref_deterministic_and_authorization_sensitive(requirement):
    fac = LocalFacilitator()
    p1 = make_payment_payload(requirement)
    ref1 = fac.verify_and_hold(payment_payload=p1,
                               requirement=requirement).payment_ref
    ref2 = fac.verify_and_hold(payment_payload=p1,
                               requirement=requirement).payment_ref
    assert ref1 == ref2 and ref1.startswith("localref_")
    other = _payload_with_nonce(requirement, "33" * 32)
    ref3 = fac.verify_and_hold(payment_payload=other,
                               requirement=requirement).payment_ref
    assert ref3 != ref1


def test_payment_ref_stable_across_processes(requirement):
    payload = make_payment_payload(requirement)
    expr = (
        "import json, sys;"
        "sys.path.insert(0, {src!r});"
        "from sincor_x402.facilitator import LocalFacilitator;"
        "from sincor_x402.types import PaymentRequirement;"
        "req = PaymentRequirement(pay_to={pay_to!r},"
        " max_amount_required={amount!r});"
        "print(LocalFacilitator().verify_and_hold("
        "payment_payload=json.loads({payload!r}), requirement=req)"
        ".payment_ref)"
    ).format(src=str(SRC), pay_to=requirement.pay_to,
             amount=requirement.max_amount_required,
             payload=json.dumps(payload))
    refs = set()
    for _ in range(3):
        out = subprocess.run(
            [sys.executable, "-c", expr], capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": str(SRC)})
        assert out.returncode == 0, out.stderr
        refs.add(out.stdout.strip())
    assert len(refs) == 1  # builtin hash() gave three different refs
    assert refs.pop().startswith("localref_")
