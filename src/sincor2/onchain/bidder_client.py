"""Reference bidder client for the onchain sealed-bid auction.

``CommitRevealAuction.commit()`` / ``reveal()`` bind bids to ``msg.sender``,
so the platform cannot relay a bidder's transaction: agents on the trustless
path submit their own transactions from their own wallets.  This module is
the copy-pasteable reference implementation of that flow — pure functions
plus a thin ``BidderClient`` wrapper, no platform imports.

Commitment scheme (mirrors the contract EXACTLY):
    commitHash = keccak256(abi.encodePacked(bytes32(price), salt,
                                           agentIdHash))
where ``price`` is the bid in wei as a 32-byte big-endian integer, ``salt``
is a 32-byte bidder-chosen nonce, and
``agentIdHash = keccak256(utf8(agent_id))`` links the onchain bid to the
bidder's registered platform identity.  Prices are bounded to uint96
(the contract reverts ``PriceTooLarge`` above it).

Currency note: the onchain auction settles in native ETH.  Prices here are
wei of the chain's native currency, not AXM.
"""

from __future__ import annotations

import secrets
from typing import Any, Dict, Tuple

UINT96_MAX = 2**96 - 1


def _keccak(data: bytes) -> bytes:
    try:
        from eth_hash.auto import keccak
    except Exception:  # pragma: no cover
        from sha3 import keccak_256 as _k  # type: ignore

        def keccak(data: bytes) -> bytes:  # type: ignore[no-redef]
            return _k(data).digest()
    return keccak(data)


def agent_id_hash(agent_id: str) -> bytes:
    """keccak256 of the bidder's registered platform agent id (utf-8)."""
    return _keccak(str(agent_id).encode("utf-8"))


def random_salt() -> bytes:
    """Fresh 32-byte commit salt from a cryptographic RNG."""
    return secrets.token_bytes(32)


def commitment(price_wei: int, salt: bytes, agent_id: str) -> bytes:
    """Compute the commit hash for a bid.

    Mirrors ``CommitRevealAuction.reveal`` byte-for-byte:
    ``keccak256(abi.encodePacked(bytes32(price), salt, agentIdHash))``.
    Raises ``ValueError`` on out-of-domain inputs instead of letting the
    contract revert after gas is spent.
    """
    price_wei = int(price_wei)
    if price_wei < 0:
        raise ValueError("price_wei must be non-negative")
    if price_wei > UINT96_MAX:
        raise ValueError(
            f"price_wei {price_wei} exceeds uint96.max "
            "(contract reverts PriceTooLarge)")
    salt = bytes(salt)
    if len(salt) != 32:
        raise ValueError("salt must be exactly 32 bytes")
    return _keccak(
        price_wei.to_bytes(32, "big") + salt + agent_id_hash(agent_id))


def auction_id_for(task_id: str) -> bytes:
    """Deterministic bytes32 auction id — same derivation as the platform
    relayer (``keccak256(b"SINCOR_SEALED_AUCTION:" + task_id)``)."""
    from sincor2.onchain.auction_relayer import auction_id_for as _relayer_id
    return _relayer_id(task_id)


class BidderClient:
    """Sign-and-send wrapper for one bidder wallet.

    ``w3`` is any connected ``web3.Web3``; ``private_key`` never leaves
    this object (it is only passed to ``eth_account`` at sign time).
    """

    def __init__(self, w3: Any, contract_address: str, private_key: str):
        from eth_account import Account
        from sincor2.onchain.auction_client import _load_abi

        self.w3 = w3
        self.address = Account.from_key(private_key).address
        self._key = private_key
        self.contract = w3.eth.contract(
            address=contract_address, abi=_load_abi("CommitRevealAuction"))

    def _send(self, fn: Any, **tx_kw: Any) -> Dict[str, Any]:
        from eth_account import Account

        tx = fn.build_transaction({
            "from": self.address,
            "nonce": self.w3.eth.get_transaction_count(self.address,
                                                      "pending"),
            "gasPrice": self.w3.eth.gas_price,
            "chainId": self.w3.eth.chain_id,
            **tx_kw,
        })
        signed = Account.sign_transaction(tx, self._key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
        return {"tx_hash": tx_hash.hex(), "status": receipt.status}

    def commit(self, auction_id: bytes, price_wei: int, salt: bytes,
               agent_id: str) -> Dict[str, Any]:
        """Submit a sealed commitment. Returns the values to keep secret
        until the reveal window (price, salt) plus the tx receipt."""
        commit_hash = commitment(price_wei, salt, agent_id)
        result = self._send(
            self.contract.functions.commit(auction_id, commit_hash))
        result.update({"auction_id": "0x" + bytes(auction_id).hex(),
                       "commitment": "0x" + commit_hash.hex()})
        return result

    def reveal(self, auction_id: bytes, price_wei: int, salt: bytes,
               agent_id: str) -> Dict[str, Any]:
        """Reveal a prior commitment. Must come from the same wallet that
        committed, inside the reveal window."""
        result = self._send(self.contract.functions.reveal(
            auction_id, int(price_wei), salt, agent_id_hash(agent_id)))
        result.update({"auction_id": "0x" + bytes(auction_id).hex(),
                       "price_wei": str(int(price_wei))})
        return result

    def vickrey_result(self, auction_id: bytes) -> Tuple[str, int]:
        """Read the Vickrey outcome: (winner_address, price_wei)."""
        winner, price = self.contract.functions.vickreyResult(
            auction_id).call()
        return winner, int(price)

    def auction_state(self, auction_id: bytes) -> Dict[str, Any]:
        """Read an auction's onchain timing/state (for deadline checks)."""
        (opened, finalized, poster, commit_deadline,
         reveal_deadline) = self.contract.functions.auctions(
            auction_id).call()
        return {"opened": opened, "finalized": finalized, "poster": poster,
                "commit_deadline": int(commit_deadline),
                "reveal_deadline": int(reveal_deadline)}
