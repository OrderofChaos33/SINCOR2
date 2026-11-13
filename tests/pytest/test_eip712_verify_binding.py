"""W-116 regression gate: the EIP-712 verify side must bind typed_data.

Red-team hunt 32 (CONFIRMED mechanism): ``ContractNetEngine._validate_bid``
recomputed the digest from config + scalar fields, but ``verify_digest``'s
secp256k1 path recovered the signer from attacker-supplied ``bid.typed_data``
without ever checking that the typed_data's digest equals the recomputed
digest or that its message matches the scalars. A bid signed at price 100
cleared as price 1, and a chainId=1 signature cleared on chain 84532.

These tests pin the binding at both layers:

- engine layer: ``_validate_bid`` refuses a SealedBid whose typed_data does
  not exactly match the scalar fields + engine config domain;
- library layer: ``verify_digest`` refuses when the digest committed by
  ``typed_data`` (via eth_account's ``encode_typed_data``) differs from the
  digest it is asked to verify.
"""

import copy

import pytest
from eth_account import Account

from marketplace.contract_net.eip712 import (
    sign_digest,
    to_hex,
    typed_data_digest,
    typed_data_payload,
    verify_digest,
)
from marketplace.contract_net.engine import ContractNetEngine
from marketplace.contract_net.types import (
    BASE_SEPOLIA_CHAIN_ID,
    AgentProfile,
    ContractNetConfig,
    TaskSpec,
)

NOW = 1_700_000_000
AUCTION_ID = "0x" + "ab" * 32
EPOCH_ROOT = "0x" + "00" * 32

_KEY = Account.create()
WALLET = _KEY.address
CFG = ContractNetConfig(chain_id=BASE_SEPOLIA_CHAIN_ID)


def _agent() -> AgentProfile:
    return AgentProfile(
        agent_id="a1",
        name="a1",
        skills=("s1",),
        wallet=WALLET,
        private_key=_KEY.key.hex(),
        true_min_price=100,
        estimated_tokens=800,
    )


def _task() -> TaskSpec:
    return TaskSpec(
        task_id="task-1",
        goal="t",
        requirements=("s1",),
        budget_tokens=800,
        max_price=10_000,
    )


def _honest_bid(engine: ContractNetEngine, agent: AgentProfile, price: int = 100):
    return engine.draft_bid(
        auction_id=AUCTION_ID,
        task=_task(),
        agent=agent,
        now=NOW,
        price=price,
        epoch_id="e1",
        epoch_merkle_root=EPOCH_ROOT,
    )


def _revalidate(engine, bid, agent):
    bid.valid = True
    bid.reject_reason = ""
    engine._validate_bid(bid, _task(), agent, now=NOW, signing_secret="")
    return bid


def _digest(cfg, **overrides):
    fields = dict(
        auction_id=AUCTION_ID,
        task_id="task-1",
        agent=WALLET,
        price=100,
        estimated_tokens=800,
        nonce=1,
        deadline=NOW + 120,
        epoch_id="e1",
        epoch_root=EPOCH_ROOT,
    )
    fields.update(overrides)
    return typed_data_digest(cfg, **fields)


# --- control: the engine's own honest bid still validates --------------------


def test_honest_draft_bid_validates_roundtrip():
    engine = ContractNetEngine(config=CFG)
    agent = _agent()
    bid = _honest_bid(engine, agent)
    assert bid.sig_type == "eip712-secp256k1"
    assert bid.valid is True
    _revalidate(engine, copy.deepcopy(bid), agent)
    assert bid.valid is True


def test_verify_digest_honest_triple_verifies():
    payload = typed_data_payload(
        CFG,
        auction_id=AUCTION_ID,
        task_id="task-1",
        agent=WALLET,
        price=100,
        estimated_tokens=800,
        nonce=1,
        deadline=NOW + 120,
        epoch_id="e1",
        epoch_root=EPOCH_ROOT,
    )
    digest = _digest(CFG)
    signature, sig_type = sign_digest(digest, private_key=_KEY.key.hex())
    assert verify_digest(
        digest,
        signature,
        sig_type=sig_type,
        expected_address=WALLET,
        typed_data=payload,
    )


# --- engine layer: substitution shapes from the hunt-32 PoC ------------------


def test_price_substitution_rejected():
    """Signed at price 100, presented with scalar price 1 + digest for 1."""
    engine = ContractNetEngine(config=CFG)
    agent = _agent()
    bid = _honest_bid(engine, agent, price=100)
    tampered = copy.deepcopy(bid)
    tampered.price = 1
    tampered.digest = to_hex(
        _digest(CFG, price=1, nonce=bid.nonce, deadline=bid.deadline)
    )
    # signature + typed_data still commit to price 100
    _revalidate(engine, tampered, agent)
    assert tampered.valid is False
    assert tampered.reject_reason == "typed_data_mismatch"


def test_chainid_substitution_rejected():
    """Signature + typed_data minted under chainId=1, scalars/digest on 84532."""
    engine = ContractNetEngine(config=CFG)
    agent = _agent()
    bid = _honest_bid(engine, agent, price=100)
    cfg1 = ContractNetConfig(chain_id=1)
    payload1 = typed_data_payload(
        cfg1,
        auction_id=AUCTION_ID,
        task_id="task-1",
        agent=WALLET,
        price=100,
        estimated_tokens=800,
        nonce=bid.nonce,
        deadline=bid.deadline,
        epoch_id="e1",
        epoch_root=EPOCH_ROOT,
    )
    digest_chain1 = _digest(
        cfg1, nonce=bid.nonce, deadline=bid.deadline
    )
    sig1, _ = sign_digest(digest_chain1, private_key=_KEY.key.hex())
    tampered = copy.deepcopy(bid)
    tampered.signature = sig1
    tampered.typed_data = payload1
    # bid.digest stays the honest 84532 digest for the same scalars
    _revalidate(engine, tampered, agent)
    assert tampered.valid is False
    assert tampered.reject_reason == "typed_data_mismatch"


def test_verifying_contract_substitution_rejected():
    """Same chain, but typed_data signed under a different verifyingContract."""
    engine = ContractNetEngine(config=CFG)
    agent = _agent()
    bid = _honest_bid(engine, agent, price=100)
    cfg_alt = ContractNetConfig(
        chain_id=BASE_SEPOLIA_CHAIN_ID, verifying_contract="0x" + "11" * 20
    )
    payload_alt = typed_data_payload(
        cfg_alt,
        auction_id=AUCTION_ID,
        task_id="task-1",
        agent=WALLET,
        price=100,
        estimated_tokens=800,
        nonce=bid.nonce,
        deadline=bid.deadline,
        epoch_id="e1",
        epoch_root=EPOCH_ROOT,
    )
    digest_alt = _digest(cfg_alt, nonce=bid.nonce, deadline=bid.deadline)
    sig_alt, _ = sign_digest(digest_alt, private_key=_KEY.key.hex())
    tampered = copy.deepcopy(bid)
    tampered.signature = sig_alt
    tampered.typed_data = payload_alt
    _revalidate(engine, tampered, agent)
    assert tampered.valid is False
    assert tampered.reject_reason == "typed_data_mismatch"


def test_deadline_substitution_rejected():
    """typed_data commits to a later deadline than the scalar fields claim."""
    engine = ContractNetEngine(config=CFG)
    agent = _agent()
    bid = _honest_bid(engine, agent, price=100)
    late = bid.deadline + 100_000
    payload_late = typed_data_payload(
        CFG,
        auction_id=AUCTION_ID,
        task_id="task-1",
        agent=WALLET,
        price=100,
        estimated_tokens=800,
        nonce=bid.nonce,
        deadline=late,
        epoch_id="e1",
        epoch_root=EPOCH_ROOT,
    )
    digest_late = _digest(CFG, nonce=bid.nonce, deadline=late)
    sig_late, _ = sign_digest(digest_late, private_key=_KEY.key.hex())
    tampered = copy.deepcopy(bid)
    tampered.signature = sig_late
    tampered.typed_data = payload_late
    # scalar deadline + bid.digest remain the honest (earlier) pair
    _revalidate(engine, tampered, agent)
    assert tampered.valid is False
    assert tampered.reject_reason == "typed_data_mismatch"


# --- library layer: verify_digest must bind digest <-> typed_data ------------


def test_verify_digest_rejects_mismatched_digest():
    """Honest signature + payload, but the digest presented is for price 1."""
    payload = typed_data_payload(
        CFG,
        auction_id=AUCTION_ID,
        task_id="task-1",
        agent=WALLET,
        price=100,
        estimated_tokens=800,
        nonce=1,
        deadline=NOW + 120,
        epoch_id="e1",
        epoch_root=EPOCH_ROOT,
    )
    digest100 = _digest(CFG)
    signature, sig_type = sign_digest(digest100, private_key=_KEY.key.hex())
    digest1 = _digest(CFG, price=1)
    assert not verify_digest(
        digest1,
        signature,
        sig_type=sig_type,
        expected_address=WALLET,
        typed_data=payload,
    )


def test_verify_digest_rejects_cross_chain_payload():
    """Payload + signature under chainId=1 must not verify against an
    84532 digest for the same scalar fields."""
    cfg1 = ContractNetConfig(chain_id=1)
    payload1 = typed_data_payload(
        cfg1,
        auction_id=AUCTION_ID,
        task_id="task-1",
        agent=WALLET,
        price=100,
        estimated_tokens=800,
        nonce=1,
        deadline=NOW + 120,
        epoch_id="e1",
        epoch_root=EPOCH_ROOT,
    )
    digest_chain1 = _digest(cfg1)
    sig1, sig_type = sign_digest(digest_chain1, private_key=_KEY.key.hex())
    digest_84532 = _digest(CFG)
    assert not verify_digest(
        digest_84532,
        sig1,
        sig_type=sig_type,
        expected_address=WALLET,
        typed_data=payload1,
    )


def test_verify_digest_rejects_garbage_typed_data():
    payload = typed_data_payload(
        CFG,
        auction_id=AUCTION_ID,
        task_id="task-1",
        agent=WALLET,
        price=100,
        estimated_tokens=800,
        nonce=1,
        deadline=NOW + 120,
        epoch_id="e1",
        epoch_root=EPOCH_ROOT,
    )
    digest = _digest(CFG)
    signature, sig_type = sign_digest(digest, private_key=_KEY.key.hex())
    for garbage in ({"primaryType": "Bid"}, {"domain": {}, "message": {}}, []):
        assert not verify_digest(
            digest,
            signature,
            sig_type=sig_type,
            expected_address=WALLET,
            typed_data=garbage,
        )
