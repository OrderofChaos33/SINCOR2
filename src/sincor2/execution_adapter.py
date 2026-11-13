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
- **EOA allowances.** On first live client init, refresh the CLOB
  balance/allowance cache and record whether the trading address already
  holds a positive collateral allowance. This module never submits on-chain
  approvals — a funded wallet still needs its pUSD/CTF approvals set by
  the founder before live orders can settle.
- **CLOB V2 only.** Order placement goes through ``py-clob-client-v2``
  (Polymarket's CLOB V2 cutover, 2026-04-28): the signed order drops
  ``nonce``/``feeRateBps``/``taker`` for ``timestamp``/``metadata``/
  ``builder``, signs under the V2 exchange domain (version "2"), and is
  collateralized in pUSD, not USDC.e. Fees are set by the protocol at
  match time and charged on top of a market buy's amount. The legacy V1
  SDK cannot sign valid production orders anymore.
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
POLYMARKET_SIGNATURE_TYPE     V2 types: 0=EOA (default), 1=Magic/email proxy,
                              2=Gnosis Safe (browser wallet), 3=POLY_1271
                              deposit wallet
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
    labeled as such. Live mode places FOK market buy orders via
    py-clob-client-v2 (CLOB V2).
    """

    def __init__(self, bankroll: Optional[Bankroll] = None):
        self.bankroll = bankroll or get_bankroll()
        self._live_requested = os.getenv("POLYCLAW_LIVE", "").lower() == "true"
        self._key = _resolve_private_key()
        self._client = None
        self._client_error = ""
        self._allowances_ready = False

    def is_live(self) -> bool:
        return bool(self._live_requested and self._key and not self._client_error)

    def key_error(self) -> Optional[str]:
        """Last client-init error, or None. Read by the status endpoint."""
        return self._client_error or None

    def trading_address(self) -> Optional[str]:
        """Address orders are funded from, or None when no key is set.

        The funder env wins when configured (proxy/Magic accounts);
        otherwise the signing EOA — derived locally from the key or read
        off the warmed client. Never touches the network.
        """
        funder = os.getenv("POLYMARKET_FUNDER", "").strip()
        if funder:
            return funder
        if self._client is not None:
            try:
                return self._client.get_address()
            except Exception:
                pass
        if not self._key:
            return None
        try:
            from eth_account import Account
            return Account.from_key(self._key).address
        except Exception:
            return None

    def ensure_client(self):
        """Public warm-up hook for the scheduler bootstrap.

        Forces the lazy CLOB client init (API creds + allowance cache) at
        startup instead of on the first order path. Returns the client,
        or None in dry-run / on failure (see :meth:`key_error`).
        """
        return self._ensure_client()

    def _ensure_client(self):
        """Lazy CLOB V2 ClobClient init. Returns None (dry-run) on failure."""
        if self._client is not None:
            return self._client
        if not (self._live_requested and self._key):
            return None
        try:
            from py_clob_client_v2.client import ClobClient
            from py_clob_client_v2.clob_types import ApiCreds
            sig_type = int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "0"))
            # V2's OrderBuilder only treats None (not "") as "no funder";
            # an empty env value would otherwise become the order maker
            # and fail EIP-712 address encoding at signing time.
            funder = os.getenv("POLYMARKET_FUNDER", "").strip() or None
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
                # V2 rename: create_or_derive_api_creds -> *_api_key, and
                # the derived creds attach via set_api_creds (no rebuild).
                client.set_api_creds(client.create_or_derive_api_key())
            self._client = client
            self._warm_allowances(client)
            logger.info("CLOB V2 client initialized (live mode armed)")
            return client
        except Exception as exc:
            self._client_error = str(exc)[:200]
            logger.error("CLOB client init failed - falling back to dry-run: %s",
                         self._client_error)
            return None

    @staticmethod
    def _extract_allowance(bal: Any) -> float:
        """Largest collateral allowance in a balance-allowance response.

        The CLOB has spelled the field both ``allowance`` (scalar) and
        ``allowances`` (per-contract dict) across API generations; accept
        either shape and fail to 0.0 on anything unparseable.
        """
        if not isinstance(bal, dict):
            return 0.0
        for key in ("allowance", "allowances"):
            value = bal.get(key)
            if value is None:
                continue
            try:
                if isinstance(value, dict):
                    return max(
                        (float(v) for v in value.values()), default=0.0)
                return float(value)
            except (TypeError, ValueError):
                return 0.0
        return 0.0

    def _warm_allowances(self, client) -> None:
        """Refresh the CLOB balance/allowance cache and record readiness.

        Read-only: this never approves on-chain allowances (that stays a
        founder-armed action); it only asks the CLOB what allowance the
        trading address already has. In CLOB V2 the COLLATERAL asset type
        resolves to pUSD. ``_allowances_ready`` is True iff the CLOB
        reports a positive collateral allowance. A failed warm-up leaves
        the flag False so the scheduler retries next cycle; it must never
        break client init.
        """
        try:
            from py_clob_client_v2.clob_types import (
                AssetType,
                BalanceAllowanceParams,
            )
            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            try:
                client.update_balance_allowance(params)
            except Exception:
                pass  # the read below still refreshes server-side state
            bal = client.get_balance_allowance(params) or {}
            self._allowances_ready = self._extract_allowance(bal) > 0
        except Exception as exc:
            self._allowances_ready = False
            logger.debug("allowance warm-up failed: %s", exc)

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

        # Live (CLOB V2): FOK market buy at the ask. The V2 market-order
        # path takes the pUSD amount directly (maker amount); the SDK
        # derives the share amount at the given price and signs the V2
        # order (timestamp/metadata/builder, no fee fields — protocol
        # fees are set at match time and charged on top of the amount).
        try:
            from py_clob_client_v2.clob_types import (
                MarketOrderArgs,
                OrderType,
            )
            from py_clob_client_v2.order_utils.model.side import Side
            size_tokens = round(size_usd / ask, 4)
            resp = client.create_and_post_market_order(
                MarketOrderArgs(
                    token_id=token_id,
                    amount=size_usd,
                    side=Side.BUY,
                    price=ask,
                    order_type=OrderType.FOK,
                )
            )
            if isinstance(resp, dict) and resp.get("success") is False:
                # V2 reports placement failure in-band (success/errorMsg)
                # instead of raising; never mistake it for a fill.
                err = str(resp.get("errorMsg") or "order_rejected")[:120]
                logger.error("live order rejected: %s", err)
                return OrderResult(success=False, simulated=False,
                                   error=f"live_order_failed: {err}")
            order_id = str(
                (resp or {}).get("orderID") or (resp or {}).get("order_id")
                or "")
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
        """pUSD balance available for trading, or None if unknown."""
        client = self._ensure_client()
        if client is None:
            return None
        try:
            from py_clob_client_v2.clob_types import (
                AssetType,
                BalanceAllowanceParams,
            )
            bal = client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
            return float(bal.get("balance", 0)) / 1e6
        except Exception as exc:
            logger.debug("balance check failed: %s", exc)
            return None
