from __future__ import annotations

"""SINC token access management, gating decorator, and metered usage logging.

This module provides:
- ``SINCAccessManager``: reads on-chain SINC balances and platform contract state
  via a read-only Base RPC call (no gas required).
- ``sinc_required``: Flask view decorator that enforces minimum SINC balance or
  staked amount before allowing access.  Returns HTTP 402 on failure.
- ``SINCMeter``: append-only usage log for tracking SINC debits/credits per wallet.

On-chain calls use ``eth_call`` with the standard ERC-20 ``balanceOf(address)``
selector and the custom ``SINCPlatformAccess`` contract ABI.  All RPC reads are
cached for 15 seconds per wallet to avoid hammering the RPC endpoint.

Environment variables
---------------------
BASE_RPC_URL                  : Base mainnet JSON-RPC endpoint (default: Cloudflare)
SINC_CONTRACT_ADDRESS         : SINC ERC-20 contract address
SINC_PLATFORM_ACCESS_ADDRESS  : Deployed SINCPlatformAccess contract address
"""

import functools
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from flask import current_app, jsonify, request

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SINC_CONTRACT = os.getenv(
    "SINC_CONTRACT_ADDRESS",
    "0x9C8cd8d3961F445D653713dE65C6578bE11668e7",
)
SINC_PLATFORM_ACCESS = os.getenv(
    "SINC_PLATFORM_ACCESS_ADDRESS",
    "",  # Empty until deployed; access checks degrade gracefully
)
TREASURY_ADDRESS = os.getenv(
    "TREASURY_ADDRESS",
    "0xAf9B539D8043C634b7E611818518BA7E850F289e",
)
DEFAULT_BASE_RPC = "https://mainnet.base.org"

# ABI selectors (keccak256 first 4 bytes)
_BALANCE_OF_SELECTOR = "0x70a08231"  # balanceOf(address)
_CREDITS_SELECTOR = "0x59e02dd7"    # credits(address)  — SINCPlatformAccess
_STAKED_SELECTOR = "0x0ffd28f3"     # staked(address)   — SINCPlatformAccess

# Cache TTL in seconds
_CACHE_TTL = 15

# Minimum SINC tiers (whole tokens; decimals=0)
TIER_ADVANCED_HOLD = int(os.getenv("SINC_TIER_ADVANCED_HOLD", "500"))
TIER_LIST_STAKE = int(os.getenv("SINC_TIER_LIST_STAKE", "250"))
TIER_PRIORITY_STAKE = int(os.getenv("SINC_TIER_PRIORITY_STAKE", "1000"))
TIER_ENTERPRISE_STAKE = int(os.getenv("SINC_TIER_ENTERPRISE_STAKE", "5000"))
COST_PER_AGENT_CALL = int(os.getenv("SINC_COST_PER_CALL", "1"))
COST_PER_SWARM_HOUR = int(os.getenv("SINC_COST_PER_SWARM_HOUR", "10"))
COST_LISTING_FEE = int(os.getenv("SINC_LISTING_FEE", "50"))


# ---------------------------------------------------------------------------
# Wallet address encoding helpers
# ---------------------------------------------------------------------------

def _encode_address(address: str) -> str:
    """Encode an Ethereum address as a 32-byte ABI parameter (zero-padded)."""
    addr = address.lower().replace("0x", "")
    return "0" * (64 - len(addr)) + addr


def _decode_uint256(hex_result: str) -> int:
    """Decode a hex ``eth_call`` result to a Python int."""
    value = hex_result.strip()
    if value in ("", "0x", "0x0"):
        return 0
    return int(value, 16)


def _normalize_wallet(wallet: str) -> str:
    """Return a checksum-free, lower-cased wallet address or raise ValueError."""
    addr = wallet.strip().lower()
    if not addr.startswith("0x") or len(addr) != 42:
        raise ValueError(f"Invalid wallet address: {wallet!r}")
    return addr


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

@dataclass
class _CacheEntry:
    value: int
    expires_at: float


class _TTLCache:
    """Simple thread-safe TTL cache for integer RPC results."""

    def __init__(self, ttl: float = _CACHE_TTL) -> None:
        self._ttl = ttl
        self._store: Dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[int]:
        with self._lock:
            entry = self._store.get(key)
            if entry and time.monotonic() < entry.expires_at:
                return entry.value
        return None

    def set(self, key: str, value: int) -> None:
        with self._lock:
            self._store[key] = _CacheEntry(
                value=value, expires_at=time.monotonic() + self._ttl
            )

    def invalidate(self, prefix: str) -> None:
        """Remove all entries whose key starts with prefix."""
        with self._lock:
            keys = [k for k in self._store if k.startswith(prefix)]
            for k in keys:
                del self._store[k]


# ---------------------------------------------------------------------------
# SINCAccessManager
# ---------------------------------------------------------------------------

class SINCAccessManager:
    """Reads on-chain SINC state via the Base JSON-RPC interface.

    All methods are safe to call concurrently and cache results for
    ``_CACHE_TTL`` seconds to avoid excessive RPC load.
    """

    def __init__(self, rpc_url: Optional[str] = None) -> None:
        rpc = rpc_url or os.getenv("BASE_RPC_URL") or DEFAULT_BASE_RPC
        if rpc and not rpc.startswith("https://") and not rpc.startswith("http://localhost") and not rpc.startswith("http://127."):
            logger.warning("BASE_RPC_URL does not use HTTPS — on-chain reads may be vulnerable to MITM attacks")
        self._rpc_url = rpc
        self._cache = _TTLCache()

    # ------------------------------------------------------------------
    # Internal RPC helpers
    # ------------------------------------------------------------------

    def _eth_call(
        self,
        to: str,
        data: str,
        timeout: int = 6,
        retries: int = 2,
    ) -> int:
        """Perform an ``eth_call`` and return the decoded uint256 result."""
        payload = json.dumps({
            "jsonrpc": "2.0",
            "method": "eth_call",
            "params": [{"to": to, "data": data}, "latest"],
            "id": 1,
        }).encode()
        req = urllib.request.Request(
            self._rpc_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        for attempt in range(retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                    body = json.loads(resp.read())
                result = body.get("result", "0x0")
                return _decode_uint256(result)
            except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError) as exc:
                if attempt < retries:
                    time.sleep(0.3 * (attempt + 1))
                    continue
                logger.warning("SINC RPC call failed (to=%s): %s", to, exc)
                return 0  # Fail-open with 0 — access checks use ≥ threshold
        return 0  # unreachable but satisfies type checker

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_balance(self, wallet: str) -> int:
        """Return the on-chain SINC balance for *wallet* (whole tokens)."""
        try:
            wallet = _normalize_wallet(wallet)
        except ValueError:
            return 0
        key = f"balance:{wallet}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        data = _BALANCE_OF_SELECTOR + _encode_address(wallet)
        value = self._eth_call(SINC_CONTRACT, data)
        self._cache.set(key, value)
        return value

    def get_credits(self, wallet: str) -> int:
        """Return the on-chain prepaid credit balance from SINCPlatformAccess."""
        if not SINC_PLATFORM_ACCESS:
            return 0
        try:
            wallet = _normalize_wallet(wallet)
        except ValueError:
            return 0
        key = f"credits:{wallet}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        data = _CREDITS_SELECTOR + _encode_address(wallet)
        value = self._eth_call(SINC_PLATFORM_ACCESS, data)
        self._cache.set(key, value)
        return value

    def get_staked(self, wallet: str) -> int:
        """Return the on-chain staked SINC balance from SINCPlatformAccess."""
        if not SINC_PLATFORM_ACCESS:
            return 0
        try:
            wallet = _normalize_wallet(wallet)
        except ValueError:
            return 0
        key = f"staked:{wallet}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        data = _STAKED_SELECTOR + _encode_address(wallet)
        value = self._eth_call(SINC_PLATFORM_ACCESS, data)
        self._cache.set(key, value)
        return value

    def verify_minimum(self, wallet: str, min_sinc: int) -> bool:
        """Return True if the wallet holds ≥ min_sinc SINC (on-chain balance)."""
        if min_sinc <= 0:
            return True
        return self.get_balance(wallet) >= min_sinc

    def verify_staked(self, wallet: str, min_staked: int) -> bool:
        """Return True if the wallet has ≥ min_staked SINC staked on-chain."""
        if min_staked <= 0:
            return True
        return self.get_staked(wallet) >= min_staked

    def verify_credits(self, wallet: str, min_credits: int) -> bool:
        """Return True if the wallet has ≥ min_credits prepaid on-chain."""
        if min_credits <= 0:
            return True
        return self.get_credits(wallet) >= min_credits

    def invalidate_cache(self, wallet: str) -> None:
        """Evict all cached entries for *wallet* (call after known on-chain changes)."""
        try:
            wallet = _normalize_wallet(wallet)
        except ValueError:
            return
        self._cache.invalidate(f"balance:{wallet}")
        self._cache.invalidate(f"credits:{wallet}")
        self._cache.invalidate(f"staked:{wallet}")

    def get_full_status(self, wallet: str) -> Dict[str, Any]:
        """Return balance, credits, staked, and tier info for *wallet*."""
        balance = self.get_balance(wallet)
        credits = self.get_credits(wallet)
        staked = self.get_staked(wallet)
        tier = "none"
        if staked >= TIER_ENTERPRISE_STAKE:
            tier = "enterprise"
        elif staked >= TIER_PRIORITY_STAKE:
            tier = "priority"
        elif staked >= TIER_LIST_STAKE:
            tier = "standard"
        return {
            "wallet": wallet,
            "sinc_balance": balance,
            "credits": credits,
            "staked": staked,
            "tier": tier,
            "can_use_advanced": balance >= TIER_ADVANCED_HOLD,
            "can_list": staked >= TIER_LIST_STAKE,
            "staking_discount": staked >= TIER_PRIORITY_STAKE,
        }


# ---------------------------------------------------------------------------
# sinc_required decorator
# ---------------------------------------------------------------------------

def sinc_required(
    min_balance: int = 0,
    min_staked: int = 0,
    min_credits: int = 0,
) -> Callable:
    """Flask view decorator enforcing SINC token requirements.

    Checks the ``X-Wallet-Address`` request header against on-chain state.
    Returns ``402 Payment Required`` if the requirement is not met.

    Parameters
    ----------
    min_balance:  Minimum whole-SINC on-chain balance required (holding gate).
    min_staked:   Minimum whole-SINC staked in SINCPlatformAccess required.
    min_credits:  Minimum prepaid credits in SINCPlatformAccess required.

    Example
    -------
    .. code-block:: python

        @marketplace_bp.post("/register")
        @sinc_required(min_staked=250)
        def register_agent():
            ...
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            wallet = request.headers.get("X-Wallet-Address", "").strip()

            if not wallet:
                logger.info(
                    "sinc_required: missing wallet header endpoint=%s", request.path
                )
                return (
                    jsonify({
                        "error": "wallet_required",
                        "message": "Connect a wallet to use this feature.",
                        "required_balance": min_balance,
                        "required_staked": min_staked,
                        "required_credits": min_credits,
                    }),
                    402,
                )

            manager: Optional[SINCAccessManager] = current_app.extensions.get("sinc_access")
            if manager is None:
                # Graceful degradation: allow access if manager not configured
                logger.warning(
                    "sinc_required: SINCAccessManager not registered; skipping check"
                )
                return fn(*args, **kwargs)

            # Balance gate
            if min_balance > 0:
                current_balance = manager.get_balance(wallet)
                if current_balance < min_balance:
                    logger.info(
                        "sinc_required: insufficient balance wallet=%s required=%d current=%d endpoint=%s",
                        wallet, min_balance, current_balance, request.path,
                    )
                    return (
                        jsonify({
                            "error": "insufficient_sinc",
                            "message": f"This feature requires {min_balance} SINC in your wallet.",
                            "required": min_balance,
                            "current": current_balance,
                            "shortfall": min_balance - current_balance,
                            "buy_sinc_url": "/sinc",
                        }),
                        402,
                    )

            # Staking gate
            if min_staked > 0:
                current_staked = manager.get_staked(wallet)
                if current_staked < min_staked:
                    logger.info(
                        "sinc_required: insufficient staked wallet=%s required=%d current=%d endpoint=%s",
                        wallet, min_staked, current_staked, request.path,
                    )
                    return (
                        jsonify({
                            "error": "insufficient_sinc_staked",
                            "message": f"This feature requires {min_staked} SINC staked.",
                            "required_staked": min_staked,
                            "current_staked": current_staked,
                            "shortfall": min_staked - current_staked,
                            "buy_sinc_url": "/sinc",
                        }),
                        402,
                    )

            # Credits gate
            if min_credits > 0:
                current_credits = manager.get_credits(wallet)
                if current_credits < min_credits:
                    logger.info(
                        "sinc_required: insufficient credits wallet=%s required=%d current=%d endpoint=%s",
                        wallet, min_credits, current_credits, request.path,
                    )
                    return (
                        jsonify({
                            "error": "insufficient_credits",
                            "message": f"This action costs {min_credits} SINC credit(s). Purchase credits to continue.",
                            "required_credits": min_credits,
                            "current_credits": current_credits,
                            "shortfall": min_credits - current_credits,
                            "purchase_credits_url": "/sinc",
                        }),
                        402,
                    )

            logger.info(
                "sinc_required: access granted wallet=%s endpoint=%s", wallet, request.path
            )
            return fn(*args, **kwargs)

        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# SINCMeter — append-only usage log
# ---------------------------------------------------------------------------

@dataclass
class SINCUsageEvent:
    """A single SINC debit or credit event for observability."""

    wallet: str
    action_type: str
    sinc_amount: int
    task_id: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    direction: str = "debit"  # "debit" | "credit"
    tx_hash: Optional[str] = None


class SINCMeter:
    """Records SINC usage events to an in-process log and optional file.

    The file is an append-only newline-delimited JSON file.  If no path is
    configured the events are stored in memory only (suitable for ephemeral
    deployments or testing).
    """

    def __init__(self, log_path: Optional[str] = None) -> None:
        env_path = os.getenv("SINC_USAGE_LOG_PATH")
        self._log_path: Optional[Path] = (
            Path(log_path) if log_path else (Path(env_path) if env_path else None)
        )
        self._events: List[SINCUsageEvent] = []
        self._lock = threading.Lock()

    def record(
        self,
        wallet: str,
        action_type: str,
        sinc_amount: int,
        task_id: str = "",
        direction: str = "debit",
        tx_hash: Optional[str] = None,
    ) -> SINCUsageEvent:
        """Append a SINC usage event to the in-memory log and optional file."""
        event = SINCUsageEvent(
            wallet=wallet.lower(),
            action_type=action_type,
            sinc_amount=sinc_amount,
            task_id=task_id,
            direction=direction,
            tx_hash=tx_hash,
        )
        with self._lock:
            self._events.append(event)
            if self._log_path:
                self._append_to_file(event)
        logger.info(
            "sinc_meter: %s wallet=%s amount=%d action=%s task=%s",
            direction, wallet, sinc_amount, action_type, task_id,
        )
        return event

    def _append_to_file(self, event: SINCUsageEvent) -> None:
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
            with open(self._log_path, "a", encoding="utf-8") as fh:  # type: ignore[arg-type]
                fh.write(json.dumps(asdict(event)) + "\n")
        except OSError as exc:
            logger.warning("SINCMeter: could not write to log file: %s", exc)

    def get_events(
        self,
        wallet: Optional[str] = None,
        limit: int = 50,
        direction: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return the most recent usage events, optionally filtered."""
        with self._lock:
            events = list(self._events)
        if wallet:
            w = wallet.lower()
            events = [e for e in events if e.wallet == w]
        if direction:
            events = [e for e in events if e.direction == direction]
        return [asdict(e) for e in events[-limit:]]

    def total_spent(self, wallet: str) -> int:
        """Return total SINC debited for a wallet (in-memory log only)."""
        w = wallet.lower()
        with self._lock:
            return sum(
                e.sinc_amount for e in self._events if e.wallet == w and e.direction == "debit"
            )
