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
POLYCLAW_LIVE                 "true" to allow real orders (default: false)
POLYMARKET_PRIVATE_KEY        Polygon EOA key for the CLOB (hex, 0x-prefixed)
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
from typing import Any, Dict, List, Optional

from sincor2.bankroll import Bankroll, get_bankroll

logger = logging.getLogger("sincor.execution")

POLYMARKET_HOST = os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com")
POLYGON_CHAIN_ID = 137
HALT_FILE = Path(os.getenv("POLYCLAW_HALT_FILE", "/data/POLYCLAW_HALT"))
POLYGON_RPC_URL = os.getenv(
    "POLYGON_RPC_URL",
    os.getenv("POLYGON_RPC", "https://polygon-bor.publicnode.com"),
)

# Polymarket Polygon mainnet contracts (EOA trading)
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK_CTF_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"

MAX_UINT256 = 2**256 - 1

_ERC20_ABI = [
    {
        "name": "allowance",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "approve",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "balanceOf",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]

_ERC1155_ABI = [
    {
        "name": "isApprovedForAll",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "account", "type": "address"},
            {"name": "operator", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "setApprovalForAll",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "operator", "type": "address"},
            {"name": "approved", "type": "bool"},
        ],
        "outputs": [],
    },
]


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
            HALT_FILE.write_text(
                f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {reason}\n"
            )
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
        self._address: Optional[str] = None
        self._allowances_ready = False

    # ------------------------------------------------------------------
    # Client lifecycle
    # ------------------------------------------------------------------

    def is_live(self) -> bool:
        return (
            os.getenv("POLYCLAW_LIVE", "false").lower() == "true"
            and self._credentials_present()
        )

    @staticmethod
    def _credentials_present() -> bool:
        return bool(os.getenv("POLYMARKET_PRIVATE_KEY", "").strip())

    def trading_address(self) -> Optional[str]:
        """EOA address derived from POLYMARKET_PRIVATE_KEY (no key material)."""
        if self._address:
            return self._address
        pk = os.getenv("POLYMARKET_PRIVATE_KEY", "").strip()
        if not pk:
            return None
        try:
            from eth_account import Account

            if not pk.startswith("0x"):
                pk = "0x" + pk
            self._address = Account.from_key(pk).address
            return self._address
        except Exception as exc:
            logger.warning("could not derive trading address: %s", exc)
            return None

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
        if not private_key.startswith("0x"):
            private_key = "0x" + private_key

        try:
            from py_clob_client.client import ClobClient
        except ImportError:
            self._client_error = (
                "py-clob-client not installed (pip install py-clob-client)"
            )
            raise LiveTradingNotEnabled(self._client_error)

        try:
            # 0 = EOA (MetaMask / raw key). Required for 0xdba… style wallets.
            sig_type = int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "0"))
            funder = os.getenv("POLYMARKET_FUNDER", "").strip() or None
            client = ClobClient(
                POLYMARKET_HOST,
                key=private_key,
                chain_id=POLYGON_CHAIN_ID,
                signature_type=sig_type,
                funder=funder,
            )
            api_key = os.getenv("POLYMARKET_API_KEY", "").strip()
            api_secret = os.getenv("POLYMARKET_API_SECRET", "").strip()
            api_passphrase = os.getenv("POLYMARKET_API_PASSPHRASE", "").strip()
            if api_key and api_secret and api_passphrase:
                from py_clob_client.clob_types import ApiCreds

                client.set_api_creds(
                    ApiCreds(
                        api_key=api_key,
                        api_secret=api_secret,
                        api_passphrase=api_passphrase,
                    )
                )
            else:
                client.set_api_creds(client.create_or_derive_api_creds())

            addr = self.trading_address()
            logger.info(
                "Polymarket CLOB client initialised (live) address=%s sig_type=%s funder=%s",
                addr,
                sig_type,
                funder or addr,
            )

            # One-time on-chain approvals + CLOB cache refresh.
            self._ensure_allowances(client, private_key)

            self._client = client
            return client
        except LiveTradingNotEnabled:
            raise
        except Exception as exc:  # noqa: BLE001
            self._client_error = f"CLOB client init failed: {exc}"
            raise LiveTradingNotEnabled(self._client_error) from exc

    # ------------------------------------------------------------------
    # On-chain allowances (EOA requirement)
    # ------------------------------------------------------------------

    def _ensure_allowances(self, client: Any, private_key: str) -> None:
        """Approve USDC.e + CTF for Polymarket exchanges if not already set.

        Without these approvals the CLOB rejects spends even when the wallet
        holds USDC.e. Idempotent: skips spenders that already have allowance.
        """
        if self._allowances_ready:
            return

        try:
            from eth_account import Account
            from web3 import Web3
        except ImportError as exc:
            raise LiveTradingNotEnabled(
                "web3/eth-account required for live allowances "
                "(pip install web3 eth-account)"
            ) from exc

        w3 = Web3(Web3.HTTPProvider(POLYGON_RPC_URL, request_kwargs={"timeout": 30}))
        if not w3.is_connected():
            raise LiveTradingNotEnabled(
                f"cannot reach Polygon RPC: {POLYGON_RPC_URL}"
            )

        account = Account.from_key(private_key)
        owner = Web3.to_checksum_address(account.address)
        self._address = owner

        pol_bal = float(w3.from_wei(w3.eth.get_balance(owner), "ether"))
        if pol_bal < 0.01:
            raise LiveTradingNotEnabled(
                f"insufficient POL for gas on {owner}: {pol_bal:.6f} POL"
            )

        usdc = w3.eth.contract(
            address=Web3.to_checksum_address(USDC_E), abi=_ERC20_ABI
        )
        ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF), abi=_ERC1155_ABI)

        usdc_bal = usdc.functions.balanceOf(owner).call() / 1e6
        logger.info("live wallet %s | POL=%.4f USDC.e=%.4f", owner, pol_bal, usdc_bal)
        if usdc_bal < 1.0:
            logger.warning(
                "USDC.e balance $%.4f is below $1 Polymarket minimum on %s",
                usdc_bal,
                owner,
            )

        # USDC.e must be approved to CTF + both exchanges + neg-risk adapter.
        usdc_spenders = [
            CTF,
            CTF_EXCHANGE,
            NEG_RISK_CTF_EXCHANGE,
            NEG_RISK_ADAPTER,
        ]
        # Outcome tokens must be operator-approved to both exchanges + adapter.
        ctf_operators = [
            CTF_EXCHANGE,
            NEG_RISK_CTF_EXCHANGE,
            NEG_RISK_ADAPTER,
        ]

        nonce = w3.eth.get_transaction_count(owner)
        sent = 0

        def _send(built_tx: Dict[str, Any]) -> str:
            nonlocal nonce, sent
            built_tx.setdefault("from", owner)
            built_tx.setdefault("nonce", nonce)
            built_tx.setdefault("chainId", POLYGON_CHAIN_ID)
            # Legacy gasPrice is fine on Polygon for simple approves.
            if "gasPrice" not in built_tx:
                built_tx["gasPrice"] = int(w3.eth.gas_price * 1.1)
            if "gas" not in built_tx:
                built_tx["gas"] = int(w3.eth.estimate_gas(built_tx) * 1.2)
            signed = account.sign_transaction(built_tx)
            raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
            tx_hash = w3.eth.send_raw_transaction(raw)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
            if receipt.status != 1:
                raise RuntimeError(f"approve tx reverted: {tx_hash.hex()}")
            nonce += 1
            sent += 1
            return tx_hash.hex()

        for spender in usdc_spenders:
            spender_cs = Web3.to_checksum_address(spender)
            current = usdc.functions.allowance(owner, spender_cs).call()
            if current >= 10**12:  # already effectively unlimited for our sizes
                continue
            logger.info("approving USDC.e for %s ...", spender_cs)
            tx = usdc.functions.approve(spender_cs, MAX_UINT256).build_transaction(
                {"from": owner}
            )
            txh = _send(tx)
            logger.info("USDC.e approve %s tx=%s", spender_cs, txh)

        for operator in ctf_operators:
            op_cs = Web3.to_checksum_address(operator)
            if ctf.functions.isApprovedForAll(owner, op_cs).call():
                continue
            logger.info("setApprovalForAll CTF operator %s ...", op_cs)
            tx = ctf.functions.setApprovalForAll(op_cs, True).build_transaction(
                {"from": owner}
            )
            txh = _send(tx)
            logger.info("CTF setApprovalForAll %s tx=%s", op_cs, txh)

        # Force CLOB to re-read on-chain collateral allowance/balance.
        try:
            from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

            client.update_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            bal = client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            logger.info("CLOB collateral balance/allowance cache: %s", bal)
        except Exception as exc:
            # Approvals may still be valid on-chain; log and continue.
            logger.warning("CLOB update_balance_allowance failed: %s", exc)

        self._allowances_ready = True
        logger.info(
            "allowances ready for %s (new txs=%d)", owner, sent
        )

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
    def get_price_history(
        token_id: str, interval: str = "1d", fidelity: int = 60
    ) -> List[Dict[str, Any]]:
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
        Ledger exposure is recorded by the caller via ``bankroll.record_trade``.
        """
        if kill_switch_tripped():
            return OrderResult(
                False, simulated=True, token_id=token_id, error="kill switch tripped"
            )

        if not self.bankroll.can_open(usd_amount):
            return OrderResult(
                False,
                simulated=True,
                token_id=token_id,
                size_usd=usd_amount,
                error="bankroll cap reached or risk limit hit",
            )

        if not self.is_live():
            logger.info(
                "[DRY RUN] market buy %.2f USD of token %s "
                "(set POLYCLAW_LIVE=true + POLYMARKET_PRIVATE_KEY to go live)",
                usd_amount,
                token_id,
            )
            return OrderResult(
                True,
                simulated=True,
                token_id=token_id,
                side="BUY",
                size_usd=usd_amount,
                filled_usd=usd_amount,
            )

        try:
            from py_clob_client.clob_types import MarketOrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY
        except ImportError:
            return OrderResult(
                False, simulated=False, token_id=token_id, error="py-clob-client not installed"
            )

        try:
            client = self._get_client()
            # Keep CLOB collateral cache warm before sizing-sensitive FOK.
            try:
                from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

                client.update_balance_allowance(
                    BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
                )
            except Exception as exc:
                logger.debug("pre-order allowance refresh skipped: %s", exc)

            order_args = MarketOrderArgs(
                token_id=token_id,
                amount=usd_amount,  # USDC notional for market buys
                side=BUY,
            )
            signed = client.create_market_order(order_args)
            resp = client.post_order(signed, OrderType.FOK)  # fill-or-kill
            if not isinstance(resp, dict):
                resp = {"raw": resp}
            order_id = resp.get("orderID") or resp.get("id") or resp.get("order_id")
            filled = float(resp.get("makingAmount", 0) or resp.get("takingAmount", 0) or 0)
            status = str(resp.get("status", "")).lower()
            success = bool(order_id) and status in (
                "matched",
                "filled",
                "live",
                "delayed",
                "unmatched",
            )
            # FOK should be matched/filled; treat explicit failure codes as miss.
            if status in ("killed", "cancelled", "canceled", "rejected"):
                success = False
            if success:
                logger.info(
                    "[LIVE] market buy $%.2f token=%s order=%s status=%s",
                    usd_amount,
                    token_id,
                    order_id,
                    status,
                )
            else:
                logger.warning("[LIVE] order not matched: %s", resp)
            return OrderResult(
                success,
                simulated=False,
                order_id=order_id,
                token_id=token_id,
                side="BUY",
                size_usd=usd_amount,
                filled_usd=filled if success else 0.0,
                raw=resp,
                error=None if success else f"not matched: {status or resp}",
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[LIVE] market buy failed")
            return OrderResult(
                False,
                simulated=False,
                token_id=token_id,
                side="BUY",
                size_usd=usd_amount,
                error=str(exc),
            )

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
        return (
            os.getenv("POLYCLAW_LIVE", "false").lower() == "true"
            and bool(self.private_key)
        )

    def send_raw(self, tx: Dict[str, Any]) -> OrderResult:
        """Sign and broadcast a prepared transaction dict. Dry-run by default."""
        if kill_switch_tripped():
            return OrderResult(False, simulated=True, error="kill switch tripped")
        if not self.is_live():
            logger.info(
                "[DRY RUN] on-chain tx not broadcast: %s",
                {k: tx.get(k) for k in ("to", "value")},
            )
            return OrderResult(True, simulated=True)
        try:
            from eth_account import Account
            from web3 import Web3
        except ImportError:
            return OrderResult(
                False, simulated=False, error="web3/eth-account not installed"
            )
        try:
            w3 = Web3(Web3.HTTPProvider(self.rpc_url))
            account = Account.from_key(self.private_key)
            tx.setdefault("from", account.address)
            tx.setdefault("chainId", 8453)
            tx.setdefault("nonce", w3.eth.get_transaction_count(account.address))
            tx.setdefault("gasPrice", w3.eth.gas_price)
            tx["gas"] = tx.get("gas") or w3.eth.estimate_gas(tx)
            signed = account.sign_transaction(tx)
            raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
            tx_hash = w3.eth.send_raw_transaction(raw)
            return OrderResult(True, simulated=False, order_id=tx_hash.hex())
        except Exception as exc:  # noqa: BLE001
            logger.exception("on-chain send failed")
            return OrderResult(False, simulated=False, error=str(exc))
