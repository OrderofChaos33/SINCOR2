"""Buyer client — pays wrapped endpoints without ever double-paying.

The bug this design makes impossible: a real x402 retry loop once produced
40 payments for one request (retrying after ambiguous responses, each retry
signing a *fresh* authorization). Defences, in order:

1. **Instruction-scoped idempotency.** Every paid call carries an
   ``instruction_id`` (the caller's unit of work). The journal maps
   instruction_id → (request fingerprint, signed payload, outcome).
   Repeating a call with the same instruction_id and the same request
   returns the recorded outcome and NEVER signs again. Reusing the id with
   a *different* request raises IdempotencyConflict instead of paying.
2. **One signature per instruction, reused verbatim.** HTTP-level retries
   resend the SAME signed payload (same EIP-3009 nonce), which the chain /
   facilitator can only settle once — a resend is not a new payment.
3. **Per-payee velocity cap.** Distinct instructions to the same payTo are
   capped by count and total amount inside a sliding window; the cap is
   checked BEFORE signing, so a capped attempt creates no payment at all.
4. **No blind retry on ambiguity** (inherited from the platform buyer):
   a retry response that is neither clear success nor clear rejection
   raises AmbiguousPayment and the journal keeps the instruction locked.

Keys never enter this module: the EIP-712 signer is injected, exactly as
in the platform's ``x402_buyer.py``.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .errors import (AmbiguousPayment, IdempotencyConflict, MalformedChallenge,
                     PaymentCapExceeded, UnsupportedPayment,
                     VelocityCapExceeded, SincorX402Error)
from .types import USDC_BASE_ADDRESS, canonical_json

SignerFn = Callable[[dict[str, Any]], str]
# transport(method, url, body, headers) -> (status, headers, raw_bytes)
TransportFn = Callable[[str, str, Optional[dict], dict], tuple]


def _urllib_transport(method: str, url: str, body: Optional[dict],
                      headers: dict) -> tuple:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method.upper(),
                                 headers=headers)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()


@dataclass
class _JournalEntry:
    fingerprint: str
    payment_payload: dict[str, Any] | None = None
    outcome: dict[str, Any] | None = None
    ambiguous: bool = False


@dataclass
class VelocityCap:
    """Per-payee cap: at most `max_payments` totalling `max_amount_atomic`
    per `window_seconds` (sliding)."""
    max_payments: int = 5
    max_amount_atomic: int = 1_000_000   # $1.00 of USDC by default
    window_seconds: float = 60.0
    _events: dict[str, list] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def check_and_record(self, pay_to: str, amount_atomic: int) -> None:
        now = time.time()
        key = pay_to.lower()
        with self._lock:
            events = [(t, a) for (t, a) in self._events.get(key, [])
                      if now - t < self.window_seconds]
            if len(events) >= self.max_payments:
                raise VelocityCapExceeded(
                    f"payee {pay_to}: {len(events)} payments in window "
                    f"(cap {self.max_payments})")
            if sum(a for _, a in events) + amount_atomic > self.max_amount_atomic:
                raise VelocityCapExceeded(
                    f"payee {pay_to}: window amount cap "
                    f"{self.max_amount_atomic} would be exceeded")
            events.append((now, amount_atomic))
            self._events[key] = events


class VerifiedBuyer:
    """Pays sincor-x402 / x402 endpoints. See module docstring."""

    def __init__(
        self,
        wallet_address: str,
        signer_fn: SignerFn,
        *,
        max_payment_usd: float = 1.00,
        velocity_cap: VelocityCap | None = None,
        transport: TransportFn | None = None,
    ) -> None:
        if not wallet_address.startswith("0x"):
            raise ValueError("wallet_address must be a 0x address")
        self.wallet_address = wallet_address
        self._signer_fn = signer_fn
        self.max_payment_usd = max_payment_usd
        self.velocity_cap = velocity_cap or VelocityCap()
        self._transport = transport or _urllib_transport
        self._journal: dict[str, _JournalEntry] = {}
        self._lock = threading.Lock()
        self.signatures_created = 0   # observability: tests assert == 1 in storms

    # -- challenge handling -------------------------------------------------

    @staticmethod
    def _select_requirement(body: dict[str, Any]) -> dict[str, Any]:
        accepts = body.get("accepts")
        if isinstance(accepts, list) and accepts:
            for entry in accepts:
                if (str(entry.get("asset", "")).lower()
                        == USDC_BASE_ADDRESS.lower()
                        and str(entry.get("network", "")) in ("base", "eip155:8453")):
                    return entry
            raise UnsupportedPayment("no supported accepts[] entry (USDC/Base)")
        if isinstance(body.get("payment"), dict):   # platform seller shape
            return body["payment"]
        raise MalformedChallenge("402 body has no accepts[]")

    def _build_signed_payload(self, requirement: dict[str, Any]) -> dict[str, Any]:
        try:
            amount_atomic = int(requirement.get("maxAmountRequired"))
        except (TypeError, ValueError) as exc:
            raise MalformedChallenge("accepts entry missing maxAmountRequired") from exc
        amount_usd = amount_atomic / 1_000_000
        if amount_usd > self.max_payment_usd:
            raise PaymentCapExceeded(
                f"402 demands ${amount_usd:.4f} > cap ${self.max_payment_usd:.2f}")
        pay_to = str(requirement.get("payTo") or "")
        if not pay_to:
            raise MalformedChallenge("accepts entry missing payTo")
        # Velocity gate BEFORE signing: a capped attempt is not a payment.
        self.velocity_cap.check_and_record(pay_to, amount_atomic)
        now = int(time.time())
        authorization = {
            "from": self.wallet_address,
            "to": pay_to,
            "value": amount_atomic,
            "validAfter": 0,
            "validBefore": now + 600,
            "nonce": "0x" + secrets.token_hex(32),
        }
        typed_data = {
            "types": {"TransferWithAuthorization": [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"},
                {"name": "nonce", "type": "bytes32"}]},
            "domain": {"name": "USD Coin", "version": "2", "chainId": 8453,
                       "verifyingContract": USDC_BASE_ADDRESS},
            "primaryType": "TransferWithAuthorization",
            "message": authorization,
        }
        signature = self._signer_fn(typed_data)
        if not signature or not str(signature).startswith("0x"):
            raise SincorX402Error("signer_fn did not return a 0x signature")
        with self._lock:
            self.signatures_created += 1
        return {
            "x402Version": 1,
            "scheme": str(requirement.get("scheme", "exact")),
            "network": str(requirement.get("network", "base")),
            # Echo the accepted terms EXACTLY as the server offered them —
            # the server hedge compares this claim against its own copy.
            "accepted": requirement,
            "payload": {"signature": str(signature),
                        "authorization": authorization},
        }

    # -- main entry -----------------------------------------------------------

    @staticmethod
    def _fingerprint(url: str, method: str, body: Optional[dict]) -> str:
        return hashlib.sha256(canonical_json(
            {"url": url, "method": method.upper(), "body": body})).hexdigest()

    def fetch(self, url: str, *, instruction_id: str, method: str = "POST",
              body: Optional[dict] = None) -> dict[str, Any]:
        """Pay-and-fetch one instruction. Safe to call any number of times
        with the same instruction_id: at most one payment is ever created.
        """
        if not instruction_id:
            raise ValueError("instruction_id is required (idempotency scope)")
        fingerprint = self._fingerprint(url, method, body)
        with self._lock:
            entry = self._journal.get(instruction_id)
            if entry is not None:
                if entry.fingerprint != fingerprint:
                    raise IdempotencyConflict(
                        f"instruction {instruction_id!r} reused with a "
                        "different request")
                if entry.outcome is not None:
                    return entry.outcome
                if entry.ambiguous:
                    raise AmbiguousPayment(
                        f"instruction {instruction_id!r} already paid with "
                        "an ambiguous outcome; refusing to pay again")
                # A signed payload exists but no outcome (transport died
                # mid-flight): fall through and RESEND THE SAME PAYLOAD.
                signed_payload = entry.payment_payload
            else:
                signed_payload = None
                self._journal[instruction_id] = _JournalEntry(fingerprint)

        headers = {"Idempotency-Key": instruction_id}
        if signed_payload is None:
            status, _, raw = self._transport(method, url, body, headers)
            if status == 200:
                outcome = {"ok": True, "paid": False,
                           "data": json.loads(raw or b"{}")}
                with self._lock:
                    self._journal[instruction_id].outcome = outcome
                return outcome
            if status != 402:
                raise SincorX402Error(f"unexpected status {status} unpaid")
            challenge = json.loads(raw or b"{}")
            requirement = self._select_requirement(challenge)
            signed_payload = self._build_signed_payload(requirement)
            with self._lock:
                self._journal[instruction_id].payment_payload = signed_payload

        payment_header = json.dumps(signed_payload)
        status2, _, raw2 = self._transport(
            method, url, body,
            {**headers, "X-PAYMENT": payment_header,
             "PAYMENT-SIGNATURE": payment_header})
        if status2 == 200:
            outcome = {"ok": True, "paid": True,
                       "data": json.loads(raw2 or b"{}")}
            with self._lock:
                self._journal[instruction_id].outcome = outcome
            return outcome
        # Anything else is ambiguous for payment purposes: the payment may
        # have settled. Lock the instruction; NEVER sign a second one.
        with self._lock:
            self._journal[instruction_id].ambiguous = True
        raise AmbiguousPayment(
            f"paid retry returned {status2}; instruction locked, no re-pay")
