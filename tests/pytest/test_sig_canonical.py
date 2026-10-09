"""Low-s signature canonicality tests.

Proves the malleability is real (twin recovers to same address) and that
all five Python EIP-191 recovery sites reject the malleated twin.
"""

import pytest

from sincor2.sig_canonical import (
    SECP256K1_HALF_N,
    SECP256K1_N,
    is_low_s,
    require_low_s,
)


@pytest.fixture(scope="module")
def sig_pair():
    """Generate a real signature and its high-s malleated twin."""
    from eth_account import Account
    from eth_account.messages import encode_defunct

    acct = Account.create()
    message = "sincor low-s test message"
    signed = acct.sign_message(encode_defunct(text=message))
    raw = bytes(signed.signature)
    assert len(raw) == 65

    r = raw[:32]
    s = int.from_bytes(raw[32:64], "big")
    v = raw[64]

    # Malleate: s -> n - s, flip v (27 <-> 28)
    s_twin = SECP256K1_N - s
    v_twin = 28 if v == 27 else 27
    twin = r + s_twin.to_bytes(32, "big") + bytes([v_twin])

    return {
        "address": acct.address,
        "message": message,
        "original": raw,
        "twin": twin,
        "s_original": s,
        "s_twin": s_twin,
    }


def test_malleability_is_real(sig_pair):
    """Both twins recover to the SAME address (proving the attack)."""
    from eth_account import Account
    from eth_account.messages import encode_defunct

    msg = encode_defunct(text=sig_pair["message"])
    addr_orig = Account.recover_message(msg, signature=sig_pair["original"])
    addr_twin = Account.recover_message(msg, signature=sig_pair["twin"])
    assert addr_orig == addr_twin == sig_pair["address"]
    assert sig_pair["original"] != sig_pair["twin"]


def test_original_is_low_s(sig_pair):
    # eth_account signs canonical low-s by default
    assert sig_pair["s_original"] <= SECP256K1_HALF_N
    require_low_s(sig_pair["original"])


def test_twin_is_high_s_rejected(sig_pair):
    assert sig_pair["s_twin"] > SECP256K1_HALF_N
    with pytest.raises(ValueError, match="non-canonical high-s"):
        require_low_s(sig_pair["twin"])


def test_require_low_s_accepts_hex_forms(sig_pair):
    hex_sig = "0x" + sig_pair["original"].hex()
    assert require_low_s(hex_sig) == sig_pair["original"]
    assert require_low_s("  " + hex_sig.upper() + "  ") == sig_pair["original"]
    assert require_low_s(sig_pair["original"].hex()) == sig_pair["original"]


def test_require_low_s_rejects_malformed():
    with pytest.raises(ValueError, match="malformed"):
        require_low_s(b"\x01\x02")  # too short
    with pytest.raises(ValueError, match="malformed"):
        require_low_s("0xzzzz")  # not hex
    with pytest.raises(ValueError, match="malformed"):
        require_low_s(12345)  # wrong type
    # s == 0 is invalid
    bad = b"\x11" * 32 + b"\x00" * 32 + b"\x1b"
    with pytest.raises(ValueError, match="malformed"):
        require_low_s(bad)


def test_is_low_s_non_raising(sig_pair):
    assert is_low_s(sig_pair["original"]) is True
    assert is_low_s(sig_pair["twin"]) is False
    assert is_low_s(b"short") is False


def _sign_with(acct_addr_holder, message):
    """Helper: sign a message with a fresh account, return (addr, sig)."""
    from eth_account import Account
    from eth_account.messages import encode_defunct

    acct = Account.create()
    sig = acct.sign_message(encode_defunct(text=message)).signature
    return acct.address, bytes(sig)


def _malleate(raw: bytes) -> bytes:
    r = raw[:32]
    s = int.from_bytes(raw[32:64], "big")
    v = raw[64]
    return r + (SECP256K1_N - s).to_bytes(32, "big") + bytes([28 if v == 27 else 27])


# --- Integration: each of the five call sites rejects the twin ---

def test_site1_dispute_signer_rejects_twin():
    from sincor2.a2a_inbound_market import _recover_dispute_signer

    addr, sig = _sign_with(None, "dispute-auth-test")
    assert _recover_dispute_signer("dispute-auth-test", sig.hex()) == addr
    with pytest.raises(ValueError, match="bad signature"):
        _recover_dispute_signer("dispute-auth-test", _malleate(sig).hex())


def test_site2_settlement_ruling_rejects_twin():
    from sincor2.settlement_proofs import recover_settle_ruling_signer

    addr, sig = _sign_with(None, "settle-ruling-test")
    assert recover_settle_ruling_signer("settle-ruling-test", sig.hex()) == addr
    with pytest.raises(ValueError, match="bad signature"):
        recover_settle_ruling_signer("settle-ruling-test", _malleate(sig).hex())


def test_site3_kya_registry_rejects_twin():
    from sincor2.kya_registry import _recover_eip191

    addr, sig = _sign_with(None, "kya-identity-test")
    assert _recover_eip191("kya-identity-test", sig.hex()) == addr
    with pytest.raises(ValueError, match="bad signature"):
        _recover_eip191("kya-identity-test", _malleate(sig).hex())


def test_site4_identity_signer_rejects_twin():
    from sincor2.a2a_integration import _recover_identity_signer

    addr, sig = _sign_with(None, "identity-test")
    assert _recover_identity_signer("identity-test", sig.hex()) == addr
    with pytest.raises(ValueError, match="bad signature"):
        _recover_identity_signer("identity-test", _malleate(sig).hex())


def test_site5_quota_signer_rejects_twin():
    from sincor2.a2a_integration import _recover_quota_signer

    addr, sig = _sign_with(None, "quota-test")
    assert _recover_quota_signer("quota-test", sig.hex()) == addr
    with pytest.raises(ValueError, match="bad signature"):
        _recover_quota_signer("quota-test", _malleate(sig).hex())
