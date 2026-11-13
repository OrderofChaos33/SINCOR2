"""W-146 mutation regression tests (Builder B adversarial review).

``_stable_payment_ref`` originally hashed the raw authorization dict:
``canonical_json`` sorts keys but does no semantic normalization, while
``LocalFacilitator`` itself treats many re-presentations as the same
payment. Each variant below — same ECDSA signature, same EIP-3009 nonce —
produced a *different* ref and opened a second escrow (200 released
twice from one authorization). These tests pin the fix: the ref is
derived from a normalized authorization (exactly the six EIP-3009
fields, canonical typed values), non-canonical forms fail closed, and
an empty payment_ref can never fund an escrow.
"""
import copy
import json

import pytest

from sincor_x402 import (LocalReferencePlatform, MissingPaymentRef,
                         PaymentRequirement, VerifiedEndpoint)
from sincor_x402.facilitator import (FacilitatorResult, LocalFacilitator,
                                     _stable_payment_ref)

from x402pkg_helpers import payment_header

# Letter-containing addresses so case mutations are actually meaningful.
PAYER = "0xAaBbCcDdEeFf1111111111111111111111111111"
PAY_TO = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
AMOUNT = 1000
NONCE = "0x" + "aa" * 32
SIG = "0x" + "ab" * 65


def _requirement():
    return PaymentRequirement(pay_to=PAY_TO, max_amount_required=str(AMOUNT))


def _base_payload():
    req = _requirement()
    return {
        "x402Version": 1,
        "scheme": "exact",
        "network": "base",
        "accepted": req.to_accepts_dict(),
        "payload": {
            "signature": SIG,
            "authorization": {
                "from": PAYER,
                "to": PAY_TO,
                "value": AMOUNT,
                "validAfter": 0,
                "validBefore": 9999999999,
                "nonce": NONCE,
            },
        },
    }


def _endpoint():
    return VerifiedEndpoint(
        lambda **kw: {"answer": 42}, requirement=_requirement(),
        spec={"required_keys": ["answer"]})


def _auth(payload):
    return payload["payload"]["authorization"]


def _set(path, value):
    def mutate(payload):
        target = payload
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        return payload
    return mutate


AUTH = ("payload", "authorization")

# (name, mutate, outcome) — outcome "consumed" means the variant is the
# SAME payment: second presentation must be 409 payment_ref_consumed.
# Outcome "rejected" means the form is non-canonical and must fail
# closed at verification (402) rather than produce a distinct ref.
MUTATIONS = [
    ("extra_auth_field",
     lambda p: _auth(p).__setitem__("junk", "x") or p, "consumed"),
    ("extra_payload_field",
     lambda p: p["payload"].__setitem__("junk", "x") or p, "consumed"),
    ("value_as_string", _set(AUTH + ("value",), "1000"), "consumed"),
    ("validAfter_as_string", _set(AUTH + ("validAfter",), "0"), "consumed"),
    ("validBefore_as_string",
     _set(AUTH + ("validBefore",), "9999999999"), "consumed"),
    ("nonce_uppercase", _set(AUTH + ("nonce",), "0x" + "AA" * 32),
     "consumed"),
    ("nonce_no_0x_prefix", _set(AUTH + ("nonce",), "aa" * 32), "consumed"),
    ("nonce_0X_prefix", _set(AUTH + ("nonce",), "0X" + "aa" * 32),
     "consumed"),
    ("signature_uppercase",
     _set(("payload", "signature"), "0x" + "AB" * 65), "consumed"),
    ("signature_0X_prefix",
     _set(("payload", "signature"), "0X" + "ab" * 65), "consumed"),
    ("signature_no_0x_prefix",
     _set(("payload", "signature"), "ab" * 65), "consumed"),
    ("to_lowercased", _set(AUTH + ("to",), PAY_TO.lower()), "consumed"),
    ("to_uppercased",
     _set(AUTH + ("to",), "0x" + PAY_TO[2:].upper()), "consumed"),
    ("from_lowercased", _set(AUTH + ("from",), PAYER.lower()), "consumed"),
    ("from_uppercased",
     _set(AUTH + ("from",), "0x" + PAYER[2:].upper()), "consumed"),
    # Non-canonical integer form: rejected, never coerced to a new ref.
    ("value_float", _set(AUTH + ("value",), 1000.0), "rejected"),
]


@pytest.mark.parametrize(
    "name,mutate,outcome", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_mutated_representation_cannot_open_second_escrow(
        name, mutate, outcome):
    endpoint = _endpoint()
    r1 = endpoint.handle(
        headers={"X-PAYMENT": payment_header(_base_payload())}, kwargs={})
    assert r1.status == 200
    variant = mutate(copy.deepcopy(_base_payload()))
    r2 = endpoint.handle(
        headers={"X-PAYMENT": payment_header(variant)}, kwargs={})
    if outcome == "consumed":
        assert r2.status == 409
        assert r2.body["error"] == "payment_ref_consumed"
    else:
        assert r2.status == 402
        assert r2.body["error"] == "payment_verification_failed"
    assert len(endpoint.platform._escrows) == 1
    assert endpoint.platform.released_total == AMOUNT
    assert endpoint.platform.refunded_total == 0


@pytest.mark.parametrize(
    "name,mutate,outcome", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_mutated_representation_ref_identity(name, mutate, outcome):
    """Accepted variants share the canonical ref; rejected ones get none."""
    fac = LocalFacilitator()
    req = _requirement()
    canonical = fac.verify_and_hold(
        payment_payload=_base_payload(), requirement=req)
    assert canonical.ok and canonical.payment_ref.startswith("localref_")
    variant = mutate(copy.deepcopy(_base_payload()))
    result = fac.verify_and_hold(payment_payload=variant, requirement=req)
    if outcome == "consumed":
        assert result.ok
        assert result.payment_ref == canonical.payment_ref
        assert result.payer == PAYER.lower()
    else:
        assert not result.ok
        assert result.payment_ref == ""


def test_canonical_presentation_ref_is_stable():
    req = _requirement()
    refs = set()
    for _ in range(3):
        result = LocalFacilitator().verify_and_hold(
            payment_payload=_base_payload(), requirement=req)
        assert result.ok
        refs.add(result.payment_ref)
    assert len(refs) == 1
    # And the raw helper agrees, deterministically.
    payload = _base_payload()
    ref = _stable_payment_ref(_auth(payload), payload["payload"]["signature"])
    assert ref == refs.pop()


def test_ref_distinctness_not_collapsed_by_normalization():
    base = _base_payload()
    ref = _stable_payment_ref(_auth(base), SIG)

    def ref_with(**auth_overrides):
        payload = copy.deepcopy(base)
        _auth(payload).update(auth_overrides)
        return _stable_payment_ref(_auth(payload), SIG)

    assert ref_with(nonce="0x" + "bb" * 32) != ref
    assert ref_with(value=AMOUNT + 1) != ref
    assert ref_with(validBefore=9999999998) != ref
    assert ref_with(**{"from": "0x3333333333333333333333333333333333333333"}) != ref
    other_sig = _stable_payment_ref(_auth(base), "0x" + "cd" * 65)
    assert other_sig != ref


MALFORMED = [
    ("missing_value", lambda p: _auth(p).pop("value")),
    ("missing_nonce", lambda p: _auth(p).pop("nonce")),
    ("missing_validAfter", lambda p: _auth(p).pop("validAfter")),
    ("missing_validBefore", lambda p: _auth(p).pop("validBefore")),
    ("missing_from", lambda p: _auth(p).pop("from")),
    ("missing_to", lambda p: _auth(p).pop("to")),
    ("value_hex_string", _set(AUTH + ("value",), "0x3e8")),
    ("value_junk_string", _set(AUTH + ("value",), "ten")),
    ("value_bool", _set(AUTH + ("value",), True)),
    ("value_negative", _set(AUTH + ("value",), -1)),
    ("validAfter_float", _set(AUTH + ("validAfter",), 0.0)),
    ("validBefore_hex_string", _set(AUTH + ("validBefore",), "0xff")),
    ("from_not_an_address", _set(AUTH + ("from",), "0x1234")),
    ("to_not_an_address", _set(AUTH + ("to",), "not-an-address")),
    ("nonce_wrong_length", _set(AUTH + ("nonce",), "0x" + "aa" * 16)),
    ("nonce_not_hex", _set(AUTH + ("nonce",), "0x" + "zz" * 32)),
    ("signature_not_hex", _set(("payload", "signature"), "0xzz")),
]


@pytest.mark.parametrize(
    "name,mutate", MALFORMED, ids=[m[0] for m in MALFORMED])
def test_malformed_authorization_fails_closed(name, mutate):
    payload = copy.deepcopy(_base_payload())
    mutate(payload)
    result = LocalFacilitator().verify_and_hold(
        payment_payload=payload, requirement=_requirement())
    assert not result.ok
    assert result.payment_ref == ""
    assert "malformed" in result.error or "missing" in result.error


def test_stable_payment_ref_raises_on_malformed_input():
    payload = _base_payload()
    _auth(payload)["value"] = 1000.0
    with pytest.raises(ValueError):
        _stable_payment_ref(_auth(payload), SIG)
    with pytest.raises(ValueError):
        _stable_payment_ref({"from": PAYER}, SIG)  # missing fields


# --- Empty payment_ref fails closed ---------------------------------------


@pytest.mark.parametrize("bad_ref", ["", None, "   "])
def test_platform_refuses_empty_payment_ref(bad_ref):
    platform = LocalReferencePlatform()
    with pytest.raises(MissingPaymentRef):
        platform.open_escrow(
            requirement=_requirement(), payer=PAYER, payment_ref=bad_ref)
    # MissingPaymentRef is also a ValueError (bad-argument contract).
    with pytest.raises(ValueError):
        platform.open_escrow(
            requirement=_requirement(), payer=PAYER, payment_ref=bad_ref)
    assert len(platform._escrows) == 0
    assert platform.released_total == 0


class _EmptyRefFacilitator:
    """Facilitator stub that (incorrectly) reports success, ref empty."""

    def verify_and_hold(self, *, payment_payload, requirement):
        return FacilitatorResult(True, payer=PAYER, payment_ref="")


def test_endpoint_treats_empty_facilitator_ref_as_error():
    endpoint = VerifiedEndpoint(
        lambda **kw: {"answer": 42}, requirement=_requirement(),
        spec={"required_keys": ["answer"]},
        facilitator=_EmptyRefFacilitator())
    resp = endpoint.handle(
        headers={"X-PAYMENT": payment_header(_base_payload())}, kwargs={})
    assert resp.status == 502
    assert resp.body["ok"] is False
    assert resp.body["error"] == "missing_payment_ref"
    assert len(endpoint.platform._escrows) == 0
    assert endpoint.platform.released_total == 0


def test_endpoint_repeated_empty_ref_presentations_open_no_escrows():
    endpoint = VerifiedEndpoint(
        lambda **kw: {"answer": 42}, requirement=_requirement(),
        spec={"required_keys": ["answer"]},
        facilitator=_EmptyRefFacilitator())
    header = payment_header(_base_payload())
    for _ in range(2):
        resp = endpoint.handle(headers={"X-PAYMENT": header}, kwargs={})
        assert resp.status == 502
    assert len(endpoint.platform._escrows) == 0


def test_json_round_trip_of_canonical_payload_keeps_ref():
    # Sanity: headers are JSON — the canonical payload survives the
    # dumps/loads round-trip with its ref intact.
    payload = json.loads(json.dumps(_base_payload()))
    result = LocalFacilitator().verify_and_hold(
        payment_payload=payload, requirement=_requirement())
    assert result.ok and result.payment_ref
