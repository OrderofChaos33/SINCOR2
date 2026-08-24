"""Hash-committed bids: on-chain publishes keccak(price || salt || agent).

The price stays hidden during the commit window so MEV / front-runners
cannot snipe public task auctions. Reveal is off-chain (or later on-chain)
and must match the commitment exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from marketplace.contract_net.keccak import keccak256, keccak256_hex


def _u256(n: int) -> bytes:
    if n < 0:
        raise ValueError("price must be non-negative")
    return int(n).to_bytes(32, "big")


def bid_commitment(price: int, salt: bytes, agent_id: str) -> bytes:
    if len(salt) != 32:
        raise ValueError("salt must be 32 bytes")
    return keccak256(_u256(price) + salt + keccak256(agent_id.encode("utf-8")))


def bid_commitment_hex(price: int, salt: bytes, agent_id: str) -> str:
    return "0x" + bid_commitment(price, salt, agent_id).hex()


@dataclass
class SealedCommit:
    auction_id: str
    agent_id: str
    commit: str
    submitted_at: float
    revealed: bool = False
    price: Optional[int] = None
    valid_reveal: bool = False
    reject_reason: str = ""


def verify_reveal(commit_hex: str, price: int, salt: bytes, agent_id: str) -> bool:
    expected = bid_commitment_hex(price, salt, agent_id)
    return expected.lower() == commit_hex.lower()
