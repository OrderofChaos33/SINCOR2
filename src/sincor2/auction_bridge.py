"""Python bridge to the onchain auction contracts (wiring prep — NOT deployed).

Entry points the task flow can call once the contracts deploy:

  - :func:`select_winner_and_fund` — poster-only: run the onchain Vickrey
    selection and atomically initialize the execution escrow in one
    transaction (``CommitRevealAuction.selectWinnerAndFund``).
  - :func:`initialize_escrow` — build and pre-validate the exact
    ``ExecutionEscrowManager.initializeEscrow`` call the auction core will
    make. On a live deployment only the auction core can execute this
    (``onlyAuctionCore``); the entry point exists so the platform can
    dry-run the core's parameters before selection, and so tests/forks can
    exercise the call path directly.

Safety rules (hard):

  * This module NEVER reads, stores, or derives private keys. Callers pass
    an ``eth_account`` ``Account`` object; signing uses the eth_account
    public API (``Account.sign_transaction``) with secp256k1 — the ratified
    money path. No HMAC, no alternate signing backend.
  * Every state-changing intent is preceded by an ``eth_call`` dry-run
    simulation (mirrors ``fee_conversion_executor``); a reverting
    simulation raises :class:`BridgeSimulationError` before anything is
    signed or broadcast.
  * Ground-truth invariants enforced client-side (see
    ``docs/architecture/AUCTION_GROUND_TRUTH.md``):
      - native ETH (wei) everywhere; ``creditToApply`` is uint96;
      - ``selectWinnerAndFund`` never takes a winner argument — the winner
        and clearing price come from ``vickreyResult`` onchain;
      - ``msg.value + creditToApply`` must equal the Vickrey price exactly;
      - selection draws credit strictly from the poster's Pool 2 balance
        (``posterReAuctionBalances[poster]``); Pool 1 offchain AXM credits
        can never offset onchain funding.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

UINT96_MAX = 2**96 - 1
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


class BridgeError(RuntimeError):
    """Base error for auction-bridge failures."""


class BridgeSimulationError(BridgeError):
    """Raised when the pre-sign eth_call dry-run reverts."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _as_bytes32(value: Any) -> bytes:
    if isinstance(value, (bytes, bytearray)) and len(value) == 32:
        return bytes(value)
    if isinstance(value, str):
        text = value[2:] if value.startswith(("0x", "0X")) else value
        raw = bytes.fromhex(text)
        if len(raw) == 32:
            return raw
    raise BridgeError(f"auction_id must be a 32-byte value, got {value!r}")


def _check_uint96(name: str, value: int) -> int:
    value = int(value)
    if value < 0 or value > UINT96_MAX:
        raise BridgeError(f"{name}={value} out of uint96 range")
    return value


def _checksum(w3: Any, address: str) -> str:
    return w3.to_checksum_address(address)


def _encode_data(fn: Any) -> str:
    """Calldata as a 0x-prefixed hex string (web3 v8 returns HexStr)."""
    data = fn._encode_transaction_data()
    if isinstance(data, (bytes, bytearray)):
        return "0x" + bytes(data).hex()
    text = str(data)
    return text if text.startswith("0x") else "0x" + text


def vickrey_result(w3: Any, auction: Any, auction_id: Any) -> Tuple[str, int]:
    """(winner, price_wei) from the onchain view — the caller never supplies
    the winner (ground-truth price discovery)."""
    winner, price = auction.functions.vickreyResult(_as_bytes32(auction_id)).call()
    return _checksum(w3, winner), int(price)


def read_auction(w3: Any, auction: Any, auction_id: Any) -> Dict[str, Any]:
    """Decode the onchain Auction struct for ``auction_id``."""
    opened, finalized, poster, commit_deadline, reveal_deadline = (
        auction.functions.auctions(_as_bytes32(auction_id)).call()
    )
    return {
        "opened": bool(opened),
        "finalized": bool(finalized),
        "poster": _checksum(w3, poster),
        "commit_deadline": int(commit_deadline),
        "reveal_deadline": int(reveal_deadline),
    }


def read_escrow(w3: Any, escrow: Any, auction_id: Any) -> Dict[str, Any]:
    """Decode the onchain Escrow struct for ``auction_id`` (escrow id reuses
    the auction id: exactly one escrow per auction)."""
    rec = escrow.functions.escrows(_as_bytes32(auction_id)).call()
    return {
        "poster": _checksum(w3, rec[0]),
        "selected_agent": _checksum(w3, rec[1]),
        "bid_amount_wei": int(rec[2]),
        "eth_deposited_wei": int(rec[3]),
        "credit_backing_wei": int(rec[4]),
        "agent_stake_wei": int(rec[5]),
        "state": int(rec[13]),
    }


def _dry_run_call(w3: Any, fn: Any, call_kwargs: Dict[str, Any], label: str) -> None:
    """eth_call simulation; raises BridgeSimulationError on revert."""
    try:
        fn.call(call_kwargs)
    except Exception as exc:
        raise BridgeSimulationError(f"dry-run reverted for {label}: {exc}") from exc


def _build_tx(
    w3: Any,
    fn: Any,
    sender: str,
    value_wei: int = 0,
    *,
    nonce: Optional[int] = None,
    gas_limit: Optional[int] = None,
    gas_price_wei: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
) -> Dict[str, Any]:
    tx: Dict[str, Any] = {
        "from": _checksum(w3, sender),
        "to": _checksum(w3, fn.address),
        "data": _encode_data(fn),
        "value": int(value_wei),
        "nonce": nonce if nonce is not None else w3.eth.get_transaction_count(sender, "pending"),
        "chainId": w3.eth.chain_id,
    }
    if max_fee_per_gas is not None:
        tx["maxFeePerGas"] = int(max_fee_per_gas)
        tx["maxPriorityFeePerGas"] = int(max_priority_fee_per_gas or 0)
    else:
        tx["gasPrice"] = int(gas_price_wei) if gas_price_wei is not None else w3.eth.gas_price
    if gas_limit is not None:
        tx["gas"] = int(gas_limit)
    else:
        # estimate_gas needs the fee fields present for a faithful estimate
        try:
            tx["gas"] = int(w3.eth.estimate_gas(
                {k: v for k, v in tx.items() if k != "nonce"}) * 1.2)
        except Exception as exc:
            raise BridgeError(
                f"gas estimation failed (transaction would revert): {exc}"
            ) from exc
    return tx


def _sign_and_send(w3: Any, tx: Dict[str, Any], signer: Any, timeout_s: int = 300) -> Dict[str, Any]:
    """Sign with the caller's eth_account Account and broadcast.

    ``signer`` must expose ``.address`` and ``.sign_transaction`` (the
    eth_account public API). This module never sees key material.
    """
    if not hasattr(signer, "sign_transaction") or not hasattr(signer, "address"):
        raise BridgeError("signer must be an eth_account Account (address + sign_transaction)")
    signed = signer.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    tx_hash = w3.eth.send_raw_transaction(raw)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout_s)
    if int(receipt.get("status", 0)) != 1:
        raise BridgeError(f"transaction reverted onchain: {tx_hash.hex()}")
    return {"tx_hash": "0x" + tx_hash.hex(), "status": int(receipt.status)}


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------

def select_winner_and_fund(
    w3: Any,
    auction: Any,
    auction_id: Any,
    *,
    signer: Any,
    credit_to_apply_wei: int = 0,
    value_wei: Optional[int] = None,
    escrow: Any = None,
    dry_run: bool = False,
    gas_limit: Optional[int] = None,
    gas_price_wei: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    nonce: Optional[int] = None,
    timeout_s: int = 300,
) -> Dict[str, Any]:
    """Poster-only: select the Vickrey winner and atomically fund the escrow.

    Reads ``(winner, price)`` from ``vickreyResult`` onchain, verifies
    ``value_wei + credit_to_apply_wei == price`` exactly, dry-runs via
    ``eth_call``, then signs (eth_account) and broadcasts.

    Returns ``{"tx_hash", "winner", "price_wei", "value_wei",
    "credit_to_apply_wei", "dry_run"}``. With ``dry_run=True`` nothing is
    signed or broadcast.
    """
    aid = _as_bytes32(auction_id)
    credit = _check_uint96("credit_to_apply_wei", credit_to_apply_wei)
    poster = _checksum(w3, getattr(signer, "address", ""))

    meta = read_auction(w3, auction, aid)
    if not meta["opened"]:
        raise BridgeError("auction is not open onchain")
    if meta["finalized"]:
        raise BridgeError("auction already finalized")
    if meta["poster"] != poster:
        raise BridgeError(f"signer {poster} is not the poster {meta['poster']}")
    latest = w3.eth.get_block("latest")
    now = int(latest["timestamp"] if isinstance(latest, dict) else latest.timestamp)
    if now <= meta["reveal_deadline"]:
        raise BridgeError("reveal window has not closed yet")

    winner, price = vickrey_result(w3, auction, aid)
    if int(winner, 16) == 0:
        raise BridgeError("vickreyResult returned no winner (no revealed bids)")
    value = int(price - credit) if value_wei is None else int(value_wei)
    if value < 0:
        raise BridgeError(f"credit_to_apply_wei={credit} exceeds Vickrey price={price}")
    if value + credit != price:
        raise BridgeError(
            f"funding mismatch: value({value}) + credit({credit}) != price({price})"
        )
    if escrow is not None:
        balance = int(escrow.functions.posterReAuctionBalances(poster).call())
        if credit > balance:
            raise BridgeError(
                f"credit_to_apply_wei={credit} exceeds poster's Pool 2 balance={balance}"
            )

    fn = auction.functions.selectWinnerAndFund(aid, credit)
    _dry_run_call(
        w3, fn,
        {"from": poster, "value": value, "to": auction.address},
        "selectWinnerAndFund",
    )
    tx = _build_tx(
        w3, fn, poster, value,
        nonce=nonce, gas_limit=gas_limit, gas_price_wei=gas_price_wei,
        max_fee_per_gas=max_fee_per_gas,
        max_priority_fee_per_gas=max_priority_fee_per_gas,
    )
    result: Dict[str, Any] = {
        "winner": winner,
        "price_wei": price,
        "value_wei": value,
        "credit_to_apply_wei": credit,
        "escrow_id": "0x" + aid.hex(),
        "dry_run": bool(dry_run),
    }
    if dry_run:
        logger.info("[dry-run] selectWinnerAndFund simulated OK (winner=%s price=%d)",
                    winner, price)
        result["unsigned_tx"] = tx
        return result
    result.update(_sign_and_send(w3, tx, signer, timeout_s=timeout_s))
    logger.info("selectWinnerAndFund confirmed: %s (winner=%s price=%d)",
                result["tx_hash"], winner, price)
    return result


def initialize_escrow(
    w3: Any,
    escrow: Any,
    *,
    auction_id: Any,
    poster: str,
    selected_agent: str,
    bid_amount_wei: int,
    credit_to_apply_wei: int = 0,
    execution_duration_s: int,
    dispute_window_s: int,
    adjudication_window_s: int,
    stake_deposit_window_s: int,
    value_wei: Optional[int] = None,
    auction_core: str,
    signer: Any = None,
    dry_run: bool = True,
    gas_limit: Optional[int] = None,
    gas_price_wei: Optional[int] = None,
    nonce: Optional[int] = None,
    timeout_s: int = 300,
) -> Dict[str, Any]:
    """Build and pre-validate the ``initializeEscrow`` call.

    On a live deployment this function is ``onlyAuctionCore`` — it is
    executed by ``CommitRevealAuction.selectWinnerAndFund``, never by an
    external account. This entry point builds the exact calldata the core
    will pass and dry-runs it via ``eth_call`` with ``from=auction_core``
    so the platform can validate parameters *before* selection. Note the
    core holds no ETH between transactions (it has no receive()), so the
    dry-run needs the core funded with ``value`` — on forks via
    impersonation/funding, in tests via a constructor-selfdestruct credit.
    For the full value-flow simulation, use ``select_winner_and_fund``
    with ``dry_run=True``, which atomically executes this call. Pass
    ``signer`` to also sign and broadcast (reverts unless the signer is the
    auction core — useful on forks/tests to prove the access control).

    With the default ``dry_run=True`` nothing is signed or broadcast;
    returns ``{"data", "dry_run": True, ...}``.
    """
    aid = _as_bytes32(auction_id)
    bid = _check_uint96("bid_amount_wei", bid_amount_wei)
    if bid == 0:
        raise BridgeError("bid_amount_wei must be positive")
    credit = _check_uint96("credit_to_apply_wei", credit_to_apply_wei)
    poster_c = _checksum(w3, poster)
    agent_c = _checksum(w3, selected_agent)
    core_c = _checksum(w3, auction_core)
    if int(poster_c, 16) == 0 or int(agent_c, 16) == 0:
        raise BridgeError("poster and selected_agent must be nonzero addresses")
    value = int(bid - credit) if value_wei is None else int(value_wei)
    if value < 0:
        raise BridgeError(f"credit_to_apply_wei={credit} exceeds bid_amount_wei={bid}")
    if value + credit != bid:
        raise BridgeError(
            f"funding mismatch: value({value}) + credit({credit}) != bid({bid})"
        )

    fn = escrow.functions.initializeEscrow(
        aid, poster_c, agent_c, bid, credit,
        int(execution_duration_s), int(dispute_window_s),
        int(adjudication_window_s), int(stake_deposit_window_s),
    )
    _dry_run_call(
        w3, fn,
        {"from": core_c, "value": value, "to": escrow.address},
        "initializeEscrow",
    )
    result: Dict[str, Any] = {
        "data": _encode_data(fn),
        "auction_id": "0x" + aid.hex(),
        "poster": poster_c,
        "selected_agent": agent_c,
        "bid_amount_wei": bid,
        "credit_to_apply_wei": credit,
        "value_wei": value,
        "dry_run": bool(dry_run),
    }
    if dry_run or signer is None:
        logger.info("[dry-run] initializeEscrow simulated OK (bid=%d credit=%d)",
                    bid, credit)
        return result
    sender = _checksum(w3, getattr(signer, "address", ""))
    tx = _build_tx(w3, fn, sender, value, nonce=nonce,
                   gas_limit=gas_limit, gas_price_wei=gas_price_wei)
    result.update(_sign_and_send(w3, tx, signer, timeout_s=timeout_s))
    result["dry_run"] = False
    return result
