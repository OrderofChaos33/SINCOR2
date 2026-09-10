"""OpenZeppelin-compatible keccak256 sorted-pair Merkle tree.

Leaf encoding (locked): keccak256(address20) where address20 is the 20-byte
checksum-stripped lowercase address. Pairing: keccak256(min||max) of two
32-byte nodes. Empty tree root is keccak256(b"").

This is the on-chain verification the airdrop quest uses. Do not fall back to
an unsorted wallet set in production.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional, Sequence

ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
EMPTY_KECCAK = bytes.fromhex("c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470")


def _keccak(data: bytes) -> bytes:
    try:
        from eth_hash.auto import keccak

        return keccak(data)
    except Exception:
        try:
            from sha3 import keccak_256  # type: ignore

            return keccak_256(data).digest()
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("keccak256 requires eth-hash or pysha3") from exc


def is_address(value: str) -> bool:
    return bool(value) and bool(ADDRESS_RE.match(value.strip()))


def normalize_address(value: str) -> str:
    return value.strip().lower()


def leaf_of(address: str) -> bytes:
    addr = normalize_address(address)
    if not is_address(addr):
        raise ValueError("bad address")
    return _keccak(bytes.fromhex(addr[2:]))


def _hash_pair(a: bytes, b: bytes) -> bytes:
    left, right = (a, b) if a <= b else (b, a)
    return _keccak(left + right)


def unique_sorted(addresses: Iterable[str]) -> List[str]:
    seen = {normalize_address(a) for a in addresses if is_address(a)}
    return sorted(seen)


class MerkleTree:
    def __init__(self, addresses: Sequence[str]) -> None:
        self.addresses = unique_sorted(addresses)
        self.leaves = [leaf_of(a) for a in self.addresses]
        self.root = self._fold(self.leaves) if self.leaves else EMPTY_KECCAK

    @staticmethod
    def _fold(layer: Sequence[bytes]) -> bytes:
        nodes = list(layer)
        while len(nodes) > 1:
            nxt: List[bytes] = []
            for i in range(0, len(nodes), 2):
                if i + 1 >= len(nodes):
                    nxt.append(nodes[i])
                else:
                    nxt.append(_hash_pair(nodes[i], nodes[i + 1]))
            nodes = nxt
        return nodes[0]

    def proof(self, address: str) -> Optional[List[str]]:
        addr = normalize_address(address)
        try:
            index = self.addresses.index(addr)
        except ValueError:
            return None
        layer = list(self.leaves)
        out: List[str] = []
        while len(layer) > 1:
            sibling = index ^ 1
            if sibling < len(layer):
                out.append("0x" + layer[sibling].hex())
            nxt: List[bytes] = []
            for i in range(0, len(layer), 2):
                if i + 1 >= len(layer):
                    nxt.append(layer[i])
                else:
                    nxt.append(_hash_pair(layer[i], layer[i + 1]))
            layer = nxt
            index //= 2
        return out

    def hex_root(self) -> str:
        return "0x" + self.root.hex()

    def manifest(self) -> dict:
        return {
            "encoding": "keccak256(address20)",
            "pair": "sorted-keccak",
            "root": self.hex_root(),
            "count": len(self.addresses),
            "chain_id": 8453,
        }


def verify_proof(address: str, proof: Sequence[str], root: str) -> bool:
    try:
        node = leaf_of(address)
        for item in proof:
            sib = bytes.fromhex(item[2:] if item.startswith("0x") else item)
            node = _hash_pair(node, sib)
        want = root[2:] if root.startswith("0x") else root
        return node.hex() == want.lower()
    except Exception:
        return False


def parse_addresses(raw: str) -> List[str]:
    return unique_sorted(re.findall(r"0x[0-9a-fA-F]{40}", raw or ""))
