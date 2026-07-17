"""Unified execution adapter — the ONLY place orders reach an exchange.

Replaces the three disconnected Polyclaw implementations
(``polyclaw_scheduler``, ``polyclaw_mega_aggressive_live``,
``verticals/trading/polyclaw``) behind a single adapter with hard guarantees:

- **Dry-run by default.** Live orders require ``POLYCLAW_LIVE=true`` AND valid
  Polymarket credentials in the environment. Anything else simulates and
  says so loudly.
- **Capital caps enforced upstream** by ``bankroll.py`` (default $20 total
  exposure, $5 per position). The adapter refuses orders that exceed them.
- **Kill switch.** A tripped switch (DB flag or ``/data/POLYCLAW_HALT`` file)
  blocks every order until manually cleared.
- **Fill reconciliation** against the CLOB REST API — no phantom PnL. An
  order that was never accepted is never counted.

Nothing here signs anything without an explicit private key from the
environment. Keys are never logged.

Environment
-----------
POLYCLAW_LIVE                 "true" to allow real orders (default: false)
POLYMARKET_PRIVATE_KEY        Polygon EOA key for the CLOB (hex, 0x-prefixed)
POLYMARKET_FUNDER             Address funding the orders (defaults to key addr)
POLYMARKET_API_KEY / _SECRET / _PASSPHRASE   CLOB API creds (derived if absent)
POLYMARKET_HOST               default https://clob.polymarket.com
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from sincor2.bankroll import Bankroll, get_bankroll

logger = logging.getLogger("sincor.execution")

POLYMARKET_HOST = os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com")
POLYGON_CHAIN_ID = 137
HALT_FILE = Path(os.getenv("POLYCLAW_HALT_FILE", "/data/POLYCLAW_HALT"))


@dataclass
class OrderResult:
    """Outcome of an order attempt. ``simulated=True`` means nothing was sent."""

    success: bool
    simulated: bool
    order_id: Optional[str] = None
    token_id: Optional[str] = None
    side: Optional[str] = None
    size_usd: float = 0.0
    filled_usd: float = 0.0
    avg_price: Optional[float] = None
    error: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


class KillSwitchTripped(RuntimeError):
    pass


class LiveTradingNotEnabled(RuntimeError):
    pass


def kill_switch_tripped() -> bool:
    """Kill switch is tripped by DB flag or halt-file presence."""
    if HALT_FILE.exists():
        return True
    try:
        return get_bankroll().kill_switch_active()
    except Exception:
        # If we cannot read the bankroll DB, fail closed.
        return True


def trip_kill_switch(reason: str) -> None:
    logger.critical("POLYCLAW KILL SWITCH TRIPPED: %s", reason)
    try:
        get_bankroll().trip_kill_switch(reason)
    finally:
        try:
            HALT_FILE.parent.mkdir(parents=True, exist_ok=True)
            HALT_FILE.write_text(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {reason}\n")
        except OSError:
            pass


def clear_kill_switch() -> None:
    try:
        get_bankroll().clear_kill_switch()
    finally:
        HALT_FILE.unlink(missing_ok=True)


class PolymarketAdapter:
    """Polymarket CLOB execution with dry-run default and reconciliation."""

    def __init__(self, bankroll: Optional[Bankroll] = None) -> None:
        self.bankroll = bankroll or get_bankroll()
        self._client = None
        self._client_error: Optional[str] = None

    # ------------------------------------------------------------------
    # Client lifecycle
    # ------------------------------------------------------------------

    def is_live(self) -> bool:
        return os.getenv("POLYCLAW_LIVE", "false").lower() == "true" and self._credentials_present()

    @staticmethod
    def _credentials_present() -> bool:
        return bool(os.getenv("POLYMARKET_PRIVATE_KEY", "").strip())

    def _get_client(self):
        """Lazily build the py_clob_client. Raises with a clear reason."""
        if self._client is not None:
            return self._client
        if self._client_error is not None:
            raise LiveTradingNotEnabled(self._client_error)

        private_key = os.getenv("POLYMARKET_PRIVATE_KEY", "").strip()
        if not private_key:
            self._client_error = "POLYMARKET_PRIVATE_KEY not set"
            raise LiveTradingNotEnabled(self._client_error)

        try:
            from py_clob_client.client import ClobClient
        except ImportError:
            self._client_error = "py-clob-client not installed (pip install py-clob-client)"
            raise LiveTradingNotEnabled(self._client_error)

        try:
            funder = os.getenv("POLYMARKET_FUNDER", "").strip() or None
            client = ClobClient(
                POLYMARKET_HOST,
                key=private_key,
                chain_id=POLYGON_CHAIN_ID,
                funder=funder,
            )
            # Use existing API creds if provided, else derive them from the key.
            api_key = os.getenv("POLYMARKET_API_KEY", "").strip()
            api_secret = os.getenv("POLYMARKET_API_SECRET", "").strip()
            api_passphrase = os.getenv("POLYMARKET_API_PASSPHRASE", "").strip()
            if api_key and api_secret and api_passphrase:
                from py_clob_client.client import ApiCreds
                client.set_api_creds(ApiCreds(api_key, api_secret, api_passphrase))
            else:
                client.set_api_creds(client.create_or_derive_api_creds())
            self._client = client
            logger.info("Polymarket CLOB client initialised (live mode)")
            return client
        except Exception as exc:  # noqa: BLE001 — surface any client failure uniformly
            self._client_error = f"CLOB client init failed: {exc}"
            raise LiveTradingNotEnabled(self._client_error) from exc

    # ------------------------------------------------------------------
    # Market data (read-only, no auth required)
    # ------------------------------------------------------------------

    @staticmethod
    def get_midpoint(token_id: str) -> Optional[float]:
        """Best-bid/ask midpoint for a CLOB token, or None."""
        try:
            url = f"{POLYMARKET_HOST}/midpoint?token_id={token_id}"
            with urllib.request.urlopen(url, timeout=8) as resp:
                data = json.loads(resp.read())
            mid = data.get("mid")
            return float(mid) if mid is not None else None
        except Exception as exc:
            logger.debug("midpoint fetch failed for %s: %s", token_id, exc)
            return None

    @staticmethod
    def get_price_history(token_id: str, interval: str = "1d", fidelity: int = 60) -> List[Dict[str, Any]]:
        """CLOB price history points [{t, p}, ...]."""
        try:
            url = (
                f"{POLYMARKET_HOST}/prices-history?market={token_id}"
                f"&interval={interval}&fidelity={fidelity}"
            )
            with urllib.request.urlopen(url, timeout=8) as resp:
                data = json.loads(resp.read())
            return data.get("history", []) or []
        except Exception as exc:
            logger.debug("price history fetch failed for %s: %s", token_id, exc)
            return []

    # ------------------------------------------------------------------
    # Order execution
    # ------------------------------------------------------------------

    def place_market_buy(self, token_id: str, usd_amount: float) -> OrderResult:
        """Place a market buy for ``usd_amount`` of a YES/NO token.

        Dry-run unless live mode is fully enabled. Hard-capped by bankroll.
        """
        if kill_switch_tripped():
            return OrderResult(False, simulated=True, token_id=token_id,
                               error="kill switch tripped")

        if not self.bankroll.can_open(usd_amount):
            return OrderResult(False, simulated=True, token_id=token_id,
                               size_usd=usd_amount,
                               error="bankroll cap reached or risk limit hit")

        if not self.is_live():
            logger.info(
                "[DRY RUN] market buy %.2f USD of token %s "
                "(set POLYCLAW_LIVE=true + POLYMARKET_PRIVATE_KEY to go live)",
                usd_amount, token_id,
            )
            self.bankroll.reserve_exposure(usd_amount)
            return OrderResult(True, simulated=True, token_id=token_id,
                               side="BUY", size_usd=usd_amount,
                               filled_usd=usd_amount)

        try:
            from py_clob_client.clob_types import MarketOrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY
        except ImportError:
            return OrderResult(False, simulated=False, token_id=token_id,
                               error="py-clob-client not installed")

        try:
            client = self._get_client()
            order_args = MarketOrderArgs(
                token_id=token_id,
                amount=usd_amount,  # USDC notional for market buys
                side=BUY,
            )
            signed = client.create_market_order(order_args)
            resp = client.post_order(signed, OrderType.FOK)  # fill-or-kill
            order_id = resp.get("orderID") or resp.get("id")
            filled = float(resp.get("makingAmount", 0) or 0)
            status = str(resp.get("status", "")).lower()
            success = status in ("matched", "filled") and bool(order_id)
            if success:
                self.bankroll.reserve_exposure(usd_amount)
                logger.info("[LIVE] filled market buy %s USD on %s order=%s",
                            usd_amount, token_id, order_id)
            else:
                logger.warning("[LIVE] order not matched: %s", resp)
            return OrderResult(success, simulated=False, order_id=order_id,
                               token_id=token_id, side="BUY", size_usd=usd_amount,
                               filled_usd=filled, raw=resp)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[LIVE] market buy failed")
            return OrderResult(False, simulated=False, token_id=token_id,
                               side="BUY", size_usd=usd_amount, error=str(exc))

    def get_order_status(self, order_id: str) -> Dict[str, Any]:
        """Reconcile an order against the CLOB. Live mode only."""
        if not self.is_live():
            return {"order_id": order_id, "status": "simulated"}
        try:
            client = self._get_client()
            return client.get_order(order_id)
        except Exception as exc:  # noqa: BLE001
            return {"order_id": order_id, "error": str(exc)}

    def reconcile(self, order_id: str) -> Optional[float]:
        """Return the actually-filled USD for an order, or None if unknown.

        Use this before booking any PnL — never trust the requested size.
        """
        status = self.get_order_status(order_id)
        if "error" in status:
            return None
        try:
            return float(status.get("size_matched", 0) or 0)
        except (TypeError, ValueError):
            return None


# ---------------------------------------------------------------------------
# On-chain executor (Base) — thin signer for treasury-side operations
# ---------------------------------------------------------------------------

class OnChainExecutor:
    """Signs Base transactions (e.g. treasury conversions, hook interactions).

    Same rules as the CLOB adapter: dry-run unless POLYCLAW_LIVE=true and a
    key is present. Separate key from the CLOB so a Polymarket compromise
    cannot touch on-chain funds.
    """

    def __init__(self) -> None:
        self.rpc_url = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")
        self.private_key = os.getenv("ONCHAIN_EXECUTOR_PRIVATE_KEY", "").strip()

    def is_live(self) -> bool:
        return os.getenv("POLYCLAW_LIVE", "false").lower() == "true" and bool(self.private_key)

    def send_raw(self, tx: Dict[str, Any]) -> OrderResult:
        """Sign and broadcast a prepared transaction dict. Dry-run by default."""
        if kill_switch_tripped():
            return OrderResult(False, simulated=True, error="kill switch tripped")
        if not self.is_live():
            logger.info("[DRY RUN] on-chain tx not broadcast: %s", {k: tx.get(k) for k in ("to", "value")})
            return OrderResult(True, simulated=True)
        try:
            from eth_account import Account
            from web3 import Web3
        except ImportError:
            return OrderResult(False, simulated=False,
                               error="web3/eth-account not installed")
        try:
            w3 = Web3(Web3.HTTPProvider(self.rpc_url))
            account = Account.from_key(self.private_key)
            tx.setdefault("from", account.address)
            tx.setdefault("chainId", 8453)
            tx.setdefault("nonce", w3.eth.get_transaction_count(account.address))
            tx.setdefault("gasPrice", w3.eth.gas_price)
            tx["gas"] = tx.get("gas") or w3.eth.estimate_gas(tx)
            signed = account.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            return OrderResult(True, simulated=False, order_id=tx_hash.hex())
        except Exception as exc:  # noqa: BLE001
            logger.exception("on-chain send failed")
            return OrderResult(False, simulated=False, error=str(exc))
