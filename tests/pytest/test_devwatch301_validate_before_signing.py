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
from marketplace.contract_net import engine as engine_module
from marketplace.contract_net.eip712 import (
    EMPTY_EPOCH_ROOT,
    BidSummaryMismatchError,
    bid_signing_summary,
    demo_signing_secret,
    sign_digest,
    to_hex,
    typed_data_digest,
    typed_data_payload,
    validate_bid_before_signing,
    validate_bid_summary,
)
from marketplace.contract_net.engine import ContractNetEngine
from marketplace.contract_net.types import (
    BASE_SEPOLIA_CHAIN_ID,
    AgentProfile,
    ContractNetConfig,
    TaskSpec,
)

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


# ---------------------------------------------------------------------------
# Round 2 — B1: the bid validator is WIRED into draft_bid (engine level)
# ---------------------------------------------------------------------------

_PRIVKEY_301 = "0x" + "7f" * 32


def _engine_and_bid_inputs():
    from eth_account import Account

    wallet = Account.from_key(_PRIVKEY_301).address
    config = ContractNetConfig(chain_id=BASE_SEPOLIA_CHAIN_ID)
    agent = AgentProfile(
        agent_id="agent-301",
        name="Agent 301",
        skills=("research",),
        wallet=wallet,
        true_min_price=1_000_000,
        estimated_tokens=400,
        private_key=_PRIVKEY_301,
    )
    task = TaskSpec(
        task_id="task-301",
        goal="research",
        requirements=("research",),
        budget_tokens=1000,
        max_price=2_000_000,
    )
    return config, agent, task


def test_engine_draft_bid_honest_output_identical_to_pre_gate_construction():
    """B1 honest-path invariance: draft_bid's digest, signature and
    typed_data are exactly what the pre-round-2 construction produced
    (payload built by typed_data_payload, digest by typed_data_digest,
    signed by sign_digest — computed here directly, no engine)."""
    config, agent, task = _engine_and_bid_inputs()
    engine = ContractNetEngine(config)
    now = 1_700_000_000
    auction_id = "0x" + "aa" * 32
    bid = engine.draft_bid(
        auction_id=auction_id, task=task, agent=agent, now=now, price=1_500_000
    )
    # Pre-gate construction, replicated field-by-field (first nonce = 1).
    nonce = 1
    deadline = now + config.bid_ttl_seconds
    kwargs = dict(
        auction_id=auction_id,
        task_id=task.task_id,
        agent=agent.wallet,
        price=1_500_000,
        estimated_tokens=agent.estimated_tokens,
        nonce=nonce,
        deadline=deadline,
        epoch_id="",
        epoch_root=EMPTY_EPOCH_ROOT,
    )
    expected_digest = typed_data_digest(config, **kwargs)
    expected_signature, expected_sig_type = sign_digest(
        expected_digest,
        private_key=agent.private_key,
        signing_secret=demo_signing_secret(agent.agent_id),
    )
    assert bid.digest == to_hex(expected_digest)
    assert bid.signature == expected_signature
    assert bid.sig_type == expected_sig_type
    assert bid.typed_data == typed_data_payload(config, **kwargs)
    assert (bid.price, bid.nonce, bid.deadline) == (1_500_000, nonce, deadline)
    assert bid.valid is True


def _tampered_payload_fn(mutate):
    real_payload = engine_module.typed_data_payload

    def fake(config, **kwargs):
        payload = copy.deepcopy(real_payload(config, **kwargs))
        mutate(payload)
        return payload

    return fake


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["message"].__setitem__("price", str(int(p["message"]["price"]) + 1)),
        lambda p: p["message"].__setitem__("deadline", str(int(p["message"]["deadline"]) + 999)),
        lambda p: p["message"].__setitem__("agent", EVIL),
        lambda p: p["domain"].__setitem__("chainId", 1),
        lambda p: p["domain"].__setitem__("verifyingContract", EVIL),
    ],
    ids=["price", "deadline", "agent", "chainId", "verifyingContract"],
)
def test_engine_draft_bid_refuses_tampered_payload_before_signing(
    monkeypatch, mutate
):
    """B1: if the payload built inside draft_bid differs in ANY field
    from the engine's own intent (or its config domain), draft_bid raises
    and sign_digest is NEVER called — no signature is produced."""
    config, agent, task = _engine_and_bid_inputs()
    engine = ContractNetEngine(config)
    sign_calls = []
    real_sign = engine_module.sign_digest

    def spy_sign(*args, **kwargs):
        sign_calls.append((args, kwargs))
        return real_sign(*args, **kwargs)

    monkeypatch.setattr(engine_module, "sign_digest", spy_sign)
    monkeypatch.setattr(
        engine_module, "typed_data_payload", _tampered_payload_fn(mutate)
    )
    with pytest.raises(BidSummaryMismatchError):
        engine.draft_bid(
            auction_id="0x" + "aa" * 32,
            task=task,
            agent=agent,
            now=1_700_000_000,
            price=1_500_000,
        )
    assert sign_calls == []  # refused BEFORE signing


# ---------------------------------------------------------------------------
# Round 2 — B2: bid validator pins the whole payload shape
# ---------------------------------------------------------------------------


def _intent(**overrides):
    intent = dict(
        auctionId=_BID["auction_id"],
        taskId=_BID["task_id"],
        agent=_BID["agent"],
        price=_BID["price"],
        estimatedTokens=_BID["estimated_tokens"],
        nonce=_BID["nonce"],
        deadline=_BID["deadline"],
        epochId="",
        epochRoot=EMPTY_EPOCH_ROOT,
    )
    intent.update(overrides)
    return intent


def test_bid_honest_payload_passes_with_config_and_intent():
    payload = typed_data_payload(_CONFIG, **_BID)
    summary = bid_signing_summary(payload)
    assert (
        validate_bid_summary(
            payload, summary, config=_CONFIG, expected_message=_intent()
        )
        == summary
    )
    # expected_domain may be a ContractNetConfig or a plain dict.
    domain = {
        "name": _CONFIG.domain_name,
        "version": _CONFIG.domain_version,
        "chainId": _CONFIG.chain_id,
        "verifyingContract": _CONFIG.verifying_contract,
    }
    assert validate_bid_summary(payload, expected_domain=_CONFIG) is not None
    assert validate_bid_summary(payload, expected_domain=domain) is not None


def test_bid_refuses_extra_top_level_field_with_self_consistent_summary():
    payload = typed_data_payload(_CONFIG, **_BID)
    payload["descriptor"] = {"price": "1"}  # hidden top-level field
    regenerated = bid_signing_summary(payload)  # self-consistent summary
    with pytest.raises(BidSummaryMismatchError, match="top-level"):
        validate_bid_summary(payload, regenerated, config=_CONFIG)


def test_bid_refuses_missing_top_level_field():
    payload = typed_data_payload(_CONFIG, **_BID)
    del payload["types"]
    with pytest.raises(BidSummaryMismatchError, match="top-level"):
        validate_bid_summary(payload)


def test_bid_refuses_mutated_chain_id_when_expected_domain_supplied():
    payload = typed_data_payload(_CONFIG, **_BID)
    payload["domain"]["chainId"] = 8453  # mutated chainId
    regenerated = bid_signing_summary(payload)  # self-consistent summary
    with pytest.raises(BidSummaryMismatchError, match="domain"):
        validate_bid_summary(payload, regenerated, config=_CONFIG)
    with pytest.raises(BidSummaryMismatchError, match="domain"):
        validate_bid_summary(payload, regenerated, expected_domain=_CONFIG)


def test_bid_refuses_extra_domain_field_with_self_consistent_summary():
    payload = typed_data_payload(_CONFIG, **_BID)
    payload["domain"]["hiddenRecipient"] = EVIL
    regenerated = bid_signing_summary(payload)
    with pytest.raises(BidSummaryMismatchError, match="domain"):
        validate_bid_summary(payload, regenerated)


def test_bid_refuses_extra_types_bid_entry():
    payload = typed_data_payload(_CONFIG, **_BID)
    payload["types"]["Bid"] = payload["types"]["Bid"] + [
        {"name": "hiddenRecipient", "type": "address"}
    ]
    with pytest.raises(BidSummaryMismatchError, match="types"):
        validate_bid_summary(payload)


def test_bid_refuses_altered_types_entry():
    payload = typed_data_payload(_CONFIG, **_BID)
    payload["types"]["Bid"][3] = {"name": "price", "type": "uint128"}
    with pytest.raises(BidSummaryMismatchError, match="types"):
        validate_bid_summary(payload)


@pytest.mark.parametrize(
    "field,bad",
    [
        ("price", 1_000_000.0),  # equal-valued float
        ("price", True),  # bool
        ("price", "0xF4240"),  # non-canonical (hex) string
        ("price", "1_000_000"),  # non-canonical string
        ("estimatedTokens", 400.0),
        ("nonce", False),
        ("deadline", 9_999_999_999.0),
        ("deadline", "tomorrow"),
    ],
)
def test_bid_refuses_non_int_numeric_fields(field, bad):
    payload = typed_data_payload(_CONFIG, **_BID)
    payload["message"][field] = bad
    with pytest.raises(BidSummaryMismatchError, match=field):
        validate_bid_summary(payload)


@pytest.mark.parametrize(
    "field,bad",
    [
        ("agent", "0x1234"),  # too short
        ("agent", "not-an-address"),
        ("auctionId", "0x" + "aa" * 16),  # not bytes32
        ("epochRoot", "0xzz" + "aa" * 31),  # non-hex
    ],
)
def test_bid_refuses_malformed_address_and_bytes32_fields(field, bad):
    payload = typed_data_payload(_CONFIG, **_BID)
    payload["message"][field] = bad
    with pytest.raises(BidSummaryMismatchError, match=field):
        validate_bid_summary(payload)


def test_bid_refuses_arbitrary_values_when_intent_supplied():
    """summary=None is NOT a free pass when the intent is supplied (the
    wired draft_bid call site always supplies it): an arbitrary price /
    agent / deadline that differs from the intent is refused."""
    payload = typed_data_payload(_CONFIG, **_BID)
    payload["message"]["price"] = str(_BID["price"] + 1)
    with pytest.raises(BidSummaryMismatchError, match="intent"):
        validate_bid_summary(payload, config=_CONFIG, expected_message=_intent())
    payload2 = typed_data_payload(_CONFIG, **_BID)
    payload2["message"]["agent"] = EVIL
    with pytest.raises(BidSummaryMismatchError, match="intent"):
        validate_bid_summary(
            payload2, config=_CONFIG, expected_message=_intent()
        )


def test_bid_library_level_wellformed_arbitrary_values_documented_carveout():
    """Pins the documented carve-out: with NO summary, NO expected domain
    and NO intent, the library accepts a well-formed payload — safety
    comes from the wired draft_bid call site supplying the intent (B1),
    as stated in validate_bid_summary's docstring."""
    other = dict(_BID, price=7, deadline=8_888_888_888, agent=EVIL)
    payload = typed_data_payload(_CONFIG, **other)
    assert validate_bid_summary(payload) == bid_signing_summary(payload)


# ---------------------------------------------------------------------------
# Round 2 — B3: x402 validator type strictness + standalone expiry sanity
# ---------------------------------------------------------------------------


def test_x402_refuses_equal_valued_float_valid_before():
    req = _req()
    td = _honest_typed_data(req)
    td["message"]["validBefore"] = float(req.valid_before)  # == by value
    with pytest.raises(SigningValidationError, match="validBefore"):
        validate_typed_data_before_signing(
            td, req, wallet_address=WALLET,
            expected_valid_before=req.valid_before,
        )


def test_x402_refuses_float_and_bool_valid_after():
    req = _req()  # valid_after = 0
    for bad in (0.0, False):
        td = _honest_typed_data(req)
        td["message"]["validAfter"] = bad  # both == 0 by value
        with pytest.raises(SigningValidationError, match="validAfter"):
            validate_typed_data_before_signing(
                td, req, wallet_address=WALLET,
                expected_valid_before=req.valid_before,
            )


def test_x402_refuses_bool_and_str_valid_before():
    req = _req()
    for bad in (True, str(req.valid_before)):
        td = _honest_typed_data(req)
        td["message"]["validBefore"] = bad
        with pytest.raises(SigningValidationError, match="validBefore"):
            validate_typed_data_before_signing(
                td, req, wallet_address=WALLET,
                expected_valid_before=req.valid_before,
            )


def test_x402_expiry_checked_when_no_expected_supplied_and_req_unset():
    """B3: req.valid_before falsy + no expected_valid_before must NOT
    skip the expiry check — a past validBefore is refused by the
    validator's own sanity check."""
    now = int(time.time())
    req_unset = _req(valid_before=0)
    td = _honest_typed_data(_req())
    td["message"]["validBefore"] = now - 60  # already expired
    with pytest.raises(SigningValidationError, match="expiry sanity"):
        validate_typed_data_before_signing(td, req_unset, wallet_address=WALLET)


def test_x402_expiry_sanity_requires_valid_before_after_valid_after():
    now = int(time.time())
    req = _req(valid_after=now + 700, valid_before=0)
    td = _honest_typed_data(_req())
    td["message"]["validAfter"] = now + 700
    td["message"]["validBefore"] = now + 600  # future, but <= validAfter
    with pytest.raises(SigningValidationError, match="expiry sanity"):
        validate_typed_data_before_signing(td, req, wallet_address=WALLET)


def test_x402_expiry_sanity_rejects_non_positive_valid_before():
    req_unset = _req(valid_before=0)
    td = _honest_typed_data(_req())
    td["message"]["validBefore"] = 0
    with pytest.raises(SigningValidationError, match="expiry sanity"):
        validate_typed_data_before_signing(td, req_unset, wallet_address=WALLET)


def test_x402_no_expected_supplied_honest_future_expiry_passes():
    now = int(time.time())
    req_unset = _req(valid_before=0)
    td = _honest_typed_data(_req())
    td["message"]["validBefore"] = now + 600
    summary = validate_typed_data_before_signing(
        td, req_unset, wallet_address=WALLET
    )
    assert summary["message"]["validBefore"] == now + 600
