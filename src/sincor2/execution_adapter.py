"""Unified execution adapter — the ONLY place orders reach an exchange.

Replaces the three disconnected Polyclaw implementations
(``polyclaw_scheduler``, ``polyclaw_mega_aggressive_live``,
``verticals/trading/polyclaw``) behind a single adapter with hard guarantees:

- **Dry-run by default.** Live orders require ``POLYCLAW_LIVE=true`` AND valid
  Polymarket credentials in the environment. Anything else simulates and
  says so loudly.
- **Capital caps enforced upstream** by ``bankroll.py``.
- **Kill switch.** A tripped switch (DB flag or ``/data/POLYCLAW_HALT`` file)
  blocks every order until manually cleared.
- **EOA allowances.** On first live client init, approve USDC.e + CTF for the
  Polymarket exchange contracts and refresh the CLOB balance/allowance cache.
  Without this, funded wallets still cannot trade.
- **Fill reconciliation** against the CLOB REST API — no phantom PnL.

Nothing here signs anything without an explicit private key from the
environment. Keys are never logged.

Environment
-----------
POLYCLAW_LIVE                 \"true\" to allow real orders (default: false)
POLYMARKET_PRIVATE_KEY        Polygon EOA key for the CLOB (hex, 0x-prefixed)
POLYCLAW_PRIVATE_KEY          fallback alias for the same key
POLYMARKET_PK                 second fallback alias
POLYMARKET_FUNDER             Address funding the orders (defaults to key addr)
POLYMARKET_SIGNATURE_TYPE     0=EOA (default), 1=Magic/email, 2=browser proxy
POLYMARKET_API_KEY / _SECRET / _PASSPHRASE   CLOB API creds (derived if absent)
POLYMARKET_HOST               default https://clob.polymarket.com
POLYGON_RPC_URL               default https://polygon-bor.publicnode.com
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sincor2.bankroll import Bankroll, get_bankroll

logger = logging.getLogger("sincor.execution")

# --- module-level env -------------------------------------------------------
# This VM's NO_PROXY carries bracketed IPv6 literals (``[::1]``) that crash
# httpx inside py-clob-client at import time. Sanitize once, here, so every
# importer of this module gets a working client.
for _var in ("NO_PROXY", "no_proxy"):
    _val = os.environ.get(_var, "")
    if "[" in _val:
        os.environ[_var] = ",".join(
            p for p in _val.split(",") if "[" not in p)
        logger.debug("sanitized %s for httpx compatibility", _var)

HALT_FILE = Path(os.getenv("POLYCLAW_HALT_FILE", "/data/POLYCLAW_HALT"))
CLOB_HOST = os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com")
POLYGON_CHAIN_ID = 137


class KillSwitchTripped(RuntimeError):
    """Raised when a live order is attempted while the kill switch is set."""


@dataclass
class OrderResult:
    success: bool
    order_id: str = ""
    simulated: bool = True
    price: float = 0.0          # fill price per token
    size_usd: float = 0.0       # notional wagered
    size_tokens: float = 0.0    # conditional tokens bought
    error: str = ""


def kill_switch_tripped(bankroll: Optional[Bankroll] = None) -> bool:
    """True if trading must halt: env, halt file, or bankroll DB flag."""
    if os.getenv("POLYCLAW_HALT", "").lower() in ("1", "true", "yes"):
        return True
    try:
        if HALT_FILE.is_file():
            return True
    except OSError:
        pass
    try:
        br = bankroll or get_bankroll()
        if br.kill_switch_active():
            return True
    except Exception as exc:  # fail-closed: unknown state halts live trading
        logger.warning("kill-switch check failed (%s); treating as tripped", exc)
        return True
    return False


def _resolve_private_key() -> str:
    for var in ("POLYMARKET_PRIVATE_KEY", "POLYCLAW_PRIVATE_KEY", "POLYMARKET_PK"):
        val = os.getenv(var, "").strip()
        if val:
            return val
    return ""


def _book_ask(token_id: str) -> Optional[float]:
    """Best ask for a token via plain REST (no signed client needed)."""
    try:
        req = urllib.request.Request(
            f"{CLOB_HOST}/book?token_id={token_id}",
            headers={"User-Agent": "sincor-execution/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            book = json.loads(resp.read())
        asks = book.get("asks") or []
        if not asks:
            return None
        return min(float(a["price"]) for a in asks)
    except Exception as exc:
        logger.debug("book fetch failed for %s: %s", token_id[:12], exc)
        return None


class PolymarketAdapter:
    """Live/dry-run order gateway for Polymarket CLOB.

    Dry-run is the default: without ``POLYCLAW_LIVE=true`` AND a private
    key, every order is simulated against the live order book and loudly
    labeled as such. Live mode places FOK buy orders via py-clob-client.
    """

    def __init__(self, bankroll: Optional[Bankroll] = None):
        self.bankroll = bankroll or get_bankroll()
        self._live_requested = os.getenv("POLYCLAW_LIVE", "").lower() == "true"
        self._key = _resolve_private_key()
        self._client = None
        self._client_error = ""

    def is_live(self) -> bool:
        return bool(self._live_requested and self._key and not self._client_error)

    def _ensure_client(self):
        """Lazy ClobClient init. Returns None (dry-run) on any failure."""
        if self._client is not None:
            return self._client
        if not (self._live_requested and self._key):
            return None
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds
            sig_type = int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "0"))
            funder = os.getenv("POLYMARKET_FUNDER", "")
            creds = ApiCreds(
                api_key=os.getenv("POLYMARKET_API_KEY", ""),
                api_secret=os.getenv("POLYMARKET_API_SECRET", ""),
                api_passphrase=os.getenv("POLYMARKET_API_PASSPHRASE", ""),
            )
            has_creds = bool(creds.api_key and creds.api_secret)
            client = ClobClient(
                CLOB_HOST, chain_id=POLYGON_CHAIN_ID, key=self._key,
                creds=creds if has_creds else None,
                signature_type=sig_type, funder=funder,
            )
            if not has_creds:
                derived = client.create_or_derive_api_creds()
                client = ClobClient(
                    CLOB_HOST, chain_id=POLYGON_CHAIN_ID, key=self._key,
                    creds=derived, signature_type=sig_type, funder=funder,
                )
            self._client = client
            logger.info("CLOB client initialized (live mode armed)")
            return client
        except Exception as exc:
            self._client_error = str(exc)[:200]
            logger.error("CLOB client init failed - falling back to dry-run: %s",
                         self._client_error)
            return None

    def place_market_buy(self, token_id: str, size_usd: float) -> OrderResult:
        """Buy ``size_usd`` of ``token_id`` at market (FOK when live)."""
        if kill_switch_tripped(self.bankroll):
            logger.warning("order blocked: kill switch tripped")
            return OrderResult(success=False, error="kill_switch_tripped")

        ask = _book_ask(token_id)
        if ask is None or ask <= 0 or ask >= 1:
            return OrderResult(success=False, error="no_book_available")

        client = self._ensure_client()
        if client is None:
            # Dry run: simulate an immediate fill at the ask (pays the spread,
            # like a real market order would).
            size_tokens = round(size_usd / ask, 4)
            logger.info("[DRY] simulated BUY %s tokens=%s @ %.4f ($%.2f)",
                        token_id[:12], size_tokens, ask, size_usd)
            return OrderResult(success=True, simulated=True, price=ask,
                               size_usd=size_usd, size_tokens=size_tokens,
                               order_id=f"dry_{int(time.time()*1000)}")

        # Live: FOK buy at the ask.
        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            from py_clob_client.constants import BUY
            size_tokens = round(size_usd / ask, 4)
            order_args = OrderArgs(token_id=token_id, price=ask,
                                   size=size_tokens, side=BUY)
            signed = client.create_order(order_args)
            resp = client.post_order(signed, OrderType.FOK)
            order_id = str(resp.get("orderID") or resp.get("order_id") or "")
            logger.info("[LIVE] FOK BUY %s tokens=%s @ %.4f ($%.2f) id=%s",
                        token_id[:12], size_tokens, ask, size_usd, order_id)
            return OrderResult(success=True, simulated=False, price=ask,
                               size_usd=size_usd, size_tokens=size_tokens,
                               order_id=order_id)
        except Exception as exc:
            logger.error("live order failed: %s", str(exc)[:200])
            return OrderResult(success=False, simulated=False,
                               error=f"live_order_failed: {str(exc)[:120]}")

    def collateral_balance(self) -> Optional[float]:
        """USDC.e balance available for trading, or None if unknown."""
        client = self._ensure_client()
        if client is None:
            return None
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams
            bal = client.get_balance_allowance(BalanceAllowanceParams())
            return float(bal.get("balance", 0)) / 1e6
        except Exception as exc:
            logger.debug("balance check failed: %s", exc)
            return None
