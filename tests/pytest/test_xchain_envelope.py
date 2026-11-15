"""Tests for CNP-5 cross-chain execution envelopes (src/sincor2/xchain_envelope.py).

Adversarial coverage: cross-chain replay, wrong-chainId, nonce reuse,
signature malleability, payload substitution, schema abuse.
"""

import copy

import pytest

from sincor2.xchain_envelope import (
    DEFAULT_VERIFYING_CONTRACT,
    SECP256K1_N,
    ChainIdMismatchError,
    EnvelopeError,
    EnvelopeValidator,
    ExpiredEnvelopeError,
    InvalidSignatureError,
    NonceReuseError,
    RelayerAdapter,
    XChainEnvelope,
    canonical_payload_bytes,
    payload_hash,
    sign_envelope,
    typed_data,
    validate_relayer_payload,
    verify_envelope,
)

try:
    from eth_account import Account

    HAVE_ETH_ACCOUNT = True
except Exception:  # pragma: no cover
    HAVE_ETH_ACCOUNT = False

pytestmark = pytest.mark.skipif(not HAVE_ETH_ACCOUNT, reason="eth-account required")

ETH = 1  # ethereum mainnet
BASE = 8453
SEPOLIA = 11155111
FAR_FUTURE = 4_000_000_000

# Deterministic test key (never used anywhere real).
_PRIVKEY = "0x" + "42" * 32
_OTHER_PRIVKEY = "0x" + "7a" * 32
SIGNER = Account.from_key(_PRIVKEY).address
OTHER = Account.from_key(_OTHER_PRIVKEY).address


def make_envelope(**overrides):
    params = {
        "payload": {"task": "settle", "auction_id": "0x" + "11" * 32, "amount": 150},
        "origin_chain_id": SEPOLIA,
        "dest_chain_id": BASE,
        "signer": SIGNER,
        "nonce": 1,
        "deadline": FAR_FUTURE,
    }
    params.update(overrides)
    return XChainEnvelope(**params)


def sign(env, key=_PRIVKEY, **kw):
    return sign_envelope(env, key, **kw)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_sign_verify_round_trip():
    env = make_envelope()
    sig = sign(env)
    recovered = verify_envelope(env, sig, local_chain_id=BASE)
    assert recovered.lower() == SIGNER.lower()


def test_validator_validate_consumes_nonce():
    env = make_envelope()
    sig = sign(env)
    v = EnvelopeValidator(BASE)
    recovered = v.validate(env, sig)
    assert recovered.lower() == SIGNER.lower()
    assert v.is_nonce_consumed(SIGNER, 1)
    assert v.consumed_count() == 1


def test_payload_canonicalization_key_order_independent():
    a = {"z": 1, "a": [1, 2], "m": {"y": 2, "x": 1}}
    b = {"m": {"x": 1, "y": 2}, "a": [1, 2], "z": 1}
    assert canonical_payload_bytes(a) == canonical_payload_bytes(b)
    assert payload_hash(a) == payload_hash(b)


def test_envelope_id_stable_and_unique():
    e1, e2 = make_envelope(), make_envelope()
    assert e1.envelope_id == e2.envelope_id
    e3 = make_envelope(nonce=2)
    assert e3.envelope_id != e1.envelope_id


def test_serialization_round_trip():
    env = make_envelope()
    restored = XChainEnvelope.from_dict(env.to_dict())
    assert restored == env
    assert restored.payload_digest == env.payload_digest


# ---------------------------------------------------------------------------
# Cross-chain replay / chainId confusion
# ---------------------------------------------------------------------------


def test_wrong_chain_id_rejected():
    """Envelope for Base verified on Ethereum is rejected (R-2)."""
    env = make_envelope()
    sig = sign(env)
    with pytest.raises(ChainIdMismatchError):
        verify_envelope(env, sig, local_chain_id=ETH)


def test_cross_chain_replay_rejected_by_validator():
    """An attacker replays a Base-bound envelope on a chain-1 validator."""
    env = make_envelope()
    sig = sign(env)
    v = EnvelopeValidator(ETH)
    with pytest.raises(ChainIdMismatchError):
        v.validate(env, sig)


def test_signature_does_not_verify_under_different_domain_chain():
    """Even raw recovery binds the destination chain: re-typed under another
    chainId, the signature no longer recovers to the signer."""
    env = make_envelope()
    sig = sign(env)
    # Simulate an attacker presenting the envelope as if bound for ETH.
    forged = XChainEnvelope(
        payload=env.payload,
        origin_chain_id=env.origin_chain_id,
        dest_chain_id=ETH,
        signer=env.signer,
        nonce=env.nonce,
        deadline=env.deadline,
    )
    with pytest.raises(InvalidSignatureError):
        verify_envelope(forged, sig, local_chain_id=ETH)


def test_domain_separator_differs_across_chains():
    e_base = make_envelope()
    e_eth = make_envelope(dest_chain_id=ETH)
    assert typed_data(e_base)["domain"]["chainId"] != typed_data(e_eth)["domain"]["chainId"]
    s_base = sign(e_base)
    with pytest.raises(InvalidSignatureError):
        verify_envelope(e_eth, s_base, local_chain_id=ETH)


def test_origin_chain_tamper_invalidates_signature():
    env = make_envelope()
    sig = sign(env)
    forged = XChainEnvelope(
        payload=env.payload,
        origin_chain_id=ETH,  # attacker re-stamps origin
        dest_chain_id=env.dest_chain_id,
        signer=env.signer,
        nonce=env.nonce,
        deadline=env.deadline,
    )
    with pytest.raises(InvalidSignatureError):
        verify_envelope(forged, sig, local_chain_id=BASE)


def test_validator_requires_explicit_local_chain():
    with pytest.raises(ChainIdMismatchError):
        EnvelopeValidator(0)
    with pytest.raises(ChainIdMismatchError):
        EnvelopeValidator(-1)


def test_zero_dest_chain_rejected_at_schema():
    with pytest.raises(EnvelopeError):
        make_envelope(dest_chain_id=0)


# ---------------------------------------------------------------------------
# Nonce replay
# ---------------------------------------------------------------------------


def test_nonce_reuse_rejected():
    env = make_envelope()
    sig = sign(env)
    v = EnvelopeValidator(BASE)
    v.validate(env, sig)
    with pytest.raises(NonceReuseError):
        v.validate(env, sig)


def test_mark_consumed_twice_rejected():
    env = make_envelope()
    v = EnvelopeValidator(BASE)
    v.mark_consumed(env)
    with pytest.raises(NonceReuseError):
        v.mark_consumed(env)


def test_same_nonce_different_signer_allowed():
    e1 = make_envelope()
    e2 = make_envelope(signer=OTHER)
    v = EnvelopeValidator(BASE)
    v.validate(e1, sign(e1))
    # OTHER's nonce-1 is a different ledger key.
    v.validate(e2, sign(e2, key=_OTHER_PRIVKEY))


def test_new_nonce_accepted_after_consumption():
    v = EnvelopeValidator(BASE)
    e1 = make_envelope(nonce=1)
    v.validate(e1, sign(e1))
    e2 = make_envelope(nonce=2)
    v.validate(e2, sign(e2))
    assert v.consumed_count() == 2


# ---------------------------------------------------------------------------
# Signature attacks
# ---------------------------------------------------------------------------


def test_tampered_payload_rejected():
    env = make_envelope()
    sig = sign(env)
    forged = XChainEnvelope(
        payload={"task": "settle", "auction_id": "0x" + "11" * 32, "amount": 999_999},
        origin_chain_id=env.origin_chain_id,
        dest_chain_id=env.dest_chain_id,
        signer=env.signer,
        nonce=env.nonce,
        deadline=env.deadline,
    )
    assert forged.payload_digest != env.payload_digest
    with pytest.raises(InvalidSignatureError):
        verify_envelope(forged, sig, local_chain_id=BASE)


def test_wrong_signer_address_rejected():
    env = make_envelope()
    sig = sign(env)
    forged = XChainEnvelope(
        payload=env.payload,
        origin_chain_id=env.origin_chain_id,
        dest_chain_id=env.dest_chain_id,
        signer=OTHER,  # attacker claims a different signer
        nonce=env.nonce,
        deadline=env.deadline,
    )
    with pytest.raises(InvalidSignatureError):
        verify_envelope(forged, sig, local_chain_id=BASE)


def test_signature_by_wrong_key_rejected():
    env = make_envelope()
    sig = sign(env, key=_OTHER_PRIVKEY)
    with pytest.raises(InvalidSignatureError):
        verify_envelope(env, sig, local_chain_id=BASE)


def test_malleated_high_s_signature_rejected():
    """The (r, n-s, v^1) sibling of a valid signature must never verify (R-4)."""
    env = make_envelope()
    sig = sign(env)
    raw = bytes.fromhex(sig[2:])
    r = int.from_bytes(raw[0:32], "big")
    s = int.from_bytes(raw[32:64], "big")
    v = raw[64]
    s_high = SECP256K1_N - s
    assert s_high > SECP256K1_N // 2  # ensure this is the malleated form
    v_flipped = 28 if v == 27 else 27
    malleated = "0x" + r.to_bytes(32, "big").hex() + s_high.to_bytes(32, "big").hex() + bytes([v_flipped]).hex()
    with pytest.raises(InvalidSignatureError):
        verify_envelope(env, malleated, local_chain_id=BASE)


def test_truncated_signature_rejected():
    env = make_envelope()
    sig = sign(env)
    with pytest.raises(InvalidSignatureError):
        verify_envelope(env, sig[:100], local_chain_id=BASE)


def test_garbage_signature_rejected():
    env = make_envelope()
    with pytest.raises(InvalidSignatureError):
        verify_envelope(env, "0x" + "zz" * 65, local_chain_id=BASE)
    with pytest.raises(InvalidSignatureError):
        verify_envelope(env, None, local_chain_id=BASE)
    with pytest.raises(InvalidSignatureError):
        verify_envelope(env, 12345, local_chain_id=BASE)


def test_sign_requires_private_key():
    env = make_envelope()
    with pytest.raises(EnvelopeError):
        sign_envelope(env, "")


def test_sign_accepts_key_without_0x_prefix():
    env = make_envelope()
    sig = sign_envelope(env, _PRIVKEY[2:])
    recovered = verify_envelope(env, sig, local_chain_id=BASE)
    assert recovered.lower() == SIGNER.lower()


def test_wrong_verifying_contract_rejected():
    env = make_envelope()
    sig = sign(env)
    other_vc = "0x1111111111111111111111111111111111111111"
    with pytest.raises(InvalidSignatureError):
        verify_envelope(env, sig, local_chain_id=BASE, verifying_contract=other_vc)


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


def test_expired_envelope_rejected():
    env = make_envelope(deadline=1_000)
    sig = sign(env)
    v = EnvelopeValidator(BASE, clock=lambda: 2_000.0)
    with pytest.raises(ExpiredEnvelopeError):
        v.validate(env, sig)
    # Nonce must NOT be consumed on a failed validation.
    assert not v.is_nonce_consumed(SIGNER, 1)


def test_deadline_in_future_accepted():
    v = EnvelopeValidator(BASE, clock=lambda: 1_000.0)
    env = make_envelope(deadline=2_000)
    v.validate(env, sign(env))


# ---------------------------------------------------------------------------
# Schema abuse
# ---------------------------------------------------------------------------


def test_missing_field_rejected():
    with pytest.raises(EnvelopeError):
        XChainEnvelope.from_dict({"payload": {}, "nonce": 1})


def test_non_dict_payload_rejected():
    with pytest.raises(EnvelopeError):
        make_envelope(payload=["not", "a", "dict"])


def test_non_json_payload_rejected():
    with pytest.raises(EnvelopeError):
        make_envelope(payload={"fn": object()})


def test_negative_nonce_rejected():
    with pytest.raises(EnvelopeError):
        make_envelope(nonce=-1)


def test_bad_signer_address_rejected():
    with pytest.raises(EnvelopeError):
        make_envelope(signer="not-an-address")
    with pytest.raises(EnvelopeError):
        make_envelope(signer="0x1234")


def test_payload_digest_mismatch_rejected():
    env = make_envelope()
    d = env.to_dict()
    d["payload_digest"] = "0x" + "ff" * 32
    with pytest.raises(EnvelopeError):
        XChainEnvelope.from_dict(d)


def test_validate_relayer_payload_schema_checks():
    env = make_envelope()
    sig = sign(env)
    e, s = validate_relayer_payload(env.to_dict(), sig)
    assert e == env and s == sig
    with pytest.raises(EnvelopeError):
        validate_relayer_payload("not-a-dict", sig)
    with pytest.raises(EnvelopeError):
        validate_relayer_payload(env.to_dict(), "")
    with pytest.raises(EnvelopeError):
        validate_relayer_payload(env.to_dict(), None)


def test_relayer_adapter_fails_loudly():
    r = RelayerAdapter()
    env = make_envelope()
    with pytest.raises(NotImplementedError):
        r.submit(env.to_dict(), "0x00")
    with pytest.raises(NotImplementedError):
        r.deliver(env.to_dict(), "0x00")
    with pytest.raises(NotImplementedError):
        r.status(env.envelope_id)
