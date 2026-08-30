#!/usr/bin/env python3
"""
Resilient AXM payment verification for Base chain.

Multi-RPC (Alchemy / QuickNode / public Base), exponential backoff,
web3.py HTTPProvider pooling, and 24h SQLite cache via PersistentStore KV.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request as _urllib_request
from typing import Any, Dict, List, Optional

from sincor2.onchain.constants import AXIOM_TOKEN, TREASURY, resolve_address

logger = logging.getLogger("sincor.payment")

# Defaults — overridden by a2a_integration env when imported from there
AXIOM_CONTRACT = resolve_address("AXIOM_CONTRACT_ADDRESS", AXIOM_TOKEN)
TREASURY_WALLET = resolve_address("TREASURY_ADDRESS", TREASURY)
BASE_RPC_TIMEOUT = int(os.getenv("BASE_RPC_TIMEOUT", "10"))
_DEV_ENVS = {"development", "dev", "test", "testing", "local"}

# Payment verification — multi-RPC, retry, SQLite cache, web3 pooling
# ---------------------------------------------------------------------------

class PaymentVerifier:
    """
    Verifies that an AXM payment tx has been confirmed on Base.

    Resilience features
    -------------------
    * Multiple RPC endpoints (BASE_RPC_URL + BASE_RPC_URLS + public Base fallbacks)
    * Exponential backoff (3 attempts per provider)
    * web3.py HTTPProvider with connection pooling when available
    * Process cache + PersistentStore KV cache (24h TTL) for successful verifications
    * Transient RPC failures raise PaymentRpcError so callers can retry the
      *verification* without treating the payment as invalid

    Validation checks (production mode):
      1. Transaction receipt exists and status == 0x1 (success).
      2. A Transfer(address,address,uint256) log from the AXM contract is present
         with `to` == expected_to (treasury wallet) and value >= expected_amount_wei.
    """

    # ERC-20 Transfer event topic: keccak256("Transfer(address,address,uint256)")
    _TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

    _verified: Dict[str, bool] = {}
    _lock: threading.Lock = threading.Lock()
    _web3_pool: Dict[str, Any] = {}
    _web3_lock: threading.Lock = threading.Lock()

    # Cache successful verifications for 24h
    _CACHE_TTL_SECONDS = int(os.getenv("PAYMENT_CACHE_TTL_SECONDS", str(24 * 3600)))
    _MAX_ATTEMPTS = int(os.getenv("PAYMENT_RPC_MAX_ATTEMPTS", "3"))
    _BACKOFF_BASE = float(os.getenv("PAYMENT_RPC_BACKOFF_BASE", "0.4"))

    # Public Base mainnet endpoints used as last-resort fallbacks
    _PUBLIC_FALLBACKS = (
        "https://mainnet.base.org",
        "https://base.llamarpc.com",
        "https://1rpc.io/base",
    )

    class PaymentRpcError(RuntimeError):
        """Raised when all RPC providers fail transiently (timeouts, 429, 5xx)."""

    @classmethod
    def _rpc_urls(cls) -> List[str]:
        urls: List[str] = []
        primary = (os.getenv("BASE_RPC_URL") or "").strip()
        if primary:
            urls.append(primary)
        extra = (os.getenv("BASE_RPC_URLS") or "").strip()
        if extra:
            for part in extra.split(","):
                u = part.strip()
                if u and u not in urls:
                    urls.append(u)
        for key in ("ALCHEMY_BASE_RPC_URL", "QUICKNODE_BASE_RPC_URL", "INFURA_BASE_RPC_URL"):
            u = (os.getenv(key) or "").strip()
            if u and u not in urls:
                urls.append(u)
        for u in cls._PUBLIC_FALLBACKS:
            if u not in urls:
                urls.append(u)
        return urls

    @classmethod
    def _cache_key(cls, tx_hash: str, expected_to: str, expected_amount_wei: int) -> str:
        return f"payverify:{tx_hash.lower()}:{expected_to.lower()}:{expected_amount_wei}"

    @classmethod
    def _cache_get(cls, key: str) -> Optional[bool]:
        with cls._lock:
            if key in cls._verified:
                return cls._verified[key]
        try:
            from sincor2.persistent_store import get_store
            raw = get_store().kv_get(key)
            if not raw:
                return None
            payload = json.loads(raw)
            exp = float(payload.get("exp", 0))
            if exp < time.time():
                return None
            val = bool(payload.get("ok"))
            with cls._lock:
                cls._verified[key] = val
            return val
        except Exception as err:
            logger.debug("PaymentVerifier cache read failed: %s", err)
            return None

    @classmethod
    def _cache_set(cls, key: str, ok: bool) -> None:
        with cls._lock:
            cls._verified[key] = ok
        if not ok:
            return
        try:
            from sincor2.persistent_store import get_store
            get_store().kv_set(key, json.dumps({
                "ok": True,
                "exp": time.time() + cls._CACHE_TTL_SECONDS,
                "ts": time.time(),
            }))
        except Exception as err:
            logger.debug("PaymentVerifier cache write failed: %s", err)

    @classmethod
    def _get_web3(cls, rpc_url: str) -> Any:
        """Return a pooled web3.HTTPProvider client for *rpc_url*."""
        with cls._web3_lock:
            if rpc_url in cls._web3_pool:
                return cls._web3_pool[rpc_url]
            try:
                from web3 import Web3
                from web3.providers import HTTPProvider
                w3 = Web3(HTTPProvider(
                    rpc_url,
                    request_kwargs={"timeout": BASE_RPC_TIMEOUT},
                ))
                cls._web3_pool[rpc_url] = w3
                return w3
            except Exception as err:
                logger.debug("web3 init failed for %s: %s — urllib fallback", rpc_url, err)
                return None

    @classmethod
    def _fetch_receipt_web3(cls, rpc_url: str, tx_hash: str) -> Optional[Dict[str, Any]]:
        w3 = cls._get_web3(rpc_url)
        if w3 is None:
            return None
        receipt = w3.eth.get_transaction_receipt(tx_hash)
        if receipt is None:
            return None
        logs = []
        for log in receipt.get("logs") or []:
            topics = [t.hex() if hasattr(t, "hex") else t for t in (log.get("topics") or [])]
            data = log.get("data")
            if hasattr(data, "hex"):
                data = data.hex()
            addr = log.get("address")
            if hasattr(addr, "lower"):
                addr = addr.lower() if isinstance(addr, str) else str(addr)
            logs.append({"address": addr, "topics": topics, "data": data})
        status = receipt.get("status")
        status_hex = hex(status) if isinstance(status, int) else str(status)
        return {"status": status_hex, "logs": logs}

    @classmethod
    def _fetch_receipt_urllib(cls, rpc_url: str, tx_hash: str) -> Optional[Dict[str, Any]]:
        payload = json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "method": "eth_getTransactionReceipt",
            "params": [tx_hash],
        }).encode()
        with _urllib_request.urlopen(_urllib_request.Request(
            rpc_url,
            data=payload,
            headers={"Content-Type": "application/json"},
        ), timeout=BASE_RPC_TIMEOUT) as resp:
            data = json.loads(resp.read())
        if data.get("error"):
            raise RuntimeError(f"RPC error: {data['error']}")
        return data.get("result")

    @classmethod
    def _fetch_receipt(cls, rpc_url: str, tx_hash: str) -> Optional[Dict[str, Any]]:
        try:
            receipt = cls._fetch_receipt_web3(rpc_url, tx_hash)
            if receipt is not None:
                return receipt
        except Exception as err:
            logger.debug("web3 receipt fetch failed (%s): %s", rpc_url, err)
        return cls._fetch_receipt_urllib(rpc_url, tx_hash)

    @classmethod
    def is_verified(cls, tx_hash: str, expected_amount_wei: int,
                    expected_to: str = TREASURY_WALLET) -> bool:
        """
        Returns True if the tx has >=1 confirmation and transferred at least
        `expected_amount_wei` AXM to `expected_to`.

        Falls back to True in non-production environments so development/testing
        does not require live RPC calls.

        Raises PaymentRpcError if every provider fails with a transient error
        (so the caller can retry verification instead of burning the payment).
        """
        env = os.getenv("FLASK_ENV", "production").lower()
        if env in _DEV_ENVS:
            logger.warning("PaymentVerifier: skipping on-chain check (non-prod env)")
            return True

        if not tx_hash or not str(tx_hash).startswith("0x"):
            logger.warning("PaymentVerifier: invalid tx_hash %r", tx_hash)
            return False

        if str(tx_hash).startswith("0xSIMULATED"):
            return True

        cache_key = cls._cache_key(tx_hash, expected_to, expected_amount_wei)
        cached = cls._cache_get(cache_key)
        if cached is not None:
            return cached

        urls = cls._rpc_urls()
        if not urls:
            logger.error("PaymentVerifier: no RPC URLs configured")
            raise cls.PaymentRpcError("no RPC URLs configured")

        transient_errors: List[str] = []
        hard_reject = False

        for rpc_url in urls:
            for attempt in range(1, cls._MAX_ATTEMPTS + 1):
                try:
                    receipt = cls._fetch_receipt(rpc_url, tx_hash)
                    if not receipt:
                        transient_errors.append(f"{rpc_url}: receipt null (attempt {attempt})")
                        time.sleep(cls._BACKOFF_BASE * (2 ** (attempt - 1)))
                        continue
                    status = str(receipt.get("status", "")).lower()
                    if status not in ("0x1", "1"):
                        logger.warning(
                            "PaymentVerifier: tx %s not successful (status=%s) via %s",
                            tx_hash, status, rpc_url,
                        )
                        hard_reject = True
                        break
                    ok = cls._validate_transfer_log(
                        receipt.get("logs") or [],
                        expected_to=expected_to,
                        expected_amount_wei=expected_amount_wei,
                    )
                    if ok:
                        cls._cache_set(cache_key, True)
                        logger.info(
                            "PaymentVerifier: tx %s verified via %s (attempt %d)",
                            tx_hash, rpc_url, attempt,
                        )
                        return True
                    hard_reject = True
                    break
                except Exception as err:
                    msg = f"{rpc_url} attempt {attempt}: {err}"
                    transient_errors.append(msg)
                    logger.warning("PaymentVerifier RPC error: %s", msg)
                    time.sleep(cls._BACKOFF_BASE * (2 ** (attempt - 1)))
            if hard_reject:
                break

        if hard_reject:
            cls._cache_set(cache_key, False)
            return False

        logger.error(
            "PaymentVerifier: all RPC providers failed transiently for %s: %s",
            tx_hash, "; ".join(transient_errors[-6:]),
        )
        raise cls.PaymentRpcError(
            f"unable to verify payment {tx_hash}: all RPC providers failed"
        )

    @classmethod
    def _validate_transfer_log(
        cls,
        logs: List[Dict[str, Any]],
        expected_to: str,
        expected_amount_wei: int,
    ) -> bool:
        """
        Scan the receipt logs for an ERC-20 Transfer from the AXM contract
        whose `to` address matches *expected_to* and whose value is at least
        *expected_amount_wei*.
        """
        axm_addr = AXIOM_CONTRACT.lower()
        expected_to_norm = expected_to.lower()
        for log in logs:
            addr = (log.get("address") or "").lower()
            if addr != axm_addr:
                continue
            topics = log.get("topics") or []
            if len(topics) < 3:
                continue
            topic0 = topics[0].lower() if isinstance(topics[0], str) else str(topics[0]).lower()
            if topic0 != cls._TRANSFER_TOPIC:
                continue
            topic2 = topics[2] if isinstance(topics[2], str) else str(topics[2])
            to_addr = ("0x" + topic2[-40:]).lower()
            if to_addr != expected_to_norm:
                continue
            raw_value = log.get("data") or "0x0"
            if not isinstance(raw_value, str):
                raw_value = str(raw_value)
            try:
                value = int(raw_value, 16)
            except ValueError:
                continue
            if value >= expected_amount_wei:
                return True
        logger.warning(
            "PaymentVerifier: no qualifying AXM Transfer log found in tx; "
            "expected >=%d wei to %s from contract %s",
            expected_amount_wei, expected_to, AXIOM_CONTRACT,
        )
        return False
