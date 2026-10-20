"""Signature canonicality guards (low-s enforcement + v normalization).

ECDSA signatures on secp256k1 are malleable: given a valid (r, s, v), the
twin (r, n-s, v^1) is also valid and recovers to the SAME address with
DIFFERENT bytes. Without low-s enforcement, an attacker can take any valid
signature, flip s to n-s, and mint a fresh signature that defeats
single-use / replay-cache protections (the replay digest changes while the
recovered signer stays the same).

The v byte is malleable in the same way: eth_account accepts v in
{0, 1, 27, 28} with 0==27 and 1==28 recovering to the identical signer.
Re-encoding the same (r, s) with the alternate v value changes the raw
65 bytes without changing the recovered signer, so replay digests keyed
on raw bytes must normalize v first.

This module provides the single shared guard. Every Python EIP-191 recovery
site must call ``require_low_s`` before ``Account.recover_message``.

Mirrors the on-chain StakeSlashManager canonical-signature check.
"""

from __future__ import annotations

# secp256k1 curve order (same constant as a2a_integration._SECP256K1_N).
SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
SECP256K1_HALF_N = SECP256K1_N // 2

_SIGNATURE_LEN = 65


def require_low_s(signature: bytes | str) -> bytes:
    """Validate that a 65-byte ECDSA signature is canonical.

    Parses hex strings (with/without ``0x`` prefix, surrounding whitespace
    stripped) or raw bytes. Raises ``ValueError("non-canonical high-s
    signature")`` when s = bytes[32:64] (big-endian) exceeds
    SECP256K1_HALF_N. Normalizes the v byte: 0 -> 27, 1 -> 28 (eth_account
    recovers both encodings to the identical signer, so only the byte
    representation changes); any other v value raises ``ValueError``.

    Returns the normalized 65 raw bytes on success: the same (r, s) with
    v canonicalized to 27 or 28. Callers that hash the return value for
    replay/single-use keys therefore see one canonical key per signing
    operation, regardless of the v encoding the caller supplied.

    Raises:
        ValueError: on malformed input (wrong length, non-hex, bad v)
            or high-s.
    """
    raw = _to_bytes(signature)
    if len(raw) != _SIGNATURE_LEN:
        raise ValueError(
            f"malformed signature: expected {_SIGNATURE_LEN} bytes, got {len(raw)}"
        )
    v = raw[64]
    if v == 0:
        v = 27
    elif v == 1:
        v = 28
    elif v not in (27, 28):
        raise ValueError(
            f"malformed signature: v must be 0, 1, 27, or 28, got {v}"
        )
    s = int.from_bytes(raw[32:64], "big")
    if s > SECP256K1_HALF_N:
        raise ValueError("non-canonical high-s signature")
    if s == 0:
        raise ValueError("malformed signature: s must be non-zero")
    return raw[:64] + bytes([v])


def _to_bytes(signature: bytes | str) -> bytes:
    """Normalize a signature to raw bytes."""
    if isinstance(signature, bytes):
        return signature
    if isinstance(signature, str):
        text = signature.strip()
        if text[:2].lower() == "0x":
            text = text[2:]
        try:
            return bytes.fromhex(text)
        except ValueError as exc:
            raise ValueError("malformed signature: not valid hex") from exc
    raise ValueError(
        f"malformed signature: expected bytes or hex str, got {type(signature).__name__}"
    )


def is_low_s(signature: bytes | str) -> bool:
    """Return True if the signature is canonical low-s (non-raising check)."""
    try:
        require_low_s(signature)
        return True
    except ValueError:
        return False
