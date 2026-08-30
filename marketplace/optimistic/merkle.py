"""Keccak-256 binary Merkle tree used for optimistic batch roots.

Odd nodes are promoted (hashed with themselves) so a unique root exists
for any non-empty leaf set. Empty set hashes to keccak(b'').
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

from marketplace.contract_net.keccak import keccak256, keccak256_hex


def leaf_hash(payload: bytes) -> bytes:
    return keccak256(payload)


def combine(left: bytes, right: bytes) -> bytes:
    if len(left) != 32 or len(right) != 32:
        raise ValueError("Merkle nodes must be 32 bytes")
    return keccak256(left + right)


def merkle_layers(leaves: Sequence[bytes]) -> List[List[bytes]]:
    if not leaves:
        return [[keccak256(b"")]]
    layer = [leaf if len(leaf) == 32 else keccak256(leaf) for leaf in leaves]
    layers = [layer]
    while len(layer) > 1:
        nxt: List[bytes] = []
        for i in range(0, len(layer), 2):
            left = layer[i]
            right = layer[i + 1] if i + 1 < len(layer) else layer[i]
            nxt.append(combine(left, right))
        layers.append(nxt)
        layer = nxt
    return layers


def merkle_root(leaves: Sequence[bytes]) -> bytes:
    return merkle_layers(leaves)[-1][0]


def merkle_root_hex(leaves: Sequence[bytes]) -> str:
    return "0x" + merkle_root(leaves).hex()


def merkle_proof(leaves: Sequence[bytes], index: int) -> List[Tuple[bytes, str]]:
    if index < 0 or index >= len(leaves):
        raise IndexError("leaf index out of range")
    layers = merkle_layers(leaves)
    proof: List[Tuple[bytes, str]] = []
    idx = index
    for layer in layers[:-1]:
        if idx % 2 == 0:
            sibling_i = idx + 1 if idx + 1 < len(layer) else idx
            side = "right"
        else:
            sibling_i = idx - 1
            side = "left"
        proof.append((layer[sibling_i], side))
        idx //= 2
    return proof


def verify_proof(leaf: bytes, proof: Sequence[Tuple[bytes, str]], root: bytes) -> bool:
    node = leaf if len(leaf) == 32 else keccak256(leaf)
    for sibling, side in proof:
        if side == "right":
            node = combine(node, sibling)
        elif side == "left":
            node = combine(sibling, node)
        else:
            raise ValueError(f"invalid proof side {side!r}")
    return node == root


def encode_event(
    *,
    agent_id: str,
    delta_axm: int,
    nonce: int,
    kind: str,
    ref: str,
) -> bytes:
    """Canonical leaf payload. Integers are 32-byte big-endian."""

    def u256(n: int) -> bytes:
        if n < 0:
            n = (1 << 256) + n
        return int(n).to_bytes(32, "big")

    return b"".join(
        [
            keccak256(agent_id.encode("utf-8")),
            u256(delta_axm),
            u256(nonce),
            keccak256(kind.encode("utf-8")),
            keccak256(ref.encode("utf-8")),
        ]
    )
