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
  clear 200 or 402, raise AmbiguousPayment instead of retrying blindly.
  A caller-driven retry of the same logical payment reuses the in-flight
  signed authorization (W-75 journal) — never a fresh nonce — so it cannot
  double-pay.
- Paid-leg transport errors (timeouts, dropped responses) AFTER the
  signed payment has been sent — or at any point where delivery cannot
  be disproven — raise AmbiguousPayment, never a retryable error: the
  payment MAY have settled server-side. Callers must reconcile (check
  payment status / reuse the settled authorization) before any retry.
  Only a transport failure that PROVES no bytes left this machine
  (DNS failure, connection refused, malformed request) raises
  PaymentTransportError, which is safe to retry.
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
import socket
import threading
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
    """Payment state is UNKNOWN — retrying blindly could double-pay.

    Raised when the paid retry's response is neither a clear success nor
    a clear 402, AND when the paid leg suffers a transport error (timeout,
    dropped response, connection reset) at or after the point where the
    signed payment bytes may have left this machine. The payment MAY have
    settled server-side. The caller must reconcile (check payment status,
    reuse the settled authorization) before any retry. Human must
    investigate.
    """


class PaymentTransportError(X402BuyerError):
    """Transport failed BEFORE any payment bytes left this machine.

    Safe to retry: either no signature had been produced yet (unpaid leg),
    or the transport proved the connection was never established
    (DNS failure, connection refused, malformed request). No payment
    could have been made or settled.
    """


class StaleChallenge(X402BuyerError):
    """validBefore is in the past or unreasonably far in the future."""


# URLError reasons that prove the TCP connection was never established,
# hence no request bytes (and no payment) could have left this machine.
# Anything else (timeouts, resets, TLS errors, mid-stream failures) is
# treated as possibly-post-send: ambiguous on the paid leg.
_PROVABLY_PRE_SEND_REASONS = (ConnectionRefusedError, socket.gaierror)


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
        # W-75: payment-attempt journal.  Maps a resource URL to the
        # (requirements, signed authorization) of the last attempt whose
        # outcome was ambiguous (or transport-failed after signing).  A
        # retry of the SAME logical payment reuses the in-flight signed
        # bytes instead of minting a fresh nonce — the onchain nonce can
        # only be spent once, so reuse can never double-pay, while a
        # fresh signature would create a second spendable authorization.
        self._inflight: dict[str, tuple[PaymentRequirements, SignedPayment]] = {}
        self._journal_lock = threading.Lock()

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

    # -- payment-attempt journal (W-75) -------------------------------------

    @staticmethod
    def _same_logical_payment(
        req: PaymentRequirements, journaled: PaymentRequirements
    ) -> bool:
        """True when two challenges describe the same logical payment.

        Same recipient, amount, asset, and network.  A changed price or
        recipient is a NEW payment and must be signed fresh.
        """
        return (
            req.pay_to == journaled.pay_to
            and req.amount_atomic == journaled.amount_atomic
            and req.asset == journaled.asset
            and req.network == journaled.network
        )

    def _reuse_inflight(
        self, url: str, req: PaymentRequirements
    ) -> Optional[SignedPayment]:
        """Return the in-flight signed authorization for a retry, if any.

        Returns None when there is no journaled attempt for this URL, when
        the new challenge describes a different logical payment, or when
        the journaled authorization has expired (validBefore in the past) —
        in all of which the caller must sign fresh.
        """
        with self._journal_lock:
            entry = self._inflight.get(url)
        if entry is None:
            return None
        journaled_req, signed = entry
        if not self._same_logical_payment(req, journaled_req):
            return None
        valid_before = int(signed.authorization.get("validBefore") or 0)
        if valid_before and valid_before <= int(time.time()):
            return None
        return signed

    def _record_inflight(
        self, url: str, req: PaymentRequirements, signed: SignedPayment
    ) -> None:
        with self._journal_lock:
            self._inflight[url] = (req, signed)

    def _clear_inflight(self, url: str) -> None:
        with self._journal_lock:
            self._inflight.pop(url, None)

    # -- HTTP ---------------------------------------------------------

    def _do_request(
        self,
        url: str,
        method: str,
        body: Optional[dict],
        headers: dict[str, str],
    ) -> tuple[int, dict[str, str], bytes]:
        """Send one HTTP request; return (status, headers, body).

        Transport-failure contract (subclass overrides must honor it):
        - raise :class:`PaymentTransportError` ONLY when the transport can
          prove no request bytes left this machine (DNS failure, connection
          refused, malformed request). The caller treats this as safe to
          retry, even on the paid leg.
        - let any other transport error (``urllib.error.URLError``,
          ``OSError``, timeouts, resets) propagate unchanged: the caller
          maps those to :class:`AmbiguousPayment` on the paid leg, because
          the signed payment may already have been delivered.
        """
        data = json.dumps(body).encode() if body is not None else None
        try:
            req = urllib.request.Request(
                url, data=data, method=method.upper(), headers=headers
            )
        except Exception as exc:
            # Request could not even be constructed: nothing was sent.
            raise PaymentTransportError(
                f"could not build request (nothing sent; safe to retry): {exc}"
            ) from exc
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers or {}), exc.read()
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, _PROVABLY_PRE_SEND_REASONS):
                # TCP connect never completed: no bytes left this machine.
                raise PaymentTransportError(
                    "connection never established "
                    f"(nothing sent; safe to retry): {exc.reason}"
                ) from exc
            # Timeout / reset / TLS failure at an unknown point in the send
            # sequence: the payment may have been delivered. Propagate raw;
            # fetch() maps this to AmbiguousPayment on the paid leg.
            raise

    def fetch(
        self,
        url: str,
        method: str = "GET",
        body: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Fetch a paid x402 resource: 402 -> sign -> retry -> data.

        Returns the parsed JSON response body on success.
        Raises PaymentCapExceeded / UnsupportedPayment / MalformedChallenge /
        AmbiguousPayment / StaleChallenge / PaymentTransportError (all
        fail-closed, no payment made unless stated).

        Retry contract:
        - :class:`PaymentTransportError`: safe to retry — no payment was
          made or could have been made.
        - :class:`AmbiguousPayment`: payment state UNKNOWN — do NOT retry
          blindly; reconcile payment status first.  If you must retry the
          same logical payment, this method reuses the in-flight signed
          authorization (W-75) instead of minting a fresh nonce, so the
          retry can never create a second spendable authorization.
        """
        # --- unpaid leg: no signature exists yet, so ANY transport failure
        # is safe to retry.
        try:
            status, headers, raw = self._do_request(url, method, body, {})
        except PaymentTransportError:
            raise
        except Exception as exc:
            raise PaymentTransportError(
                "unpaid request transport error "
                f"(no payment was made; safe to retry): {exc}"
            ) from exc

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
        # W-75: on retry of the same logical payment, reuse the in-flight
        # signed authorization instead of minting a fresh nonce.  The
        # onchain nonce can only be spent once, so reuse is double-pay
        # safe; a fresh signature would be a second spendable authorization.
        signed = self._reuse_inflight(url, req)
        if signed is None:
            signed = self.sign_authorization(req)
            self._record_inflight(url, req, signed)

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
        # --- paid leg: the signed authorization now exists. A transport
        # error here is AMBIGUOUS unless the transport proves nothing was
        # sent: the payment may have settled server-side before the error.
        # A caller-driven retry reuses the in-flight authorization (W-75),
        # never a fresh nonce. Never auto-retry; the caller must reconcile
        # first.
        try:
            status2, _, raw2 = self._do_request(
                url,
                method,
                body,
                {"X-PAYMENT": payment_header, "PAYMENT-SIGNATURE": payment_header},
            )
        except PaymentTransportError:
            # Transport proved no bytes left this machine: safe to retry.
            raise
        except Exception as exc:
            raise AmbiguousPayment(
                "paid request transport error after the signed payment was "
                "sent (or at an unknown point in the send sequence); the "
                "payment MAY have settled server-side. DO NOT retry blindly "
                "— reconcile payment status before any retry "
                f"({type(exc).__name__}: {exc})"
            ) from exc

        if status2 == 200:
            # Paid leg settled: the logical payment is complete — drop the
            # journal entry so a later, genuinely new payment signs fresh.
            self._clear_inflight(url)
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
        try:
            status, _, raw = self._do_request(preflight_url, "GET", None, {})
        except PaymentTransportError:
            raise
        except Exception as exc:
            raise PaymentTransportError(
                f"preflight transport error (nothing paid; safe to retry): {exc}"
            ) from exc
        if status != 200:
            raise X402BuyerError(f"preflight failed with status {status}")
        return {"ok": True, "data": json.loads(raw or b"{}")}
