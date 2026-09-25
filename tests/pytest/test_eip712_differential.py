"""Permanent differential gate for the EIP-712 migration.

Our digest construction must stay byte-identical to eth_account's
``encode_typed_data`` (independent ABI-encoding path), and secp256k1
sign/recover must round-trip through eth_account's public API.

Deterministic vectors — no randomness. If any of these fail, the digest
format drifted and mainnet signature compatibility is at risk.
See docs/ops/AUCTION_SECURITY_DECISIONS.md.
"""

import pytest

from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_hash.auto import keccak as eth_keccak

from marketplace.contract_net.eip712 import (
    sign_digest,
    sign_hmac,
    typed_data_digest,
    typed_data_payload,
    verify_digest,
    verify_hmac,
)
from marketplace.contract_net.keccak import keccak256
from marketplace.contract_net.types import ContractNetConfig, SigType

TEST_PRIVKEY = "0x" + "7f" * 32
TEST_ADDRESS = Account.from_key(TEST_PRIVKEY).address

CONFIG = ContractNetConfig()

VECTORS = [
    dict(
        auction_id="0x" + "11" * 32,
        task_id="T-101",
        agent=TEST_ADDRESS,
        price=5_000_000,
        estimated_tokens=520,
        nonce=7,
        deadline=9_999_999_999,
    ),
    dict(
        auction_id="0x" + "22" * 32,
        task_id="",
        agent=TEST_ADDRESS,
        price=1,
        estimated_tokens=1,
        nonce=1,
        deadline=1,
        epoch_id="ep-9",
        epoch_root="0x" + "ab" * 32,
    ),
    dict(
        auction_id="0x" + "33" * 32,
        task_id="x" * 200,
        agent=TEST_ADDRESS,
        price=2**200,
        estimated_tokens=0,
        nonce=2**64,
        deadline=2**64,
    ),
]


def _eth_account_digest(payload):
    msg = encode_typed_data(full_message=payload)
    return eth_keccak(b"\x19\x01" + msg.header + msg.body)


def test_keccak_backend_fixture():
    assert (
        keccak256(b"").hex()
        == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    )


@pytest.mark.parametrize("vector", VECTORS)
def test_digest_matches_eth_account(vector):
    ours = typed_data_digest(CONFIG, **vector)
    payload = typed_data_payload(CONFIG, **vector)
    assert ours == _eth_account_digest(payload)


@pytest.mark.parametrize("vector", VECTORS)
def test_secp256k1_roundtrip_through_audited_lib(vector):
    digest = typed_data_digest(CONFIG, **vector)
    payload = typed_data_payload(CONFIG, **vector)
    signature, sig_type = sign_digest(digest, private_key=TEST_PRIVKEY)
    assert sig_type == SigType.SECP256K1.value
    # recover directly through eth_account (independent of our wrapper)
    msg = encode_typed_data(full_message=payload)
    assert Account.recover_message(msg, signature=signature) == TEST_ADDRESS
    # and through our verify_digest
    assert verify_digest(
        digest,
        signature,
        sig_type=sig_type,
        expected_address=TEST_ADDRESS,
        typed_data=payload,
    )


def test_verify_digest_rejects_secp256k1_without_typed_data():
    vector = VECTORS[0]
    digest = typed_data_digest(CONFIG, **vector)
    signature, sig_type = sign_digest(digest, private_key=TEST_PRIVKEY)
    assert not verify_digest(
        digest, signature, sig_type=sig_type, expected_address=TEST_ADDRESS
    )


def test_hmac_demo_path_still_functions():
    digest = keccak256(b"demo")
    sig = sign_hmac(digest, "secret")
    assert verify_hmac(digest, sig, "secret")
    assert not verify_hmac(digest, sig, "other")
