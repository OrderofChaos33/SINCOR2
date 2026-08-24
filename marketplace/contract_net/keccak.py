"""Keccak-256 as used by Ethereum (not NIST SHA3-256).

Canonical fixture:

    keccak256(b\"\") ==
        c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470
"""

from __future__ import annotations

from typing import Iterable

_ROUNDS = 24
_RC = (
    0x0000000000000001,
    0x0000000000008082,
    0x800000000000808A,
    0x8000000080008000,
    0x000000000000808B,
    0x0000000080000001,
    0x8000000080008081,
    0x8000000000008009,
    0x000000000000008A,
    0x0000000000000088,
    0x0000000080008009,
    0x000000008000000A,
    0x000000008000808B,
    0x800000000000008B,
    0x8000000000008089,
    0x8000000000008003,
    0x8000000000008002,
    0x8000000000000080,
    0x000000000000800A,
    0x800000008000000A,
    0x8000000080008081,
    0x8000000000008080,
    0x0000000080000001,
    0x8000000080008008,
)
# rho offsets indexed [x][y]
_ROT = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)
_MASK = 0xFFFFFFFFFFFFFFFF
_RATE = 136  # 1088-bit rate for Keccak-256


def _rotl64(value: int, shift: int) -> int:
    shift &= 63
    return ((value << shift) | (value >> (64 - shift))) & _MASK


def _keccak_f(state: list[int]) -> None:
    for round_index in range(_ROUNDS):
        c = [state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rotl64(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                state[x + 5 * y] ^= d[x]

        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rotl64(state[x + 5 * y], _ROT[x][y])

        for y in range(5):
            lane = [b[x + 5 * y] for x in range(5)]
            for x in range(5):
                state[x + 5 * y] = (lane[x] ^ ((~lane[(x + 1) % 5]) & lane[(x + 2) % 5])) & _MASK

        state[0] ^= _RC[round_index]


def keccak256(data: bytes) -> bytes:
    """Return the 32-byte Keccak-256 digest of *data*."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("keccak256 expects bytes")
    state = [0] * 25
    offset = 0
    length = len(data)
    while length - offset >= _RATE:
        for i in range(_RATE):
            state[i >> 3] ^= data[offset + i] << ((i & 7) << 3)
        _keccak_f(state)
        offset += _RATE

    block = bytearray(data[offset:])
    block.append(0x01)
    block.extend(b"\x00" * (_RATE - len(block)))
    block[_RATE - 1] |= 0x80
    for i in range(_RATE):
        state[i >> 3] ^= block[i] << ((i & 7) << 3)
    _keccak_f(state)

    out = bytearray()
    for lane in state[:4]:
        out.extend(int(lane).to_bytes(8, "little"))
    return bytes(out)


def keccak256_hex(data: bytes) -> str:
    return "0x" + keccak256(data).hex()


def keccak256_many(chunks: Iterable[bytes]) -> bytes:
    return keccak256(b"".join(chunks))
