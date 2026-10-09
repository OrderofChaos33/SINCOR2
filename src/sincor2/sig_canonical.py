"""Signature canonicality guards (low-s enforcement).

ECDSA signatures on secp256k1 are malleable: given a valid (r, s, v), the
twin (r, n-s, v^1) is also valid and recovers to the SAME address with
DIFFERENT bytes. Without low-s enforcement, an attacker can take any valid
signature, flip s to n-s, and mint a fresh signature that defeats
single-use / replay-cache protections (the replay digest changes while the
recovered signer stays the same).

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
    """Validate that a 65-byte ECDSA signature has canonical low-s.

    Parses hex strings (with/without ``0x`` prefix, surrounding whitespace
    stripped) or raw bytes. Extracts s = bytes[32:64] as a big-endian int
    and raises ``ValueError("non-canonical high-s signature")`` when
    s > SECP256K1_HALF_N.

    Returns the normalized 65 raw bytes on success.

    Raises:
        ValueError: on malformed input (wrong length, non-hex) or high-s.
    """
    raw = _to_bytes(signature)
    if len(raw) != _SIGNATURE_LEN:
        raise ValueError(
            f"malformed signature: expected {_SIGNATURE_LEN} bytes, got {len(raw)}"
        )
    s = int.from_bytes(raw[32:64], "big")
    if s > SECP256K1_HALF_N:
        raise ValueError("non-canonical high-s signature")
    if s == 0:
        raise ValueError("malformed signature: s must be non-zero")
    return raw


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
