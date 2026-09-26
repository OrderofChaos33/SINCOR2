"""web3.py client for the SINCOR2 auction contracts (wiring prep — NOT deployed).

Wraps ``CommitRevealAuction`` and ``ExecutionEscrowManager``: transaction
*builders* (unsigned dicts — the caller signs and broadcasts with their own
signer), read helpers, and event-listener scaffolding.

Safety rules (hard):
  * This module NEVER reads private keys. ``build_*_tx`` return unsigned
    transaction dicts; sign them with eth_account (or your HSM) and broadcast
    with ``w3.eth.send_raw_transaction`` in the calling code.
  * No mainnet calls are made here. Every public method raises
    :class:`AuctionNotConfiguredError` unless ``COMMIT_REVEAL_AUCTION_ADDRESS``,
    ``EXECUTION_ESCROW_ADDRESS`` and ``AUCTION_RPC_URL`` are all set.

ABIs live in ``src/sincor2/onchain/abis/`` (compiled from ``contracts/`` with
solc 0.8.24 --via-ir --optimize --optimize-runs 200; see
``docs/ops/AUCTION_DEPLOY_CHECKLIST.md``).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_ABIS_DIR = Path(__file__).resolve().parent / "abis"

AUCTION_ENV_ADDRESS = "COMMIT_REVEAL_AUCTION_ADDRESS"
ESCROW_ENV_ADDRESS = "EXECUTION_ESCROW_ADDRESS"
RPC_ENV_URL = "AUCTION_RPC_URL"
CHAIN_ID_ENV = "AUCTION_CHAIN_ID"

DEFAULT_CHAIN_ID = 8453  # Base mainnet

# Event names worth watching, grouped by operational concern.
AUCTION_LIFECYCLE_EVENTS = (
    "AuctionOpened", "Committed", "Revealed", "TimedOut", "WinnerSelected",
)
RESOLUTION_EVENTS = (
    "WinnerSelected", "EscrowInitialized", "EscrowFinalized",
    "EscrowTimedOut", "ResultSubmitted",
)
SLASH_EVENTS = (
    "SlashExecuted", "QualityDisputeOpened", "QualityDisputeResolved",
)
FEE_EVENTS = (
    "ReAuctionFundCredited", "ReAuctionFundDrawn", "PaymentQueued", "Withdrawn",
)


class AuctionNotConfiguredError(RuntimeError):
    """Raised when contract wiring is used without deployment addresses/RPC."""


@dataclass
class AuctionConfig:
    auction_address: Optional[str] = None
    escrow_address: Optional[str] = None
    rpc_url: Optional[str] = None
    chain_id: int = DEFAULT_CHAIN_ID

    @property
    def configured(self) -> bool:
        return bool(self.auction_address and self.escrow_address and self.rpc_url)

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "AuctionConfig":
        src = env if env is not None else os.environ
        missing = [v for v in (AUCTION_ENV_ADDRESS, ESCROW_ENV_ADDRESS, RPC_ENV_URL)
                   if not src.get(v)]
        if missing:
            raise AuctionNotConfiguredError(
                "auction contracts not configured; set: " + ", ".join(missing)
            )
        try:
            chain_id = int(src.get(CHAIN_ID_ENV) or DEFAULT_CHAIN_ID)
        except ValueError:
            raise AuctionNotConfiguredError(f"{CHAIN_ID_ENV} must be an integer")
        return cls(
            auction_address=src[AUCTION_ENV_ADDRESS],
            escrow_address=src[ESCROW_ENV_ADDRESS],
            rpc_url=src[RPC_ENV_URL],
            chain_id=chain_id,
        )


def _load_abi(name: str) -> List[Dict[str, Any]]:
    path = _ABIS_DIR / f"{name}.json"
    if not path.is_file():
        raise AuctionNotConfiguredError(
            f"ABI artifact missing: {path} (recompile contracts/ with solc 0.8.24)"
        )
    return json.loads(path.read_text(encoding="utf-8"))["abi"]


def compute_commitment(price_wei: int, salt: bytes, agent_id: str) -> bytes:
    """keccak256(abi.encodePacked(bytes32(price), salt, keccak256(agent_id))).

    The exact ``CommitRevealAuction.reveal`` preimage. Pairs with
    ``sincor2.a2a_inbound_market.sealed_commitment`` (the Python shim); both
    must stay byte-identical so shim clients can reuse commitments on-chain.
    """
    try:
        from eth_hash.auto import keccak
    except Exception:  # pragma: no cover
        from sha3 import keccak_256 as _k  # type: ignore

        def keccak(data: bytes) -> bytes:  # type: ignore[no-redef]
            return _k(data).digest()

    if price_wei <= 0:
        raise ValueError("price_wei must be positive")
    if len(salt) != 32:
        raise ValueError("salt must be 32 bytes")
    agent_id_hash = keccak(str(agent_id).encode("utf-8"))
    return keccak(int(price_wei).to_bytes(32, "big") + bytes(salt) + agent_id_hash)


class AuctionClient:
    """Unsigned-transaction builder + reader for the auction contracts."""

    def __init__(self, config: AuctionConfig):
        self.config = config
        self._w3 = None
        self._auction = None
        self._escrow = None

    # -- setup -----------------------------------------------------------
    def _require_configured(self) -> AuctionConfig:
        if not self.config.configured:
            raise AuctionNotConfiguredError(
                "auction contracts not configured; set "
                f"{AUCTION_ENV_ADDRESS}, {ESCROW_ENV_ADDRESS}, {RPC_ENV_URL}"
            )
        return self.config

    @property
    def w3(self):
        self._require_configured()
        if self._w3 is None:
            from web3 import Web3

            self._w3 = Web3(Web3.HTTPProvider(self.config.rpc_url))
        return self._w3

    def _checksum(self, address: str) -> str:
        from web3 import Web3

        return Web3.to_checksum_address(address)

    @property
    def auction(self):
        cfg = self._require_configured()
        if self._auction is None:
            self._auction = self.w3.eth.contract(
                address=self._checksum(cfg.auction_address),
                abi=_load_abi("CommitRevealAuction"),
            )
        return self._auction

    @property
    def escrow(self):
        cfg = self._require_configured()
        if self._escrow is None:
            self._escrow = self.w3.eth.contract(
                address=self._checksum(cfg.escrow_address),
                abi=_load_abi("ExecutionEscrowManager"),
            )
        return self._escrow

    # -- transaction building (unsigned; caller signs) --------------------
    def build_transaction(
        self,
        contract_function,
        sender: str,
        value_wei: int = 0,
        *,
        nonce: Optional[int] = None,
        gas: Optional[int] = None,
        gas_price_wei: Optional[int] = None,
        chain_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Build an unsigned tx dict for ``contract_function``.

        ``nonce``/``gas``/``gas_price_wei`` are fetched from the RPC when
        omitted (pass them explicitly for fully offline encoding). The dict
        is ready for ``eth_account`` signing — this module never sees keys.
        """
        cfg = self._require_configured()
        w3 = self.w3
        sender = self._checksum(sender)
        if nonce is None:
            nonce = w3.eth.get_transaction_count(sender)
        if gas is None:
            gas = contract_function.estimate_gas({"from": sender, "value": value_wei})
        tx_params: Dict[str, Any] = {
            "from": sender,
            "value": int(value_wei),
            "nonce": nonce,
            "gas": gas,
            "chainId": chain_id or cfg.chain_id,
        }
        if gas_price_wei is not None:
            tx_params["gasPrice"] = int(gas_price_wei)
        return contract_function.build_transaction(tx_params)

    # -- CommitRevealAuction ----------------------------------------------
    def build_commit_tx(self, auction_id: str, commit_hash: str, sender: str, **tx_kw) -> Dict[str, Any]:
        return self.build_transaction(
            self.auction.functions.commit(auction_id, commit_hash),
            sender, **tx_kw,
        )

    def build_reveal_tx(self, auction_id: str, price_wei: int, salt: str,
                        agent_id_hash: str, sender: str, **tx_kw) -> Dict[str, Any]:
        return self.build_transaction(
            self.auction.functions.reveal(auction_id, price_wei, salt, agent_id_hash),
            sender, **tx_kw,
        )

    def build_timeout_tx(self, auction_id: str, sender: str, **tx_kw) -> Dict[str, Any]:
        """Permissionless timeout after the reveal window (mirrors the
        ratified instant-timeout)."""
        return self.build_transaction(
            self.auction.functions.timeout(auction_id), sender, **tx_kw,
        )

    def build_select_winner_and_fund_tx(self, auction_id: str, bid_amount_wei: int,
                                       sender: str, value_wei: int = 0, **tx_kw) -> Dict[str, Any]:
        """selectWinnerAndFund is payable: fund the execution escrow with the
        Vickrey price. Pass ``value_wei`` = funding amount."""
        return self.build_transaction(
            self.auction.functions.selectWinnerAndFund(auction_id, bid_amount_wei),
            sender, value_wei=value_wei, **tx_kw,
        )

    def vickrey_result(self, auction_id: str):
        """(winner, price) view — no signature needed."""
        return self.auction.functions.vickreyResult(auction_id).call()

    # -- ExecutionEscrowManager -------------------------------------------
    def build_initialize_escrow_tx(
        self, escrow_id: str, poster: str, worker: str,
        bid_amount_wei: int, stake_wei: int,
        commit_deadline: int, reveal_deadline: int,
        execution_deadline: int, review_window: int,
        sender: str, value_wei: int = 0, **tx_kw,
    ) -> Dict[str, Any]:
        return self.build_transaction(
            self.escrow.functions.initializeEscrow(
                escrow_id, poster, worker, bid_amount_wei, stake_wei,
                commit_deadline, reveal_deadline, execution_deadline, review_window,
            ),
            sender, value_wei=value_wei, **tx_kw,
        )

    def build_deposit_stake_tx(self, auction_id: str, sender: str, value_wei: int, **tx_kw) -> Dict[str, Any]:
        """depositStake is payable and requires the EXACT stake amount."""
        return self.build_transaction(
            self.escrow.functions.depositStake(auction_id),
            sender, value_wei=value_wei, **tx_kw,
        )

    def build_withdraw_tx(self, sender: str, **tx_kw) -> Dict[str, Any]:
        """Pull-payment withdrawal of non-withdrawable-credit-safe funds."""
        return self.build_transaction(
            self.escrow.functions.withdraw(), sender, **tx_kw,
        )

    def pending_withdrawals(self, address: str) -> int:
        return self.escrow.functions.pendingWithdrawals(
            self._checksum(address)).call()

    # -- event scaffolding --------------------------------------------------
    def get_past_events(self, contract: str, event_name: str,
                        from_block: int = 0, to_block="latest") -> List[Dict[str, Any]]:
        """Decoded past events for ``contract`` in {"auction", "escrow"}.

        Poll this in a loop (or feed it to your indexer) to watch resolution,
        slash, and fee events. No signature required.
        """
        self._require_configured()
        handle = {"auction": self.auction, "escrow": self.escrow}.get(contract)
        if handle is None:
            raise ValueError('contract must be "auction" or "escrow"')
        logs = handle.events[event_name].get_logs(from_block=from_block, to_block=to_block)
        return [dict(log) for log in logs]
