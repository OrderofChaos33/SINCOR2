"""Platform relayer for the onchain sealed-bid auction contracts.

The contracts (``CommitRevealAuction`` / ``ExecutionEscrowManager``) are the
trustless path: commits and reveals are keyed by ``msg.sender``, so bidders
who want trustlessness submit their own onchain transactions from their own
wallets.  The platform acts as *poster*: it opens the onchain auction for
every sealed Python task (same 5m/5m windows) and, when enabled, funds the
execution escrow at selection.

Currency note: the onchain auction/escrow settles in native ETH
(``selectWinnerAndFund`` is payable; ``initializeEscrow`` enforces
``msg.value + credit == vickrey price`` exactly).  The Python market prices
in AXM and keeps its existing payout path; the onchain mirror is the
ETH-settled trustless alternative until AXM settlement lands.

Two independent flags (both default off):
  AUCTION_ONCHAIN_ANCHOR=1  open the onchain auction at task creation
                            (fail-closed: the task is not created if the
                            open transaction fails).
  AUCTION_ONCHAIN_FUND=1    fund the escrow via selectWinnerAndFund at close
                            (fail-loud: failures are recorded on the task,
                            never brick the Python assignment).

The relayer key comes from ``AUCTION_RELAYER_KEY`` only (Secure Vault in
production).  Contract addresses from ``COMMIT_REVEAL_AUCTION_ADDRESS`` /
``EXECUTION_ESCROW_ADDRESS`` / ``AUCTION_RPC_URL`` (shared with
``auction_client``).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("sincor.market.relayer")

ANCHOR_ENV = "AUCTION_ONCHAIN_ANCHOR"
FUND_ENV = "AUCTION_ONCHAIN_FUND"
RELAYER_KEY_ENV = "AUCTION_RELAYER_KEY"

AUCTION_ID_DOMAIN = b"SINCOR_SEALED_AUCTION:"


def _keccak(data: bytes) -> bytes:
    try:
        from eth_hash.auto import keccak
    except Exception:  # pragma: no cover
        from sha3 import keccak_256 as _k  # type: ignore

        def keccak(data: bytes) -> bytes:  # type: ignore[no-redef]
            return _k(data).digest()
    return keccak(data)


def auction_id_for(task_id: str) -> bytes:
    """Deterministic bytes32 auction id for a Python task id."""
    return _keccak(AUCTION_ID_DOMAIN + str(task_id).encode("utf-8"))


def anchor_enabled() -> bool:
    return os.environ.get(ANCHOR_ENV, "").strip() == "1"


def fund_enabled() -> bool:
    return os.environ.get(FUND_ENV, "").strip() == "1"


class RelayerNotConfiguredError(RuntimeError):
    pass


class AuctionRelayer:
    """Builds, signs and sends poster transactions for the auction contracts."""

    def __init__(self, auction_address: str, rpc_url: str,
                 relayer_key: str):
        from sincor2.onchain.auction_client import _load_abi

        self.auction_address = auction_address
        self.rpc_url = rpc_url
        self._relayer_key = relayer_key
        self._w3 = None
        self._auction = None
        self._abi = _load_abi("CommitRevealAuction")

    @classmethod
    def from_env(cls) -> "AuctionRelayer":
        from sincor2.onchain.auction_client import (
            AUCTION_ENV_ADDRESS, RPC_ENV_URL,
        )

        auction = os.environ.get(AUCTION_ENV_ADDRESS, "").strip()
        rpc = os.environ.get(RPC_ENV_URL, "").strip()
        key = os.environ.get(RELAYER_KEY_ENV, "").strip()
        if not (auction and rpc and key):
            raise RelayerNotConfiguredError(
                "relayer needs COMMIT_REVEAL_AUCTION_ADDRESS, AUCTION_RPC_URL "
                "and AUCTION_RELAYER_KEY")
        return cls(auction, rpc, key)

    @property
    def w3(self):
        if self._w3 is None:
            from web3 import Web3

            self._w3 = Web3(Web3.HTTPProvider(
                self.rpc_url, request_kwargs={"timeout": 30}))
        return self._w3

    @property
    def auction(self):
        if self._auction is None:
            from web3 import Web3

            self._auction = self.w3.eth.contract(
                address=Web3.to_checksum_address(self.auction_address),
                abi=self._abi,
            )
        return self._auction

    @property
    def poster_address(self) -> str:
        from eth_account import Account

        return Account.from_key(self._relayer_key).address

    # -- readers -----------------------------------------------------------
    def is_open(self, task_id: str) -> bool:
        auction_id = auction_id_for(task_id)
        try:
            opened, finalized = self.auction.functions.auctions(
                auction_id).call()[:2]
            return bool(opened) and not bool(finalized)
        except Exception:
            return False

    def read_vickrey(self, task_id: str) -> Optional[Tuple[str, int]]:
        """(winner, price_wei) or None when no onchain reveals exist."""
        try:
            winner, price = self.auction.functions.vickreyResult(
                auction_id_for(task_id)).call()
        except Exception as err:
            logger.debug("vickreyResult unavailable for %s: %s", task_id, err)
            return None
        if int(winner, 16) == 0:
            return None
        return winner, int(price)

    # -- poster transactions -------------------------------------------------
    def _send(self, fn, value_wei: int = 0) -> str:
        from eth_account import Account

        account = Account.from_key(self._relayer_key)
        tx = fn.build_transaction({
            "from": account.address,
            # "pending" so back-to-back poster txs (open, fund) can't reuse
            # a nonce while the first is still in flight.
            "nonce": self.w3.eth.get_transaction_count(account.address,
                                                      "pending"),
            "gasPrice": self.w3.eth.gas_price,
            "value": value_wei,
            "chainId": self.w3.eth.chain_id,
        })
        tx["gas"] = int(self.w3.eth.estimate_gas(tx) * 1.2)
        signed = account.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
        tx_hash = self.w3.eth.send_raw_transaction(raw)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        if receipt.status != 1:
            raise RuntimeError(f"transaction reverted: {tx_hash.hex()}")
        return tx_hash.hex()

    def open_auction(self, task_id: str, commit_window_s: int = 300,
                     reveal_window_s: int = 300) -> Dict[str, Any]:
        """Open the onchain auction mirroring the Python sealed task."""
        auction_id = auction_id_for(task_id)
        tx_hash = self._send(self.auction.functions.openAuction(
            auction_id, commit_window_s, reveal_window_s))
        logger.info("opened onchain auction %s for task %s (%s)",
                    auction_id.hex(), task_id, tx_hash)
        return {"auction_id": "0x" + auction_id.hex(), "tx_hash": tx_hash}

    def select_winner_and_fund(self, task_id: str, value_wei: int,
                               credit_wei: int = 0) -> str:
        """Poster-only: select the Vickrey winner and fund the escrow.

        ``value_wei + credit_wei`` must equal the onchain Vickrey price
        exactly or the escrow reverts (fail-closed onchain).
        """
        tx_hash = self._send(
            self.auction.functions.selectWinnerAndFund(
                auction_id_for(task_id), credit_wei),
            value_wei=value_wei,
        )
        logger.info("selectWinnerAndFund for task %s (%s)", task_id, tx_hash)
        return tx_hash


def get_relayer() -> Optional[AuctionRelayer]:
    """The configured relayer, or None when it cannot be constructed.

    Callers gate on :func:`anchor_enabled` / :func:`fund_enabled`;
    misconfiguration is logged (never raised) so a bad env can't brick
    the Python market — the flag-gated call sites fail closed themselves.
    """
    try:
        return AuctionRelayer.from_env()
    except RelayerNotConfiguredError as err:
        logger.warning("auction relayer unconfigured: %s", err)
        return None
