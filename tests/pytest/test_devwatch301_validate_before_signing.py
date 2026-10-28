"""Dev-watch item 301: validate before signing; descriptors are untrusted.

Cyfrin 1inch clear-signing hedge turned into code:
(a) the full typed-data payload (recipient, expiry, executor/spend) is
    validated programmatically against the checked requirements before
    the signer is called, and any summary is generated from the exact
    typed_data being signed — never from a separate descriptor;
(b) a payload whose auxiliary field (expiry / recipient / executor)
    differs from the summary shown must be refused, not signed;
(c) a third-party descriptor is untrusted display metadata: mutating it
    changes neither what is validated nor what is signed.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time

import pytest

from sincor2.x402_buyer import (
    BASE_CHAIN_ID,
    USDC_BASE_ADDRESS,
    PaymentRequirements,
    SigningValidationError,
    X402Buyer,
    signing_summary,
    validate_typed_data_before_signing,
)
from marketplace.contract_net.eip712 import (
    BidSummaryMismatchError,
    bid_signing_summary,
    typed_data_digest,
    typed_data_payload,
    validate_bid_before_signing,
    validate_bid_summary,
)
from marketplace.contract_net.types import BASE_SEPOLIA_CHAIN_ID, ContractNetConfig

WALLET = "0x" + "11" * 20
PAY_TO = "0x" + "22" * 20
EVIL = "0x" + "99" * 20
NONCE = "0x" + "cc" * 32


def _req(**overrides):
    now = int(time.time())
    base = dict(
        amount_atomic=5000,
        amount_usd=0.005,
        asset=USDC_BASE_ADDRESS,
        network="eip155:8453",
        pay_to=PAY_TO,
        nonce=NONCE,
        valid_after=0,
        valid_before=now + 600,
        resource="/api/test",
        description="test",
    )
    base.update(overrides)
    return PaymentRequirements(**base)


def _honest_typed_data(req):
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "TransferWithAuthorization": [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"},
                {"name": "nonce", "type": "bytes32"},
            ],
        },
        "domain": {
            "name": "USD Coin",
            "version": "2",
            "chainId": BASE_CHAIN_ID,
            "verifyingContract": USDC_BASE_ADDRESS,
        },
        "primaryType": "TransferWithAuthorization",
        "message": {
            "from": WALLET,
            "to": req.pay_to,
            "value": req.amount_atomic,
            "validAfter": req.valid_after or 0,
            "validBefore": req.valid_before,
            "nonce": req.nonce,
        },
    }


def _hash_signer(captured):
    def signer(typed_data):
        captured.append(copy.deepcopy(typed_data))
        blob = json.dumps(typed_data, sort_keys=True).encode()
        return "0x" + hashlib.sha256(blob).hexdigest() + "ab" * 32 + "cd"

    return signer


# ---------------------------------------------------------------------------
# Honest path: validation passes and the signed payload is unchanged
# ---------------------------------------------------------------------------


def test_honest_path_signs_exact_expected_typed_data():
    req = _req()
    captured = []
    buyer = X402Buyer(WALLET, _hash_signer(captured))
    signed = buyer.sign_authorization(req)
    # The typed_data handed to the signer is byte-for-byte (dict-equal to)
    # the pre-301 construction: the gate added no field and changed none.
    assert captured == [_honest_typed_data(req)]
    assert signed.authorization == _honest_typed_data(req)["message"]
    # Signature is the deterministic hash-signer output over that payload.
    expected_sig = _hash_signer([])(_honest_typed_data(req))
    assert signed.signature == expected_sig


def test_validate_honest_typed_data_returns_canonical_summary():
    req = _req()
    td = _honest_typed_data(req)
    summary = validate_typed_data_before_signing(
        td, req, wallet_address=WALLET, expected_valid_before=req.valid_before
    )
    assert summary == signing_summary(td)
    assert summary["message"]["to"] == PAY_TO
    assert summary["message"]["validBefore"] == req.valid_before


# ---------------------------------------------------------------------------
# Summary is derived from the exact typed_data being signed
# ---------------------------------------------------------------------------


def test_summary_is_derived_from_exact_typed_data_not_requirements():
    req = _req()
    td = _honest_typed_data(req)
    summary = signing_summary(td)
    assert summary["domain"] == td["domain"]
    assert summary["message"] == td["message"]
    # Mutating the payload changes the summary — the summary tracks the
    # exact bytes, not some stored/separate description of the payment.
    td2 = copy.deepcopy(td)
    td2["message"]["validBefore"] = req.valid_before + 111
    assert signing_summary(td2)["message"]["validBefore"] == req.valid_before + 111
    assert signing_summary(td2) != summary
    # And that mutated payload is refused against the checked req.
    with pytest.raises(SigningValidationError, match="validBefore"):
        validate_typed_data_before_signing(
            td2, req, wallet_address=WALLET,
            expected_valid_before=req.valid_before,
        )


# ---------------------------------------------------------------------------
# Hedge (b): summary differs from payload (expiry / recipient) -> refused
# ---------------------------------------------------------------------------


def test_sign_refused_when_summary_expiry_differs_from_payload():
    req = _req()
    captured = []
    buyer = X402Buyer(WALLET, _hash_signer(captured))
    good_summary = signing_summary(_honest_typed_data(req))
    shown = copy.deepcopy(good_summary)
    shown["message"]["validBefore"] = req.valid_before + 3600  # hidden expiry
    with pytest.raises(SigningValidationError, match="summary"):
        buyer.sign_authorization(req, summary=shown)
    assert captured == []  # signer never called: refused, not signed


def test_sign_refused_when_summary_recipient_differs_from_payload():
    req = _req()
    captured = []
    buyer = X402Buyer(WALLET, _hash_signer(captured))
    shown = copy.deepcopy(signing_summary(_honest_typed_data(req)))
    shown["message"]["to"] = EVIL  # hidden recipient swap in the summary
    with pytest.raises(SigningValidationError, match="summary"):
        buyer.sign_authorization(req, summary=shown)
    assert captured == []


def test_sign_accepts_summary_matching_exact_payload():
    req = _req()
    captured = []
    buyer = X402Buyer(WALLET, _hash_signer(captured))
    summary = signing_summary(_honest_typed_data(req))
    signed = buyer.sign_authorization(req, summary=summary)
    assert signed.authorization["to"] == PAY_TO
    assert len(captured) == 1


# ---------------------------------------------------------------------------
# Field-by-field refusal: recipient / spend / expiry / nonce / domain
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,bad,match",
    [
        ("to", EVIL, "recipient"),
        ("value", 999999, "value"),
        ("validBefore", 9999999999, "validBefore"),
        ("validAfter", 12345, "validAfter"),
        ("nonce", "0x" + "dd" * 32, "nonce"),
        ("from", EVIL, "from"),
    ],
)
def test_validate_refuses_each_mismatched_field(field, bad, match):
    req = _req()
    td = _honest_typed_data(req)
    td["message"][field] = bad
    with pytest.raises(SigningValidationError, match=match):
        validate_typed_data_before_signing(
            td, req, wallet_address=WALLET,
            expected_valid_before=req.valid_before,
        )


def test_validate_refuses_substituted_domain():
    req = _req()
    td = _honest_typed_data(req)
    td["domain"]["verifyingContract"] = EVIL
    with pytest.raises(SigningValidationError, match="domain"):
        validate_typed_data_before_signing(
            td, req, wallet_address=WALLET,
            expected_valid_before=req.valid_before,
        )


# ---------------------------------------------------------------------------
# Extra / hidden fields are refused
# ---------------------------------------------------------------------------


def test_validate_refuses_extra_hidden_message_field():
    req = _req()
    td = _honest_typed_data(req)
    td["message"]["executor"] = EVIL  # hidden executor-restriction field
    with pytest.raises(SigningValidationError, match="extra"):
        validate_typed_data_before_signing(
            td, req, wallet_address=WALLET,
            expected_valid_before=req.valid_before,
        )


def test_validate_refuses_missing_message_field():
    req = _req()
    td = _honest_typed_data(req)
    del td["message"]["validBefore"]
    with pytest.raises(SigningValidationError, match="missing"):
        validate_typed_data_before_signing(
            td, req, wallet_address=WALLET,
            expected_valid_before=req.valid_before,
        )


def test_validate_refuses_extra_top_level_and_domain_fields():
    req = _req()
    td = _honest_typed_data(req)
    td["descriptor"] = {"to": EVIL}
    with pytest.raises(SigningValidationError, match="extra"):
        validate_typed_data_before_signing(
            td, req, wallet_address=WALLET,
            expected_valid_before=req.valid_before,
        )
    td2 = _honest_typed_data(req)
    td2["domain"]["hiddenRecipient"] = EVIL
    with pytest.raises(SigningValidationError, match="domain"):
        validate_typed_data_before_signing(
            td2, req, wallet_address=WALLET,
            expected_valid_before=req.valid_before,
        )


# ---------------------------------------------------------------------------
# (c) Descriptor mutation changes neither validation nor what is signed
# ---------------------------------------------------------------------------


def test_descriptor_mutation_does_not_change_validation_or_signature():
    req = _req()
    descriptor = {"to": PAY_TO, "validBefore": req.valid_before, "note": "ok"}
    td = _honest_typed_data(req)
    before = validate_typed_data_before_signing(
        td, req, wallet_address=WALLET,
        expected_valid_before=req.valid_before, descriptor=descriptor,
    )
    # Attacker / buggy display layer rewrites the descriptor wholesale.
    descriptor.update({"to": EVIL, "validBefore": 9999999999, "value": 10**9})
    after = validate_typed_data_before_signing(
        td, req, wallet_address=WALLET,
        expected_valid_before=req.valid_before, descriptor=descriptor,
    )
    assert before == after == signing_summary(td)

    captured = []
    buyer = X402Buyer(WALLET, _hash_signer(captured))
    signed = buyer.sign_authorization(req, descriptor=descriptor)
    assert captured == [_honest_typed_data(req)]
    assert signed.authorization["to"] == PAY_TO
    assert signed.authorization["value"] == req.amount_atomic


# ---------------------------------------------------------------------------
# Bid payloads (eip712.py): deadline = expiry, agent = executor identity
# ---------------------------------------------------------------------------

_CONFIG = ContractNetConfig(chain_id=BASE_SEPOLIA_CHAIN_ID)
_BID = dict(
    auction_id="0x" + "aa" * 32,
    task_id="task-301",
    agent="0x" + "44" * 20,
    price=1_000_000,
    estimated_tokens=400,
    nonce=3,
    deadline=9_999_999_999,
)


def test_bid_honest_summary_roundtrip_and_digest_unchanged():
    payload = typed_data_payload(_CONFIG, **_BID)
    snapshot = copy.deepcopy(payload)
    digest_before = typed_data_digest(_CONFIG, **_BID)
    summary = bid_signing_summary(payload)
    assert validate_bid_summary(payload, summary) == summary
    assert validate_bid_before_signing(payload, summary) == summary
    assert summary["message"]["deadline"] == str(_BID["deadline"])
    assert summary["message"]["agent"] == _BID["agent"]
    # The gate is additive and pure: payload and digest are untouched.
    assert payload == snapshot
    assert typed_data_digest(_CONFIG, **_BID) == digest_before


def test_bid_refused_when_summary_deadline_differs():
    payload = typed_data_payload(_CONFIG, **_BID)
    shown = copy.deepcopy(bid_signing_summary(payload))
    shown["message"]["deadline"] = "1"  # summary shows a different expiry
    with pytest.raises(BidSummaryMismatchError, match="summary"):
        validate_bid_summary(payload, shown)


def test_bid_refused_when_summary_agent_or_price_differs():
    payload = typed_data_payload(_CONFIG, **_BID)
    for field, bad in [("agent", EVIL), ("price", "1")]:
        shown = copy.deepcopy(bid_signing_summary(payload))
        shown["message"][field] = bad
        with pytest.raises(BidSummaryMismatchError):
            validate_bid_summary(payload, shown)


def test_bid_refuses_extra_hidden_message_field():
    payload = typed_data_payload(_CONFIG, **_BID)
    payload["message"]["hiddenRecipient"] = EVIL
    with pytest.raises(BidSummaryMismatchError, match="extra"):
        validate_bid_summary(payload)


def test_bid_descriptor_mutation_is_ignored():
    payload = typed_data_payload(_CONFIG, **_BID)
    summary = bid_signing_summary(payload)
    descriptor = {"agent": EVIL, "deadline": "1", "price": "1"}
    assert validate_bid_summary(payload, summary, descriptor=descriptor) == summary
