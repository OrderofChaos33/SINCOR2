"""Onchain fee-inflow event listener — read-only plumbing for the AXM money path.

Watches the AXM ERC-20 contract on Base for ``Transfer`` events into the
platform fee-recipient address and records the locked 5 % platform fee of
each *confirmed* inflow as a pending obligation in the ``ConversionLedger``
via ``record_pending_conversion()``.  A recorded fee inflow becomes a
ledger obligation automatically.

What this module is NOT (hard rules — build-out item 32 is out of scope):
* It never handles private keys, never signs, never broadcasts.
* It never arms the executor: ``FeeConversionConfig.armed`` stays False.
* It performs no conversion, no pool pinning, no treasury deposit.
  Pure plumbing only.

Security posture (same as ``payment_verifier.py`` / ``auction_client.py``):
* web3.py polling via ``eth_getLogs`` on an operator-configured RPC URL.
* ``confirmations``-deep processing: an event is recorded only after N
  confirmations (default 12), so short reorgs cannot double-record.
* Durable cursor (last scanned block + block hash) under SINCOR_DATA_DIR:
  restarts resume exactly where they stopped — no gaps, no replays.
* Reorg detection: if the stored block hash no longer matches the chain,
  the cursor rewinds REORG_WINDOW blocks and the range is rescanned.
  Re-recording is idempotent — obligation IDs derive from
  (tx_hash, fee amount) — so a rescan can never create duplicates.
* RPC failures fail closed: the cursor never advances on error and
  ``run_once()`` returns ``ok=False``; the next run retries the same range.
* Dust filter: inflows below ``min_recordable_wei`` are observed but not
  recorded as obligations (logged at debug level).
* Client-side log verification: every returned log is re-checked against
  the token address, the Transfer topic, and the recipient topic before it
  is trusted — a misbehaving RPC cannot smuggle in foreign events.
* Trust boundary: like every other web3 path in this repo, the listener
  trusts the operator-configured RPC endpoint.  A compromised RPC could
  feed fabricated logs; the damage is bounded because (a) obligations are
  pending-only and the executor is DISARMED, so no funds can move on their
  basis, and (b) every obligation carries tx_hash/block/log_index so it can
  be reconciled against real receipts before any future arming
  (see docs/ops/FEE_EXECUTOR_RUNBOOK.md).

Ops notes:
* Run exactly ONE listener instance per (cursor, ledger) pair.  Two
  instances sharing a ledger are idempotent-safe (same obligation ids),
  but two instances with different ledgers would record divergent
  journals.
* The listener never arms the executor.  Arming is the item-32 ceremony;
  until then ``FeeConversionConfig.armed`` stays False and obligations
  queue in the ledger.

Fee model: each Transfer into the fee recipient is treated as a platform
payment; the locked 5 % platform fee (fee policy 2026-09-26) of each
payment is recorded as the conversion obligation.  Direct transfers to the
recipient are conservatively covered too — over-recording while the
executor is disarmed only queues more pending obligations, never moves
funds.  Before any future arming, reconcile obligations against receipts.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from eth_hash.auto import keccak

from sincor2.onchain.constants import AXIOM_TOKEN, BASE_CHAIN_ID, TREASURY
from sincor2.onchain.fee_conversion_executor import (
    ConversionLedger,
    pending_conversion_id,
    record_pending_conversion,
)

logger = logging.getLogger("sincor.treasury.fee_listener")

# --- Chain / event constants -------------------------------------------------

# Transfer(address,address,uint256)
TRANSFER_TOPIC = "0x" + keccak(b"Transfer(address,address,uint256)").hex()

# Locked fee policy (2026-09-26): 5 % platform fee, 100 % to treasury, no burn.
# Mirrors marketplace.settlement.PLATFORM_FEE_BPS and
# a2a_integration.A2A_PLATFORM_FEE_BPS (both default 500); the listener reads
# its own env so it never drags in the app import chain.  A non-500 value is
# logged as a warning because it contradicts the locked policy.
BPS_DENOM = 10_000

# Blocks rewound when a reorg is detected at the cursor.
REORG_WINDOW = 64
# Cap on blocks scanned per run_once() so one run cannot exceed RPC limits.
MAX_BLOCKS_PER_SCAN = 2_000


def _default_cursor_path() -> str:
    explicit = os.environ.get("FEE_LISTENER_CURSOR_PATH", "").strip()
    if explicit:
        return os.path.expanduser(explicit)
    try:
        from sincor2.data_paths import data_dir

        return str(data_dir() / "fee_event_cursor.json")
    except Exception:
        return os.path.expanduser("~/workspace/ops/fee_event_cursor.json")


def _topic_address(addr: str) -> str:
    """Left-pad an address to a 32-byte log topic."""
    return "0x" + addr.lower().replace("0x", "").rjust(64, "0")


def _hex(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    if hasattr(value, "hex"):  # HexBytes and friends
        return "0x" + value.hex().lstrip("0x")
    return str(value)


# --- Config ------------------------------------------------------------------


@dataclass
class FeeListenerConfig:
    """Operator config for one listener instance.  Read-only: no keys."""

    rpc_url: str = field(
        default_factory=lambda: os.getenv(
            "FEE_LISTENER_RPC_URL",
            os.getenv("BASE_RPC_URL", "https://mainnet.base.org"),
        ).strip()
    )
    token: str = field(
        default_factory=lambda: os.getenv("FEE_LISTENER_TOKEN", AXIOM_TOKEN)
    )
    # Address whose inbound AXM transfers count as fee inflows.
    fee_recipient: str = field(
        default_factory=lambda: os.getenv("SINCOR_FEE_RECIPIENT", TREASURY)
    )
    confirmations: int = field(
        default_factory=lambda: int(os.getenv("FEE_LISTENER_CONFIRMATIONS", "12"))
    )
    poll_interval_secs: int = field(
        default_factory=lambda: int(os.getenv("FEE_LISTENER_POLL_SECS", "30"))
    )
    # Inflows below this are observed but not recorded as obligations.
    min_recordable_wei: int = field(
        default_factory=lambda: int(os.getenv("FEE_LISTENER_MIN_WEI", "0"))
    )
    fee_bps: int = field(
        default_factory=lambda: int(os.getenv("FEE_LISTENER_FEE_BPS", "500"))
    )
    # First-run behaviour: start scanning at the current tip (0 = no backfill).
    backfill_blocks: int = field(
        default_factory=lambda: int(os.getenv("FEE_LISTENER_BACKFILL_BLOCKS", "0"))
    )
    cursor_path: str = field(default_factory=_default_cursor_path)
    # Explicit start block overrides backfill logic on first run.
    start_block: Optional[int] = field(
        default_factory=lambda: (
            int(v) if (v := os.getenv("FEE_LISTENER_START_BLOCK", "").strip()) else None
        )
    )

    def __post_init__(self) -> None:
        if self.fee_bps != 500:
            logger.warning(
                "FEE_LISTENER_FEE_BPS=%d differs from the locked 5%% policy (500); "
                "recording obligations at the configured rate",
                self.fee_bps,
            )


# --- Listener ----------------------------------------------------------------


class FeeEventListener:
    """Polls eth_getLogs for fee-inflow Transfers and records obligations.

    Construct with an optional ``w3_factory`` (used by tests to inject a
    fake); production uses a lazy ``web3.Web3`` HTTP client.  This class
    never signs, never broadcasts, never touches keys.
    """

    def __init__(
        self,
        config: Optional[FeeListenerConfig] = None,
        ledger: Optional[ConversionLedger] = None,
        w3_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.config = config or FeeListenerConfig()
        self.ledger = ledger or ConversionLedger(
            os.environ.get("SINCOR_FEE_LEDGER_PATH", "").strip()
            or _default_ledger_path()
        )
        self._w3_factory = w3_factory or self._default_w3_factory
        self._w3_client: Optional[Any] = None

    # -- web3 ----------------------------------------------------------------
    @staticmethod
    def _default_w3_factory() -> Any:
        from web3 import HTTPProvider, Web3

        rpc_url = os.getenv(
            "FEE_LISTENER_RPC_URL",
            os.getenv("BASE_RPC_URL", "https://mainnet.base.org"),
        ).strip()
        return Web3(HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))

    def _w3(self) -> Any:
        if self._w3_client is None:
            self._w3_client = self._w3_factory()
        return self._w3_client

    # -- cursor ---------------------------------------------------------------
    def load_cursor(self) -> Optional[Dict[str, Any]]:
        path = self.config.cursor_path
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data.get("block"), int) and data.get("hash"):
                return {"block": data["block"], "hash": str(data["hash"])}
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("fee-listener cursor load failed (%s); starting fresh", exc)
        return None

    def save_cursor(self, block: int, block_hash: str) -> None:
        path = self.config.cursor_path
        tmp = path + ".tmp"
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"block": block, "hash": block_hash}, fh)
        os.replace(tmp, path)

    # -- chain reads -----------------------------------------------------------
    def _block_hash(self, w3: Any, block_number: int) -> str:
        block = w3.eth.get_block(block_number)
        raw = block["hash"] if isinstance(block, dict) else block.hash
        return _hex(raw)

    def _reorged(self, w3: Any, cursor: Dict[str, Any]) -> bool:
        """True if the chain no longer has our cursor block hash (reorg)."""
        try:
            return self._block_hash(w3, cursor["block"]) != cursor["hash"]
        except Exception as exc:
            # Cannot verify the cursor — fail closed, do not advance.
            raise ListenerError(f"cursor reorg check failed: {exc}") from exc

    def fetch_transfer_events(
        self, w3: Any, from_block: int, to_block: int
    ) -> List[Dict[str, Any]]:
        """Return verified fee-inflow Transfer events in [from_block, to_block].

        Every log is re-validated client-side (token address, Transfer
        topic, recipient topic) so a misbehaving RPC cannot smuggle in
        foreign events.
        """
        if to_block < from_block:
            return []
        recipient_topic = _topic_address(self.config.fee_recipient)
        token = self.config.token
        logs = w3.eth.get_logs(
            {
                "fromBlock": from_block,
                "toBlock": to_block,
                "address": token,
                "topics": [TRANSFER_TOPIC, None, recipient_topic],
            }
        )
        events: List[Dict[str, Any]] = []
        for log in logs:
            parsed = self._parse_log(log, token, recipient_topic)
            if parsed is not None:
                events.append(parsed)
        return events

    def _parse_log(
        self, log: Any, token: str, recipient_topic: str
    ) -> Optional[Dict[str, Any]]:
        get = (lambda k: log[k]) if isinstance(log, dict) else (lambda k: getattr(log, k))
        try:
            address = get("address")
            topics = [_hex(t) for t in get("topics")]
            data = _hex(get("data"))
        except Exception:
            logger.debug("fee-listener: skipping malformed log")
            return None
        # Client-side verification — never trust the filter alone.
        if str(address).lower() != token.lower():
            return None
        if len(topics) != 3 or topics[0].lower() != TRANSFER_TOPIC.lower():
            return None
        if topics[2].lower() != recipient_topic.lower():
            return None
        try:
            value_wei = int(data, 16)
            from_addr = "0x" + topics[1][-40:]
        except (ValueError, IndexError):
            return None
        try:
            block_number = int(get("blockNumber"))
            log_index = int(get("logIndex"))
        except (TypeError, ValueError):
            return None
        return {
            "tx_hash": _hex(get("transactionHash")),
            "block_number": block_number,
            "block_hash": _hex(get("blockHash")),
            "log_index": log_index,
            "from": from_addr,
            "to": self.config.fee_recipient,
            "value_wei": value_wei,
            "token": token,
        }

    # -- obligation recording ---------------------------------------------------
    def process_events(
        self, events: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Record the 5 % fee of each inflow as a ConversionLedger obligation.

        Idempotent: obligation IDs derive from (tx_hash, fee amount), so
        re-processing the same events is a no-op.
        """
        recorded: List[str] = []
        reencountered = 0
        skipped_dust = 0
        for ev in events:
            value_wei = int(ev["value_wei"])
            if value_wei < self.config.min_recordable_wei:
                skipped_dust += 1
                logger.debug(
                    "fee-listener: dust inflow skipped tx=%s value_wei=%d",
                    ev["tx_hash"][:18],
                    value_wei,
                )
                continue
            fee_wei = (value_wei * self.config.fee_bps) // BPS_DENOM
            if fee_wei <= 0:
                skipped_dust += 1
                continue
            source = {
                "tx_hash": ev["tx_hash"],
                "from": ev["from"],
                "to": ev["to"],
                "token": ev["token"],
                "block_number": ev["block_number"],
                "block_hash": ev["block_hash"],
                "log_index": ev["log_index"],
                "inflow_value_wei": str(value_wei),
                "fee_bps": self.config.fee_bps,
                "listener": "fee_event_listener",
                "chain_id": BASE_CHAIN_ID,
            }
            oid = pending_conversion_id("AXM", fee_wei, source)
            is_new = self.ledger.get(oid) is None
            ob = record_pending_conversion(self.ledger, "AXM", fee_wei, source)
            if is_new:
                recorded.append(ob["id"])
                logger.info(
                    "fee-listener: obligation %s recorded "
                    "(fee %d wei of %d wei, tx=%s)",
                    ob["id"], fee_wei, value_wei, ev["tx_hash"][:18],
                )
            else:
                # Idempotent re-encounter (e.g. after a reorg rescan):
                # the ledger already holds this obligation; count it
                # separately so ops logs never imply new money moved.
                reencountered += 1
                logger.debug(
                    "fee-listener: obligation %s already recorded; skipped",
                    ob["id"],
                )
        return {"recorded": recorded, "recorded_count": len(recorded),
                "reencountered": reencountered, "skipped_dust": skipped_dust}

    # -- main cycle ---------------------------------------------------------------
    def run_once(self) -> Dict[str, Any]:
        """One poll cycle.  Returns a summary dict; never raises on RPC errors.

        The cursor advances ONLY after events are fetched and recorded
        successfully — an RPC failure anywhere fails closed (ok=False) and
        the next run retries the same range.
        """
        try:
            w3 = self._w3()
        except Exception as exc:
            return self._rpc_failure(f"web3 client init failed: {exc}")
        try:
            gbn = w3.eth.get_block_number
            latest = int(gbn() if callable(gbn) else gbn)
        except Exception as exc:
            return self._rpc_failure(f"get_block_number failed: {exc}")

        tip = latest - self.config.confirmations
        if tip < 0:
            tip = 0

        cursor = self.load_cursor()
        reorg = False
        if cursor is None:
            # First run: begin at the tip (no historical backfill by default).
            start = tip - self.config.backfill_blocks
            if self.config.start_block is not None:
                start = self.config.start_block
            # Cursor semantics = "last fully processed block", so the first
            # scan covers (start-1, tip], i.e. blocks >= start.
            from_block = max(-1, start - 1)
        else:
            try:
                if self._reorged(w3, cursor):
                    reorg = True
                    rewound = max(0, cursor["block"] - REORG_WINDOW)
                    logger.warning(
                        "fee-listener: reorg detected at cursor block %d "
                        "(stored %s); rewinding to %d and rescanning",
                        cursor["block"], cursor["hash"][:18], rewound,
                    )
                    from_block = rewound
                else:
                    from_block = cursor["block"]
            except ListenerError as exc:
                return self._rpc_failure(str(exc))

        # Cap the scan window so one run cannot exceed RPC log limits.
        to_block = min(tip, from_block + MAX_BLOCKS_PER_SCAN)
        if to_block <= from_block:
            return {"ok": True, "scanned": 0, "recorded_count": 0,
                    "from_block": from_block, "to_block": to_block,
                    "tip": tip, "reorg": reorg}

        try:
            events = self.fetch_transfer_events(w3, from_block + 1, to_block)
            result = self.process_events(events)
            tip_hash = self._block_hash(w3, to_block)
            self.save_cursor(to_block, tip_hash)
        except Exception as exc:
            # Ledger-write or disk failures land here too: nothing was
            # committed past the cursor, so the next run retries the same
            # range (recording is idempotent).  Never advance on error.
            return self._rpc_failure(f"scan/process/cursor failed: {exc}")

        summary = {
            "ok": True,
            "scanned": len(events),
            "from_block": from_block + 1,
            "to_block": to_block,
            "tip": tip,
            "reorg": reorg,
            "cursor": {"block": to_block, "hash": tip_hash},
        }
        summary.update(result)
        return summary

    def _rpc_failure(self, error: str) -> Dict[str, Any]:
        logger.error("fee-listener: %s — cursor NOT advanced (fail-closed)", error)
        return {"ok": False, "error": error}

    def run_forever(self) -> None:
        """Poll forever.  Ctrl-C stops cleanly; the cursor persists."""
        logger.info(
            "fee-listener starting: token=%s recipient=%s confirmations=%d",
            self.config.token, self.config.fee_recipient,
            self.config.confirmations,
        )
        backoff = self.config.poll_interval_secs
        try:
            while True:
                summary = self.run_once()
                if summary.get("ok"):
                    backoff = self.config.poll_interval_secs
                    if summary.get("recorded_count"):
                        logger.info("fee-listener cycle: %s", summary)
                else:
                    logger.warning(
                        "fee-listener cycle failed; retrying in %ds", backoff)
                    backoff = min(backoff * 2, 600)
                time.sleep(backoff)
        except KeyboardInterrupt:
            logger.info("fee-listener stopped by operator")


class ListenerError(RuntimeError):
    pass


def _default_ledger_path() -> str:
    explicit = os.environ.get("SINCOR_FEE_LEDGER_PATH", "").strip()
    if explicit:
        return os.path.expanduser(explicit)
    try:
        from sincor2.data_paths import data_dir

        return str(data_dir() / "fee_conversion_ledger.json")
    except Exception:
        return os.path.expanduser("~/workspace/ops/fee_conversion_ledger.json")


def build_listener(
    w3_factory: Optional[Callable[[], Any]] = None,
) -> FeeEventListener:
    """Build a listener from environment config (ops entry point)."""
    return FeeEventListener(config=FeeListenerConfig(), w3_factory=w3_factory)


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("FEE_LISTENER_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    build_listener().run_forever()
