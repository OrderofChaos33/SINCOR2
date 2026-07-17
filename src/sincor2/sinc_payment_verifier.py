"""SINC payment verifier — on-chain validation of SINC transfers to treasury.

Closes a real revenue leak: SINC is the platform's primary settlement token,
but the A2A ``PaymentVerifier`` only ever validated **AXM** transfer logs.
Anything quoted in SINC was effectively unverified.

SINC has ``decimals = 0`` on Base — the raw ERC-20 ``uint256`` transfer value
IS the whole-token amount, so no decimal scaling is applied.

Usage
-----
    from sincor2.sinc_payment_verifier import SINCPaymentVerifier

    ok = SINCPaymentVerifier.is_verified(tx_hash, expected_amount_sinc=1)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import urllib.request as _urllib_request
from typing import Any, Dict, List

logger = logging.getLogger("sincor.sinc_verifier")

SINC_CONTRACT = os.getenv(
    "SINC_CONTRACT_ADDRESS", "0x9C8cd8d3961F445D653713dE65C6578bE11668e7"
)
TREASURY_ADDRESS = os.getenv(
    "TREASURY_ADDRESS", "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
)
BASE_RPC_TIMEOUT = int(os.getenv("BASE_RPC_TIMEOUT", "10"))

_DEV_ENVS = frozenset({"development", "dev", "test", "testing", "local"})

# ERC-20 Transfer(address,address,uint256) event topic
_TRANSFER_TOPIC = (
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
)


class SINCPaymentVerifier:
    """Verifies a tx transferred >= N whole SINC to the treasury on Base."""

    _verified: Dict[str, bool] = {}
    _lock: threading.Lock = threading.Lock()

    @classmethod
    def is_verified(
        cls,
        tx_hash: str,
        expected_amount_sinc: int,
        expected_to: str = TREASURY_ADDRESS,
    ) -> bool:
        """True if the tx succeeded AND contains a SINC Transfer to
        ``expected_to`` of at least ``expected_amount_sinc`` whole tokens.

        Non-production environments skip the RPC check so dev flows work
        without a live node.
        """
        env = os.getenv("FLASK_ENV", "production").lower()
        if env in _DEV_ENVS:
            logger.warning("SINCPaymentVerifier: skipping on-chain check (non-prod env)")
            return True

        with cls._lock:
            cached = cls._verified.get(tx_hash)
        if cached is not None:
            return cached

        rpc_url = os.getenv("BASE_RPC_URL")
        if not rpc_url:
            logger.error("BASE_RPC_URL not set — cannot verify SINC payment")
            return False

        result = False
        try:
            payload = json.dumps({
                "jsonrpc": "2.0", "id": 1,
                "method": "eth_getTransactionReceipt",
                "params": [tx_hash],
            }).encode()
            with _urllib_request.urlopen(_urllib_request.Request(
                rpc_url, data=payload,
                headers={"Content-Type": "application/json"},
            ), timeout=BASE_RPC_TIMEOUT) as resp:
                data = json.loads(resp.read())
            receipt = data.get("result")
            if not receipt or receipt.get("status") != "0x1":
                logger.warning("SINCPaymentVerifier: tx %s not successful", tx_hash)
            else:
                result = cls._validate_transfer_log(
                    receipt.get("logs", []),
                    expected_to=expected_to,
                    expected_amount_sinc=expected_amount_sinc,
                )
        except Exception as exc:  # noqa: BLE001
            logger.error("SINCPaymentVerifier RPC error: %s", exc)

        if result:
            with cls._lock:
                cls._verified[tx_hash] = True
        return result

    @classmethod
    def _validate_transfer_log(
        cls,
        logs: List[Dict[str, Any]],
        expected_to: str,
        expected_amount_sinc: int,
    ) -> bool:
        """Scan receipt logs for a qualifying SINC Transfer event."""
        sinc_addr = SINC_CONTRACT.lower()
        expected_to_norm = expected_to.lower()
        for log in logs:
            if log.get("address", "").lower() != sinc_addr:
                continue
            topics = log.get("topics", [])
            if len(topics) < 3 or topics[0].lower() != _TRANSFER_TOPIC:
                continue
            to_addr = ("0x" + topics[2][-40:]).lower()
            if to_addr != expected_to_norm:
                continue
            try:
                value = int(log.get("data", "0x0"), 16)  # decimals=0 → whole SINC
            except ValueError:
                continue
            if value >= expected_amount_sinc:
                return True
        logger.warning(
            "SINCPaymentVerifier: no qualifying SINC Transfer log; expected "
            ">=%d SINC to %s from %s",
            expected_amount_sinc, expected_to, SINC_CONTRACT,
        )
        return False

    @classmethod
    def invalidate(cls, tx_hash: str) -> None:
        with cls._lock:
            cls._verified.pop(tx_hash, None)
