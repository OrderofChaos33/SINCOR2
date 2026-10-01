"""W-3 regression: EIP-712 domain must bind the deployment chain.

- chain_id is explicit per-deployment config: missing chain config fails
  closed (no silent 8453 default).
- A signature minted under one chainId does NOT verify under another
  chainId's domain config (cross-chain replay rejected).
- assert_domain_matches_chain hard-fails when the configured domain
  chainId differs from the live RPC eth_chainId (stub RPCs used; no
  network required).
"""
from __future__ import annotations

import pytest
from eth_account import Account

from marketplace.contract_net.eip712 import (
    ChainIdMismatchError,
    assert_domain_matches_chain,
    rpc_chain_id,
    sign_digest,
    typed_data_digest,
    typed_data_payload,
    verify_digest,
)
from marketplace.contract_net.types import ContractNetConfig, SigType

BASE_MAINNET = 8453
BASE_SEPOLIA = 84532  # contract deployment target

_PRIVKEY = "0x" + "3a" * 32
_ACCOUNT = Account.from_key(_PRIVKEY)

_BID = dict(
    auction_id="0x" + "aa" * 32,
    task_id="task-w3",
    agent=_ACCOUNT.address,
    price=1_000_000,
    estimated_tokens=400,
    nonce=1,
    deadline=9_999_999_999,
)


def _cfg(chain_id: int) -> ContractNetConfig:
    return ContractNetConfig(chain_id=chain_id)


def _signed_bid(config: ContractNetConfig):
    payload = typed_data_payload(config, **_BID)
    digest = typed_data_digest(config, **_BID)
    sig, sig_type = sign_digest(digest, private_key=_PRIVKEY)
    assert sig_type == SigType.SECP256K1.value
    return digest, payload, sig, sig_type


# --- fail closed on missing chain config -------------------------------------

def test_missing_chain_config_fails_closed():
    with pytest.raises(ValueError, match="chain_id"):
        ContractNetConfig()


def test_non_positive_chain_id_rejected():
    with pytest.raises(ValueError, match="chain_id"):
        ContractNetConfig(chain_id=0)


# --- cross-chain signatures do not verify ------------------------------------

@pytest.mark.parametrize("sign_chain, verify_chain", [(8453, 84532), (84532, 8453)])
def test_cross_chain_signature_rejected(sign_chain, verify_chain):
    cfg_sign = _cfg(sign_chain)
    cfg_verify = _cfg(verify_chain)
    digest, _, sig, sig_type = _signed_bid(cfg_sign)
    payload_verify = typed_data_payload(cfg_verify, **_BID)
    ok = verify_digest(
        digest,
        sig,
        sig_type=sig_type,
        expected_address=_ACCOUNT.address,
        typed_data=payload_verify,
    )
    assert ok is False, (
        f"W-3: signature minted on chainId {sign_chain} verified under "
        f"chainId {verify_chain} domain -- cross-chain replay possible"
    )


def test_same_chain_signature_still_verifies():
    """Honest flow: sign and verify under the same deployment domain."""
    cfg = _cfg(BASE_SEPOLIA)
    digest, payload, sig, sig_type = _signed_bid(cfg)
    ok = verify_digest(
        digest,
        sig,
        sig_type=sig_type,
        expected_address=_ACCOUNT.address,
        typed_data=payload,
    )
    assert ok is True


def test_same_chain_digest_stable_across_calls():
    """Byte-identity of the domain separator is unchanged by this fix."""
    cfg = _cfg(BASE_SEPOLIA)
    assert typed_data_digest(cfg, **_BID) == typed_data_digest(cfg, **_BID)


# --- live binding: domain chainId vs RPC eth_chainId --------------------------

class _Web3Stub:
    """Minimal web3-shaped stub; eth_chainId is the ONLY source of truth."""

    def __init__(self, chain_id: int):
        self.eth = type("eth", (), {"chain_id": chain_id})()


def test_assert_domain_matches_chain_ok_web3_stub():
    cfg = _cfg(BASE_SEPOLIA)
    assert assert_domain_matches_chain(cfg, _Web3Stub(BASE_SEPOLIA)) == BASE_SEPOLIA


def test_assert_domain_matches_chain_ok_int_and_hex():
    cfg = _cfg(BASE_SEPOLIA)
    assert assert_domain_matches_chain(cfg, BASE_SEPOLIA) == BASE_SEPOLIA
    assert assert_domain_matches_chain(cfg, hex(BASE_SEPOLIA)) == BASE_SEPOLIA
    assert assert_domain_matches_chain(cfg, str(BASE_SEPOLIA)) == BASE_SEPOLIA
    assert assert_domain_matches_chain(cfg, lambda: BASE_SEPOLIA) == BASE_SEPOLIA


@pytest.mark.parametrize("live", [8453, 1, 137])
def test_assert_domain_matches_chain_mismatch_hard_fails(live):
    """CI gate: signing domain chainId must equal the target RPC eth_chainId."""
    cfg = _cfg(BASE_SEPOLIA)
    with pytest.raises(ChainIdMismatchError):
        assert_domain_matches_chain(cfg, _Web3Stub(live))


def test_assert_domain_matches_chain_unset_config_fails():
    cfg = object.__new__(ContractNetConfig)  # bypass __init__ validation path
    object.__setattr__(cfg, "chain_id", None)  # frozen dataclass
    with pytest.raises(ChainIdMismatchError):
        assert_domain_matches_chain(cfg, BASE_SEPOLIA)


def test_rpc_chain_id_rejects_garbage():
    with pytest.raises(ValueError):
        rpc_chain_id(object())


# --- CI-style deployment gate -------------------------------------------------

def test_signing_domain_matches_deployment_rpc_chain_id():
    """The deployment's signing domain must equal the deployment RPC's chain.

    Uses a stub RPC (no network). In CI this is wired to the configured
    deployment RPC; here the stub stands in for the Base Sepolia endpoint.
    """
    deployment_config = ContractNetConfig(
        chain_id=BASE_SEPOLIA,  # explicit deployment configuration (W-3)
    )
    deployment_rpc = _Web3Stub(BASE_SEPOLIA)  # stub for the deployment RPC
    asserted = assert_domain_matches_chain(deployment_config, deployment_rpc)
    assert asserted == rpc_chain_id(deployment_rpc) == BASE_SEPOLIA
    assert deployment_config.chain_id != BASE_MAINNET, (
        "deployment must not silently reuse the Base mainnet chain id"
    )
