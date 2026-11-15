"""CNP-1 acceptance tests: SigilSeal + session-key delegation + seal enclave.

- seal create/verify round-trip; byte-identical digests vs eth_account;
- tampered seals rejected; expired seals rejected; cross-chain rejected;
- HMAC default-deny on the seal path (explicit opt-in only);
- delegation chain verifies end-to-end; expired/revoked session keys
  rejected; scope and down-scoping enforced;
- enclave refuses out-of-policy payloads and never exposes key material.
"""
from __future__ import annotations

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_hash.auto import keccak as eth_keccak

from marketplace.contract_net.eip712 import typed_data_digest, typed_data_payload
from marketplace.contract_net.seal_enclave import (
    EnclaveKeyError,
    EnclavePolicyError,
    MAX_DELEGATION_TTL,
    MAX_SEAL_TTL,
    SealEnclave,
)
from marketplace.contract_net.sigil_seal import (
    delegation_typed_data_digest,
    delegation_typed_data_payload,
    grants_cover_all,
    permission_covered,
    revocation_typed_data_digest,
    revocation_typed_data_payload,
    seal_typed_data_digest,
    seal_typed_data_payload,
    sign_seal,
    verify_seal,
    SealRegistry,
)
from marketplace.contract_net.types import BASE_SEPOLIA_CHAIN_ID, ContractNetConfig, SigType

CONFIG = ContractNetConfig(chain_id=BASE_SEPOLIA_CHAIN_ID)
OTHER_CHAIN = ContractNetConfig(chain_id=8453)

MASTER_PRIV = "0x" + "11" * 32
SESSION_PRIV = "0x" + "22" * 32
ATTACKER_PRIV = "0x" + "33" * 32
MASTER = Account.from_key(MASTER_PRIV)
SESSION = Account.from_key(SESSION_PRIV)
ATTACKER = Account.from_key(ATTACKER_PRIV)

NOW = 1_800_000_000  # fixed "now" for deterministic expiry checks
SEAL_EXPIRY = NOW + 10 * 24 * 3600
CERT_EXPIRY = NOW + 24 * 3600


def _seal_fields(**over):
    fields = {
        "agent_did": "did:sincor:e-vega-02",
        "agent_address": MASTER.address,
        "capabilities": ["auction:bid", "auction:reveal", "market:read"],
        "expiry": SEAL_EXPIRY,
        "nonce": 1,
    }
    fields.update(over)
    return fields


def _cert_fields(**over):
    fields = {
        "delegator": MASTER.address,
        "session_key": SESSION.address,
        "permissions": ["auction:bid", "auction:reveal"],
        "expiry": CERT_EXPIRY,
        "nonce": 7,
    }
    fields.update(over)
    return fields


def _raw_sign(digest: bytes, privkey: str) -> str:
    signed = Account.from_key(privkey).unsafe_sign_hash(digest)
    sig = signed.signature.hex()
    return sig if sig.startswith("0x") else "0x" + sig


def _signed_seal(fields=None):
    fields = fields or _seal_fields()
    sig, sig_type, digest = sign_seal(CONFIG, fields, private_key=MASTER_PRIV)
    assert sig_type == SigType.SECP256K1.value
    return fields, sig, sig_type


def _signed_cert(fields=None, privkey=MASTER_PRIV):
    fields = fields or _cert_fields()
    digest = delegation_typed_data_digest(CONFIG, **fields)
    return fields, _raw_sign(digest, privkey)


# ---------------------------------------------------------------------------
# SigilSeal
# ---------------------------------------------------------------------------

def test_seal_create_verify_roundtrip():
    fields, sig, sig_type = _signed_seal()
    ok, reason = verify_seal(
        CONFIG, fields, sig, sig_type=sig_type,
        expected_address=MASTER.address, now=NOW,
    )
    assert ok, reason


# ---------------------------------------------------------------------------
# W-174: high-S malleated twins are rejected (low-S parity with eip712)
# ---------------------------------------------------------------------------

SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


def _malleated_twin(sig: str) -> str:
    """The (r, n-s, v^1) twin of a 65-byte signature: same signer, high-s."""
    raw = bytes.fromhex(sig[2:] if sig.startswith("0x") else sig)
    r, s, v = raw[:32], int.from_bytes(raw[32:64], "big"), raw[64]
    twin_s = SECP256K1_N - s
    assert twin_s > SECP256K1_N // 2  # the twin really is the high-s form
    return (
        "0x" + r.hex() + twin_s.to_bytes(32, "big").hex()
        + bytes([27 + ((v - 27) ^ 1)]).hex()
    )


def test_low_s_seal_signature_verifies():
    fields, sig, sig_type = _signed_seal()
    raw = bytes.fromhex(sig[2:])
    assert int.from_bytes(raw[32:64], "big") <= SECP256K1_N // 2
    ok, reason = verify_seal(
        CONFIG, fields, sig, sig_type=sig_type,
        expected_address=MASTER.address, now=NOW,
    )
    assert ok, reason


def test_high_s_twin_seal_signature_rejected():
    fields, sig, sig_type = _signed_seal()
    twin = _malleated_twin(sig)
    assert twin != sig
    ok, reason = verify_seal(
        CONFIG, fields, twin, sig_type=sig_type,
        expected_address=MASTER.address, now=NOW,
    )
    assert not ok, reason


def test_high_s_twin_delegation_cert_rejected():
    reg = SealRegistry(CONFIG)
    seal_id = _register_seal(reg)
    cert_fields, cert_sig = _signed_cert()
    with pytest.raises(ValueError, match="signed by the delegator"):
        reg.register_delegation(
            cert_fields, _malleated_twin(cert_sig),
            sig_type=SigType.SECP256K1.value,
            seal_id=seal_id,
            now=NOW,
        )
    assert not reg.session_key_live(SESSION.address, now=NOW)


def test_seal_digest_byte_identical_to_eth_account():
    fields = _seal_fields()
    payload = seal_typed_data_payload(CONFIG, **fields)
    msg = encode_typed_data(full_message=payload)
    expected = eth_keccak(b"\x19\x01" + msg.header + msg.body)
    assert seal_typed_data_digest(CONFIG, **fields) == expected


def test_delegation_digest_byte_identical_to_eth_account():
    fields = _cert_fields()
    payload = delegation_typed_data_payload(CONFIG, **fields)
    msg = encode_typed_data(full_message=payload)
    expected = eth_keccak(b"\x19\x01" + msg.header + msg.body)
    assert delegation_typed_data_digest(CONFIG, **fields) == expected


def test_seal_tampered_capability_rejected():
    fields, sig, sig_type = _signed_seal()
    tampered = dict(fields, capabilities=["auction:bid", "auction:reveal", "seal:admin"])
    ok, _ = verify_seal(
        CONFIG, tampered, sig, sig_type=sig_type,
        expected_address=MASTER.address, now=NOW,
    )
    assert not ok


def test_seal_tampered_expiry_rejected():
    fields, sig, sig_type = _signed_seal()
    tampered = dict(fields, expiry=SEAL_EXPIRY + 3600)
    ok, _ = verify_seal(
        CONFIG, tampered, sig, sig_type=sig_type,
        expected_address=MASTER.address, now=NOW,
    )
    assert not ok


def test_seal_tampered_did_rejected():
    fields, sig, sig_type = _signed_seal()
    tampered = dict(fields, agent_did="did:sincor:e-vega-03")
    ok, _ = verify_seal(
        CONFIG, tampered, sig, sig_type=sig_type,
        expected_address=MASTER.address, now=NOW,
    )
    assert not ok


def test_seal_wrong_signer_rejected():
    fields, sig, sig_type = _signed_seal()
    ok, _ = verify_seal(
        CONFIG, fields, sig, sig_type=sig_type,
        expected_address=ATTACKER.address, now=NOW,
    )
    assert not ok


def test_seal_expired_rejected():
    fields, sig, sig_type = _signed_seal()
    ok, reason = verify_seal(
        CONFIG, fields, sig, sig_type=sig_type,
        expected_address=MASTER.address, now=SEAL_EXPIRY + 1,
    )
    assert not ok and "expired" in reason


def test_seal_cross_chain_rejected():
    fields, sig, sig_type = _signed_seal()
    ok, _ = verify_seal(
        OTHER_CHAIN, fields, sig, sig_type=sig_type,
        expected_address=MASTER.address, now=NOW,
    )
    assert not ok


def test_seal_hmac_default_deny():
    fields = _seal_fields()
    sig, sig_type, _digest = sign_seal(
        CONFIG, fields, signing_secret="demo-secret", allow_hmac=True
    )
    assert sig_type == SigType.HMAC.value
    ok, reason = verify_seal(
        CONFIG, fields, sig, sig_type=sig_type,
        expected_address=MASTER.address, now=NOW,
    )
    assert not ok and "default-deny" in reason


def test_seal_hmac_explicit_opt_in_verifies():
    fields = _seal_fields()
    secret = "demo-secret"
    sig, sig_type, _digest = sign_seal(
        CONFIG, fields, signing_secret=secret, allow_hmac=True
    )
    ok, reason = verify_seal(
        CONFIG, fields, sig, sig_type=sig_type,
        expected_address=MASTER.address, signing_secret=secret,
        allow_hmac=True, now=NOW,
    )
    assert ok, reason


def test_seal_typehash_distinct_from_delegation():
    # A SigilSeal signature must never verify as a DelegationCert and
    # vice versa: distinct primaryType -> distinct typehash -> distinct digest.
    seal_fields = _seal_fields()
    cert_fields = _cert_fields()
    assert seal_typed_data_digest(CONFIG, **seal_fields) != delegation_typed_data_digest(
        CONFIG, **cert_fields
    )


# ---------------------------------------------------------------------------
# Delegation chain
# ---------------------------------------------------------------------------

def _register_seal(reg, **over):
    fields, sig, sig_type = _signed_seal(_seal_fields(**over))
    return reg.register_seal(fields, sig, sig_type=sig_type, now=NOW)


def _registered_registry(*, cert_over=None, seal_capabilities=None):
    reg = SealRegistry(CONFIG)
    seal_over = {}
    if seal_capabilities is not None:
        seal_over["capabilities"] = seal_capabilities
    seal_id = _register_seal(reg, **seal_over)
    cert_fields, cert_sig = _signed_cert(_cert_fields(**(cert_over or {})))
    reg.register_delegation(
        cert_fields, cert_sig,
        sig_type=SigType.SECP256K1.value,
        seal_id=seal_id,
        now=NOW,
    )
    return reg, cert_fields


def _bid_payload(nonce=99):
    bid = dict(
        auction_id="0x" + "aa" * 32,
        task_id="task-cnp1",
        agent=SESSION.address,
        price=1_000_000,
        estimated_tokens=400,
        nonce=nonce,
        deadline=9_999_999_999,
    )
    return typed_data_payload(CONFIG, **bid), typed_data_digest(CONFIG, **bid)


def test_delegation_cert_registers():
    reg, cert_fields = _registered_registry()
    assert reg.session_key_live(SESSION.address, now=NOW)
    assert cert_fields["delegator"] == MASTER.address


def test_delegation_chain_verifies_session_signed_action():
    reg, _cert = _registered_registry()
    payload, digest = _bid_payload()
    action_sig = _raw_sign(digest, SESSION_PRIV)
    ok, reason = reg.verify_delegation_chain(
        SESSION.address, payload, action_sig, "auction:bid", now=NOW
    )
    assert ok, reason


def test_expired_session_key_rejected():
    reg, _cert = _registered_registry(cert_over={"expiry": NOW + 60})
    payload, digest = _bid_payload()
    action_sig = _raw_sign(digest, SESSION_PRIV)
    ok, reason = reg.verify_delegation_chain(
        SESSION.address, payload, action_sig, "auction:bid", now=NOW + 61
    )
    assert not ok and "expired" in reason
    assert not reg.session_key_live(SESSION.address, now=NOW + 61)


def test_revoked_session_key_rejected():
    reg, _cert = _registered_registry()
    revoked_at, nonce = NOW + 10, 50
    rev_digest = revocation_typed_data_digest(
        CONFIG, revoker=MASTER.address, target=SESSION.address,
        revoked_at=revoked_at, nonce=nonce,
    )
    reg.revoke_session_key(
        SESSION.address, _raw_sign(rev_digest, MASTER_PRIV),
        revoked_at=revoked_at, nonce=nonce,
    )
    payload, digest = _bid_payload()
    action_sig = _raw_sign(digest, SESSION_PRIV)
    ok, reason = reg.verify_delegation_chain(
        SESSION.address, payload, action_sig, "auction:bid", now=NOW + 20
    )
    assert not ok and "revoked" in reason


def test_revocation_must_come_from_delegator():
    reg, _cert = _registered_registry()
    revoked_at, nonce = NOW + 10, 51
    evil_digest = revocation_typed_data_digest(
        CONFIG, revoker=MASTER.address, target=SESSION.address,
        revoked_at=revoked_at, nonce=nonce,
    )
    with pytest.raises(ValueError, match="delegator"):
        reg.revoke_session_key(
            SESSION.address, _raw_sign(evil_digest, ATTACKER_PRIV),
            revoked_at=revoked_at, nonce=nonce,
        )
    # session key still live: the forged revocation did not land
    assert reg.session_key_live(SESSION.address, now=NOW + 20)


def test_seal_revocation_kills_seal():
    reg = SealRegistry(CONFIG)
    fields, sig, sig_type = _signed_seal()
    seal_id = reg.register_seal(fields, sig, sig_type=sig_type, now=NOW)
    assert reg.seal_valid(seal_id, now=NOW)
    revoked_at, nonce = NOW + 10, 52
    rev_digest = revocation_typed_data_digest(
        CONFIG, revoker=MASTER.address, target=seal_id,
        revoked_at=revoked_at, nonce=nonce,
    )
    reg.revoke_seal(
        seal_id, _raw_sign(rev_digest, MASTER_PRIV),
        revoked_at=revoked_at, nonce=nonce,
    )
    assert not reg.seal_valid(seal_id, now=NOW + 20)


def test_cert_tampered_permissions_rejected_at_registration():
    reg = SealRegistry(CONFIG)
    seal_id = _register_seal(reg)
    cert_fields, cert_sig = _signed_cert()
    tampered = dict(cert_fields, permissions=["seal:admin"])
    with pytest.raises(ValueError, match="signed by the delegator"):
        reg.register_delegation(
            tampered, cert_sig,
            sig_type=SigType.SECP256K1.value,
            seal_id=seal_id,
            now=NOW,
        )


def test_cert_signed_by_wrong_key_rejected():
    reg = SealRegistry(CONFIG)
    seal_id = _register_seal(reg)
    cert_fields, cert_sig = _signed_cert(privkey=ATTACKER_PRIV)  # claims MASTER delegator
    with pytest.raises(ValueError, match="signed by the delegator"):
        reg.register_delegation(
            cert_fields, cert_sig,
            sig_type=SigType.SECP256K1.value,
            seal_id=seal_id,
            now=NOW,
        )


def test_delegation_scope_enforced_on_action():
    reg, _cert = _registered_registry()
    payload, digest = _bid_payload()
    action_sig = _raw_sign(digest, SESSION_PRIV)
    ok, reason = reg.verify_delegation_chain(
        SESSION.address, payload, action_sig, "market:read", now=NOW
    )
    assert not ok and "scope" in reason


def test_delegation_downscoping_enforced():
    reg = SealRegistry(CONFIG)
    seal_id = _register_seal(reg, capabilities=["auction:bid"])
    cert_fields, cert_sig = _signed_cert()
    with pytest.raises(ValueError, match="down-scoping"):
        reg.register_delegation(
            cert_fields, cert_sig,
            sig_type=SigType.SECP256K1.value,
            seal_id=seal_id,  # the seal cannot grant reveal
            now=NOW,
        )


def test_w169_inflated_permissions_cannot_use_caller_supplied_grants():
    # PoC A: the master seal is capped at auction:bid. Neither a caller
    # supplied grants argument nor an inflated cert permission can expand it.
    reg = SealRegistry(CONFIG)
    seal_id = _register_seal(reg, capabilities=["auction:bid"])
    cert_fields, cert_sig = _signed_cert(_cert_fields(permissions=["seal:admin"]))
    with pytest.raises(TypeError, match="delegator_grants"):
        reg.register_delegation(
            cert_fields, cert_sig,
            sig_type=SigType.SECP256K1.value,
            delegator_grants=["seal:admin"],
            seal_id=seal_id,
            now=NOW,
        )
    with pytest.raises(ValueError, match="down-scoping"):
        reg.register_delegation(
            cert_fields, cert_sig,
            sig_type=SigType.SECP256K1.value,
            seal_id=seal_id,
            now=NOW,
        )
    assert not reg.session_key_live(SESSION.address, now=NOW)


def test_w169_revoked_delegator_seal_kills_delegation():
    # PoC B: revocation of the referenced seal cascades to its delegation.
    reg = SealRegistry(CONFIG)
    seal_id = _register_seal(reg)
    cert_fields, cert_sig = _signed_cert()
    reg.register_delegation(
        cert_fields, cert_sig,
        sig_type=SigType.SECP256K1.value,
        seal_id=seal_id,
        now=NOW,
    )
    payload, digest = _bid_payload()
    action_sig = _raw_sign(digest, SESSION_PRIV)
    assert reg.session_key_live(SESSION.address, now=NOW)
    ok, reason = reg.verify_delegation_chain(
        SESSION.address, payload, action_sig, "auction:bid", now=NOW
    )
    assert ok, reason

    revoked_at, nonce = NOW + 10, 52
    rev_digest = revocation_typed_data_digest(
        CONFIG, revoker=MASTER.address, target=seal_id,
        revoked_at=revoked_at, nonce=nonce,
    )
    reg.revoke_seal(
        seal_id, _raw_sign(rev_digest, MASTER_PRIV),
        revoked_at=revoked_at, nonce=nonce,
    )

    assert not reg.seal_valid(seal_id, now=NOW + 20)
    assert not reg.session_key_live(SESSION.address, now=NOW + 20)
    ok, reason = reg.verify_delegation_chain(
        SESSION.address, payload, action_sig, "auction:bid", now=NOW + 20
    )
    assert not ok and "delegator seal revoked" in reason


def test_w169_unrelated_seal_id_rejected():
    # PoC D: a cert signed by SESSION cannot borrow MASTER's seal, even
    # when the requested permission is within that seal's capabilities.
    reg = SealRegistry(CONFIG)
    victim_seal_id = _register_seal(reg, expiry=NOW + 30 * 24 * 3600)
    cert_fields, cert_sig = _signed_cert(
        _cert_fields(
            delegator=SESSION.address,
            session_key=ATTACKER.address,
            permissions=["auction:bid"],
            expiry=NOW + 20 * 24 * 3600,
            nonce=9,
        ),
        privkey=SESSION_PRIV,
    )
    with pytest.raises(ValueError, match="does not match delegator seal"):
        reg.register_delegation(
            cert_fields, cert_sig,
            sig_type=SigType.SECP256K1.value,
            seal_id=victim_seal_id,
            now=NOW,
        )
    assert not reg.session_key_live(ATTACKER.address, now=NOW)


def test_w169_registration_requires_existing_live_seal():
    cert_fields, cert_sig = _signed_cert()

    unknown_reg = SealRegistry(CONFIG)
    with pytest.raises(ValueError, match="unknown delegator seal_id"):
        unknown_reg.register_delegation(
            cert_fields, cert_sig,
            sig_type=SigType.SECP256K1.value,
            seal_id="0x" + "00" * 32,
            now=NOW,
        )

    revoked_reg = SealRegistry(CONFIG)
    revoked_seal_id = _register_seal(revoked_reg)
    revoked_at, nonce = NOW + 10, 52
    rev_digest = revocation_typed_data_digest(
        CONFIG, revoker=MASTER.address, target=revoked_seal_id,
        revoked_at=revoked_at, nonce=nonce,
    )
    revoked_reg.revoke_seal(
        revoked_seal_id, _raw_sign(rev_digest, MASTER_PRIV),
        revoked_at=revoked_at, nonce=nonce,
    )
    with pytest.raises(ValueError, match="delegator seal revoked"):
        revoked_reg.register_delegation(
            cert_fields, cert_sig,
            sig_type=SigType.SECP256K1.value,
            seal_id=revoked_seal_id,
            now=NOW + 20,
        )

    expired_reg = SealRegistry(CONFIG)
    expired_seal_id = _register_seal(expired_reg, expiry=NOW + 60)
    long_cert, long_cert_sig = _signed_cert(_cert_fields(expiry=NOW + 120))
    with pytest.raises(ValueError, match="delegator seal expired"):
        expired_reg.register_delegation(
            long_cert, long_cert_sig,
            sig_type=SigType.SECP256K1.value,
            seal_id=expired_seal_id,
            now=NOW + 61,
        )


def test_delegation_expiry_cannot_exceed_master_seal():
    reg = SealRegistry(CONFIG)
    fields, sig, sig_type = _signed_seal()
    seal_id = reg.register_seal(fields, sig, sig_type=sig_type, now=NOW)
    cert_fields, cert_sig = _signed_cert(
        _cert_fields(expiry=SEAL_EXPIRY + 3600)  # past the master seal
    )
    with pytest.raises(ValueError, match="exceeds delegator seal expiry"):
        reg.register_delegation(
            cert_fields, cert_sig,
            sig_type=SigType.SECP256K1.value,
            seal_id=seal_id,
            now=NOW,
        )


def test_delegation_requires_secp256k1_not_hmac():
    reg = SealRegistry(CONFIG)
    seal_id = _register_seal(reg)
    cert_fields, _cert_sig = _signed_cert()
    with pytest.raises(ValueError, match="secp256k1"):
        reg.register_delegation(
            cert_fields, "0x" + "00" * 32,
            sig_type=SigType.HMAC.value,
            seal_id=seal_id,
            now=NOW,
        )


def test_action_signed_by_wrong_session_key_rejected():
    reg, _cert = _registered_registry()
    payload, digest = _bid_payload()
    attacker_sig = _raw_sign(digest, ATTACKER_PRIV)
    ok, reason = reg.verify_delegation_chain(
        SESSION.address, payload, attacker_sig, "auction:bid", now=NOW
    )
    assert not ok and "session key" in reason


def test_permission_scope_boundary():
    assert permission_covered(["auction"], "auction:bid")
    assert permission_covered(["auction:bid"], "auction:bid")
    assert not permission_covered(["auction:bi"], "auction:bid")
    assert not permission_covered(["auctionx"], "auction:bid")
    assert not permission_covered(["auction"], "market:read")
    assert grants_cover_all(["auction", "market:read"], ["auction:bid", "market:read"])
    assert not grants_cover_all(["auction"], ["auction:bid", "market:read"])


# ---------------------------------------------------------------------------
# Enclave
# ---------------------------------------------------------------------------

def _enclave():
    return SealEnclave(CONFIG)


def test_enclave_sign_seal_roundtrip():
    enc = _enclave()
    key_id = enc.import_key(MASTER_PRIV)
    assert enc.key_address(key_id) == MASTER.address
    assert key_id != MASTER_PRIV  # opaque handle, not the key
    sealed = enc.sign_seal(key_id, _seal_fields(), now=NOW)
    assert sealed["sig_type"] == SigType.SECP256K1.value
    assert sealed["signer"] == MASTER.address
    ok, reason = verify_seal(
        CONFIG,
        {k: sealed[k] for k in ("agent_did", "agent_address", "capabilities", "expiry", "nonce")},
        sealed["signature"],
        sig_type=sealed["sig_type"],
        expected_address=MASTER.address,
        now=NOW,
    )
    assert ok, reason


def test_enclave_sign_delegation_roundtrip():
    enc = _enclave()
    key_id = enc.import_key(MASTER_PRIV)
    issued = enc.sign_delegation(
        key_id,
        {"session_key": SESSION.address, "permissions": ["auction:bid"],
         "expiry": NOW + 3600, "nonce": 11},
        now=NOW,
    )
    assert issued["delegator"] == MASTER.address
    assert issued["session_key"] == SESSION.address
    # the issued cert verifies through the registry chain
    reg = SealRegistry(CONFIG)
    seal_id = _register_seal(reg, capabilities=["auction:bid"])
    reg.register_delegation(
        {k: issued[k] for k in ("delegator", "session_key", "permissions", "expiry", "nonce")},
        issued["signature"],
        sig_type=SigType.SECP256K1.value,
        seal_id=seal_id,
        now=NOW,
    )
    assert reg.session_key_live(SESSION.address, now=NOW)


def test_enclave_refuses_over_ttl_expiry():
    enc = _enclave()
    key_id = enc.import_key(MASTER_PRIV)
    with pytest.raises(EnclavePolicyError, match="maximum TTL"):
        enc.sign_seal(key_id, _seal_fields(expiry=NOW + MAX_SEAL_TTL + 1), now=NOW)
    with pytest.raises(EnclavePolicyError, match="maximum TTL"):
        enc.sign_delegation(
            key_id,
            {"session_key": SESSION.address, "permissions": ["auction:bid"],
             "expiry": NOW + MAX_DELEGATION_TTL + 1, "nonce": 12},
            now=NOW,
        )


def test_enclave_refuses_past_expiry():
    enc = _enclave()
    key_id = enc.import_key(MASTER_PRIV)
    with pytest.raises(EnclavePolicyError, match="future"):
        enc.sign_seal(key_id, _seal_fields(expiry=NOW - 1), now=NOW)


def test_enclave_refuses_unknown_capability():
    enc = _enclave()
    key_id = enc.import_key(MASTER_PRIV)
    with pytest.raises(EnclavePolicyError, match="allowed namespaces"):
        enc.sign_seal(
            key_id, _seal_fields(capabilities=["pwn:everything"]), now=NOW
        )


def test_enclave_refuses_nonce_reuse():
    enc = _enclave()
    key_id = enc.import_key(MASTER_PRIV)
    enc.sign_seal(key_id, _seal_fields(nonce=21), now=NOW)
    with pytest.raises(EnclavePolicyError, match="already used"):
        enc.sign_seal(key_id, _seal_fields(nonce=21), now=NOW)


def test_enclave_refuses_unknown_key():
    enc = _enclave()
    with pytest.raises(EnclaveKeyError):
        enc.sign_seal("key-deadbeef", _seal_fields(), now=NOW)


def test_enclave_refuses_bad_key_import():
    enc = _enclave()
    with pytest.raises(EnclaveKeyError):
        enc.import_key("not-a-key")


def test_enclave_refuses_hmac_algorithm():
    enc = _enclave()
    key_id = enc.import_key(MASTER_PRIV)
    with pytest.raises(EnclavePolicyError, match="secp256k1 only"):
        enc.sign_with_algorithm(key_id, "hmac", _seal_fields())


def test_enclave_refuses_self_delegation():
    enc = _enclave()
    key_id = enc.import_key(MASTER_PRIV)
    with pytest.raises(EnclavePolicyError, match="must differ from the delegator"):
        enc.sign_delegation(
            key_id,
            {"session_key": MASTER.address, "permissions": ["auction:bid"],
             "expiry": NOW + 3600, "nonce": 13},
            now=NOW,
        )


def test_enclave_rejects_extra_fields():
    enc = _enclave()
    key_id = enc.import_key(MASTER_PRIV)
    with pytest.raises(EnclavePolicyError, match="unexpected fields"):
        enc.sign_seal(key_id, dict(_seal_fields(), money_path_amount=100), now=NOW)


def test_enclave_rejects_bad_did_and_address():
    enc = _enclave()
    key_id = enc.import_key(MASTER_PRIV)
    with pytest.raises(EnclavePolicyError):
        enc.sign_seal(key_id, _seal_fields(agent_did="not-a-did"), now=NOW)
    with pytest.raises(EnclavePolicyError):
        enc.sign_seal(key_id, _seal_fields(agent_address="0x1234"), now=NOW)


def test_enclave_never_exposes_key_material():
    enc = _enclave()
    key_id = enc.import_key(MASTER_PRIV)
    # no attribute or method leaks the raw private key
    blob = repr(enc) + repr(enc.__dict__)
    assert MASTER_PRIV.lower().replace("0x", "") not in blob.lower()
    assert not any(
        name.startswith(("export", "private", "secret", "raw"))
        for name in dir(enc) if not name.startswith("_")
    )
    # destroy invalidates the handle completely
    assert enc.has_key(key_id)
    enc.destroy(key_id)
    assert not enc.has_key(key_id)
    with pytest.raises(EnclaveKeyError):
        enc.sign_seal(key_id, _seal_fields(), now=NOW)


# ---------------------------------------------------------------------------
# W-175: the enclave issues seals only for its own key's identity
# ---------------------------------------------------------------------------

def test_enclave_refuses_seal_for_foreign_agent_address():
    # sign_seal used to validate only the address FORMAT: the enclave
    # would mint a seal naming a victim, signed with the attacker's key.
    # (verify_seal rejected it downstream, but the enclave is the policy
    # boundary — it must never issue the impersonation seal at all.)
    enc = _enclave()
    key_id = enc.import_key(ATTACKER_PRIV)
    with pytest.raises(EnclavePolicyError, match="own address"):
        enc.sign_seal(
            key_id, _seal_fields(agent_address=MASTER.address), now=NOW
        )


def test_enclave_foreign_address_refusal_burns_no_nonce():
    enc = _enclave()
    key_id = enc.import_key(ATTACKER_PRIV)
    with pytest.raises(EnclavePolicyError):
        enc.sign_seal(
            key_id,
            _seal_fields(agent_address=MASTER.address, nonce=9),
            now=NOW,
        )
    # The refusal happened before signing: nonce 9 is still usable for
    # a legitimate seal under the key's own address.
    sealed = enc.sign_seal(
        key_id, _seal_fields(agent_address=ATTACKER.address, nonce=9), now=NOW
    )
    assert sealed["signer"] == ATTACKER.address
    assert sealed["agent_address"] == ATTACKER.address


def test_enclave_sign_seal_own_address_any_case():
    enc = _enclave()
    key_id = enc.import_key(MASTER_PRIV)
    sealed = enc.sign_seal(
        key_id, _seal_fields(agent_address=MASTER.address.lower()), now=NOW
    )
    ok, reason = verify_seal(
        CONFIG,
        {k: sealed[k] for k in ("agent_did", "agent_address", "capabilities", "expiry", "nonce")},
        sealed["signature"],
        sig_type=sealed["sig_type"],
        expected_address=MASTER.address,
        now=NOW,
    )
    assert ok, reason
