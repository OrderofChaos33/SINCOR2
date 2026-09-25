"""Keccak-256 as used by Ethereum (not NIST SHA3-256).

Canonical fixture:

    keccak256(b"") ==
        c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470

Backed by ``eth_hash`` (audited). This module previously carried a
hand-rolled Keccak-f[1600] implementation; it was removed after a
differential study (200 random keccak vectors + 500 full EIP-712 bid
digests, 2026-09-25) proved byte-equivalence with the audited backend.
See ``docs/ops/AUCTION_SECURITY_DECISIONS.md``.

The public API is unchanged so every import site keeps working.
"""

from __future__ import annotations

from typing import Iterable

try:
    from eth_hash.auto import keccak as _keccak
except ImportError as exc:  # pragma: no cover - backend declared in requirements.txt
    raise ImportError(
        "marketplace.contract_net.keccak requires a keccak backend for eth-hash "
        "(declared as 'eth-hash[pycryptodome]' in requirements.txt)."
    ) from exc


def keccak256(data: bytes) -> bytes:
    """Return the 32-byte Keccak-256 digest of *data*."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("keccak256 expects bytes")
    return _keccak(bytes(data))


def keccak256_hex(data: bytes) -> str:
    return "0x" + keccak256(data).hex()


def keccak256_many(chunks: Iterable[bytes]) -> bytes:
    return keccak256(b"".join(chunks))
