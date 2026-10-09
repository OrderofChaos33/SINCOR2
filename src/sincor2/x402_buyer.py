"""x402 BUYER client: pay other x402 services (we are the buyer).

This is the buyer-side complement to x402_payments.py (seller side).
Standard x402 v2 flow:
  1. GET/POST resource without payment -> 402 PAYMENT-REQUIRED
  2. Parse payment details from 402 response
  3. Sign EIP-712 transferWithAuthorization for USDC via injected signer
  4. Retry with X-PAYMENT (PAYMENT-SIGNATURE) header
  5. Server verifies, returns data

SAFETY (fail-closed):
- signer_fn is INJECTED. This module NEVER sees, stores, or derives
  private keys. Key custody stays with the caller (founder-controlled).
- max_payment_usd cap per call (default $0.10). Any 402 demanding more
  raises PaymentCapExceeded and NO signature is produced.
- No auto-retry on ambiguous payments: if the retry response is not a
  clear 200 or 402, raise AmbiguousPayment instead of retrying blindly
  (a retry could double-pay).
- validBefore must be in the future and within 1 hour of now; otherwise
  refuse to sign (stale or absurd expiry).
- Only USDC on Base (eip155:8453) is supported. Any other asset/network
  in the 402 raises UnsupportedPayment.

Tested against MACH Base (https://api.mach.gallery) x402 v2 flow.
No real network calls in tests — everything is mocked.
"""

from __future__ import annotations

import json
import secrets
import time
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Any, Callable, Optional

# Base mainnet USDC (EIP-3009 transferWithAuthorization)
USDC_BASE_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
USDC_DECIMALS = 6
BASE_CHAIN_ID = 8453
BASE_CHAIN_REF = "eip155:8453"

# EIP-3009 domain for USDC on Base
_USDC_DOMAIN = {
    "name": "USD Coin",
    "version": "2",
    "chainId": BASE_CHAIN_ID,
    "verifyingContract": USDC_BASE_ADDRESS,
}

_TRANSFER_WITH_AUTH_TYPES = {
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ],
    "TransferWithAuthorization": [
        {"name": "from", "type": "address"},
        {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "validAfter", "type": "uint256"},
        {"name": "validBefore", "type": "uint256"},
        {"name": "nonce", "type": "bytes32"},
    ],
}


# ---------------------------------------------------------------------------
# Errors (all fail-closed)
# ---------------------------------------------------------------------------


class X402BuyerError(Exception):
    """Base for all buyer errors."""


class PaymentCapExceeded(X402BuyerError):
    """402 demands more than the per-call cap. No signature produced."""


class UnsupportedPayment(X402BuyerError):
    """402 asks for an unsupported asset, network, or scheme."""


class MalformedChallenge(X402BuyerError):
    """402 response is missing required payment fields."""


class AmbiguousPayment(X402BuyerError):
    """Retry response was neither clear success nor clear 402.

    Retrying blindly could double-pay. Human must investigate.
    """


class StaleChallenge(X402BuyerError):
    """validBefore is in the past or unreasonably far in the future."""


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PaymentRequirements:
    """Parsed from a 402 PAYMENT-REQUIRED response."""

    amount_atomic: int
    amount_usd: float
    asset: str  # token contract address
    network: str  # e.g. "eip155:8453"
    pay_to: str
    nonce: str  # hex bytes32
    valid_after: int  # unix timestamp
    valid_before: int  # unix timestamp
    resource: str = ""
    description: str = ""


@dataclass(frozen=True)
class SignedPayment:
    """EIP-712 signed transferWithAuthorization, ready for X-PAYMENT header."""

    signature: str  # 0x-prefixed hex
    authorization: dict[str, Any]  # the signed message fields


# Type: takes EIP-712 typed data dict, returns 0x-prefixed signature hex.
# The caller owns the key; this module never sees it.
SignerFn = Callable[[dict[str, Any]], str]


# ---------------------------------------------------------------------------
# Buyer
# ---------------------------------------------------------------------------


class X402Buyer:
    """Pays x402 services. Keys never enter this class."""

    def __init__(
        self,
        wallet_address: str,
        signer_fn: SignerFn,
        *,
        max_payment_usd: float = 0.10,
        timeout_seconds: int = 30,
    ) -> None:
        if not wallet_address or not wallet_address.startswith("0x"):
            raise ValueError("wallet_address must be a 0x address")
        if max_payment_usd <= 0:
            raise ValueError("max_payment_usd must be positive")
        self.wallet_address = wallet_address
        self._signer_fn = signer_fn
        self.max_payment_usd = max_payment_usd
        self.timeout_seconds = timeout_seconds

    # -- 402 parsing ----------------------------------------------------

    @staticmethod
    def parse_402(body: dict[str, Any]) -> PaymentRequirements:
        """Parse x402 v2 402 response body into PaymentRequirements.

        Accepts both our seller format (nested "payment" dict) and the
        generic x402 v2 flat format. Raises MalformedChallenge on missing
        fields, UnsupportedPayment on wrong asset/network.
        """
        # Unwrap: our seller nests under "payment"; MACH/x402v2 may be flat
        # or under "accepts"[0].
        p = body
        if isinstance(body.get("payment"), dict):
            p = body["payment"]
        elif isinstance(body.get("accepts"), list) and body["accepts"]:
            p = body["accepts"][0]

        def _get(*names: str) -> Any:
            for n in names:
                if p.get(n) is not None:
                    return p[n]
            return None

        amount_atomic = _get("maxAmountRequired", "amount_atomic", "amount")
        asset = _get("asset", "assetAddress")
        network = _get("network", "chainId")
        pay_to = _get("payTo", "pay_to", "recipient")
        # nonce may be absent -> we generate one (EIP-3009 allows any unique)
        nonce = _get("nonce") or ("0x" + secrets.token_hex(32))
        valid_after = _get("validAfter", "valid_after") or 0
        valid_before = _get("validBefore", "valid_before") or 0

        missing = [
            n
            for n, v in [
                ("amount", amount_atomic),
                ("asset", asset),
                ("network", network),
                ("pay_to", pay_to),
            ]
            if v is None
        ]
        if missing:
            raise MalformedChallenge(f"402 missing fields: {', '.join(missing)}")

        # Normalize network to eip155:NNNN
        net_str = str(network)
        if net_str.isdigit():
            net_str = f"eip155:{net_str}"
        if net_str != BASE_CHAIN_REF:
            raise UnsupportedPayment(f"unsupported network: {network!r}")

        # Only USDC on Base
        if str(asset).lower() != USDC_BASE_ADDRESS.lower():
            raise UnsupportedPayment(f"unsupported asset: {asset!r}")

        try:
            amount_atomic_int = int(amount_atomic)
        except (TypeError, ValueError) as exc:
            raise MalformedChallenge(f"bad amount: {amount_atomic!r}") from exc

        amount_usd = amount_atomic_int / (10**USDC_DECIMALS)

        return PaymentRequirements(
            amount_atomic=amount_atomic_int,
            amount_usd=amount_usd,
            asset=str(asset),
            network=net_str,
            pay_to=str(pay_to),
            nonce=str(nonce),
            valid_after=int(valid_after or 0),
            valid_before=int(valid_before or 0),
            resource=str(_get("resource") or ""),
            description=str(_get("description") or ""),
        )

    # -- safety gates ---------------------------------------------------

    def _check_requirements(self, req: PaymentRequirements) -> None:
        """Fail-closed gates before signing. Raises on any violation."""
        if req.amount_usd > self.max_payment_usd:
            raise PaymentCapExceeded(
                f"402 demands ${req.amount_usd:.4f} > cap ${self.max_payment_usd:.2f}; "
                "no signature produced"
            )
        if req.amount_atomic <= 0:
            raise MalformedChallenge("non-positive payment amount")
        now = int(time.time())
        # valid_before must be future and within 1h (absurd expiries rejected)
        if req.valid_before and req.valid_before <= now:
            raise StaleChallenge("valid_before is in the past")
        if req.valid_before and req.valid_before > now + 3600:
            raise StaleChallenge("valid_before more than 1h out; refusing")

    # -- EIP-712 signing ------------------------------------------------

    def sign_authorization(self, req: PaymentRequirements) -> SignedPayment:
        """Build and sign EIP-712 transferWithAuthorization via injected signer."""
        self._check_requirements(req)

        now = int(time.time())
        valid_before = req.valid_before or (now + 600)  # default 10 min

        message = {
            "from": self.wallet_address,
            "to": req.pay_to,
            "value": req.amount_atomic,
            "validAfter": req.valid_after or 0,
            "validBefore": valid_before,
            "nonce": req.nonce,
        }
        typed_data = {
            "types": _TRANSFER_WITH_AUTH_TYPES,
            "domain": _USDC_DOMAIN,
            "primaryType": "TransferWithAuthorization",
            "message": message,
        }
        signature = self._signer_fn(typed_data)
        if not signature or not str(signature).startswith("0x"):
            raise X402BuyerError("signer_fn did not return 0x signature")
        return SignedPayment(signature=str(signature), authorization=message)

    # -- HTTP ---------------------------------------------------------

    def _do_request(
        self,
        url: str,
        method: str,
        body: Optional[dict],
        headers: dict[str, str],
    ) -> tuple[int, dict[str, str], bytes]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers or {}), exc.read()

    def fetch(
        self,
        url: str,
        method: str = "GET",
        body: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Fetch a paid x402 resource: 402 -> sign -> retry -> data.

        Returns the parsed JSON response body on success.
        Raises PaymentCapExceeded / UnsupportedPayment / MalformedChallenge /
        AmbiguousPayment / StaleChallenge (all fail-closed, no payment made).
        """
        status, headers, raw = self._do_request(url, method, body, {})

        if status == 200:
            return {"ok": True, "paid": False, "data": json.loads(raw or b"{}")}

        if status != 402:
            raise X402BuyerError(f"unexpected status {status} on unpaid request")

        # Parse 402: body JSON or PAYMENT-REQUIRED header
        challenge_body: dict[str, Any] = {}
        try:
            challenge_body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            pass
        # Header fallback (x402 v2 sends payment details in header too)
        for h_name in ("PAYMENT-REQUIRED", "X-Payment-Required", "Payment-Required"):
            if headers.get(h_name):
                try:
                    challenge_body = json.loads(headers[h_name])
                    break
                except json.JSONDecodeError:
                    continue

        req = self.parse_402(challenge_body)
        signed = self.sign_authorization(req)

        payment_header = json.dumps(
            {
                "x402Version": 1,
                "scheme": "exact",
                "network": req.network,
                "payload": {
                    "signature": signed.signature,
                    "authorization": signed.authorization,
                },
            }
        )
        status2, _, raw2 = self._do_request(
            url,
            method,
            body,
            {"X-PAYMENT": payment_header, "PAYMENT-SIGNATURE": payment_header},
        )

        if status2 == 200:
            return {
                "ok": True,
                "paid": True,
                "amount_usd": req.amount_usd,
                "data": json.loads(raw2 or b"{}"),
            }
        if status2 == 402:
            # Server still wants payment after our signed retry: do NOT loop.
            raise AmbiguousPayment(
                "server returned 402 after signed payment; refusing to retry "
                "(could double-pay). Investigate manually."
            )
        raise AmbiguousPayment(
            f"ambiguous retry status {status2}; refusing to retry blindly"
        )

    # -- preflight ------------------------------------------------------

    def preflight(self, preflight_url: str) -> dict[str, Any]:
        """Free preflight check before any paid call. No signing involved."""
        status, _, raw = self._do_request(preflight_url, "GET", None, {})
        if status != 200:
            raise X402BuyerError(f"preflight failed with status {status}")
        return {"ok": True, "data": json.loads(raw or b"{}")}
