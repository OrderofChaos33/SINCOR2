"""WP4: server-priced payment intents.

D3: USDC + AXM ONLY. Fiat, Stripe, and any other asset are rejected at
construction time — there is no code path that can build a fiat intent.

A PaymentIntent binds (server-side, never client-supplied):
- asset: "USDC" | "AXM" (frozen literal — anything else raises)
- chain: e.g. "base", "base-sepolia"
- recipient: destination address (validated format)
- amount_atomic: integer atomic units (no floats on the money path)
- payer: payer wallet address
- expiry: unix timestamp after which the intent is void
- fulfillment_id: unique ID claimed atomically before fulfillment
- idempotency_key: deduplicates concurrent/duplicate submissions

The price is set by the SERVER (plan catalog / quote engine), never taken
from client request data. This closes the dynamic-price checkout hole
(stripe_routes /checkout accepted client-supplied price_cents).
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal

SUPPORTED_ASSETS: frozenset[str] = frozenset({"USDC", "AXM"})

Asset = Literal["USDC", "AXM"]

# Ethereum-style address validation (0x + 40 hex). Used for recipient/payer.
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

# Supported chains for launch (D4: off-chain; chains listed for intent
# binding so a future on-chain v2 has the binding already in place).
SUPPORTED_CHAINS: frozenset[str] = frozenset({"base", "base-sepolia"})


class PaymentIntentError(ValueError):
    """Raised when a payment intent violates D3 or binding rules."""


def _utc_now() -> int:
    return int(time.time())


@dataclass(frozen=True, slots=True)
class PaymentIntent:
    """Immutable, server-priced payment intent. USDC|AXM only."""

    intent_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    asset: str = ""
    chain: str = "base"
    recipient: str = ""
    amount_atomic: int = 0
    payer: str = ""
    expiry: int = 0
    fulfillment_id: str = ""
    idempotency_key: str = ""
    # Server-side price reference (USD cents) for audit, not for charging.
    usd_reference_cents: int = 0
    created_at: int = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        # D3: asset allowlist — fiat/Stripe/anything else rejected here.
        if self.asset not in SUPPORTED_ASSETS:
            raise PaymentIntentError(
                f"unsupported asset {self.asset!r}: only {sorted(SUPPORTED_ASSETS)} "
                "per owner decision D3 (USDC + AXM only)"
            )
        if self.chain not in SUPPORTED_CHAINS:
            raise PaymentIntentError(f"unsupported chain {self.chain!r}")
        if not _ADDRESS_RE.match(self.recipient):
            raise PaymentIntentError(f"invalid recipient address {self.recipient!r}")
        if not _ADDRESS_RE.match(self.payer):
            raise PaymentIntentError(f"invalid payer address {self.payer!r}")
        if self.amount_atomic <= 0:
            raise PaymentIntentError("amount_atomic must be positive")
        if self.expiry <= _utc_now():
            raise PaymentIntentError("expiry must be in the future")
        if not self.idempotency_key:
            raise PaymentIntentError("idempotency_key is required")
        if not self.fulfillment_id:
            raise PaymentIntentError("fulfillment_id is required")

    @property
    def is_expired(self) -> bool:
        return _utc_now() >= self.expiry

    @classmethod
    def create_server_priced(
        cls,
        *,
        asset: str,
        chain: str,
        recipient: str,
        amount_atomic: int,
        payer: str,
        ttl_seconds: int,
        idempotency_key: str,
        usd_reference_cents: int = 0,
    ) -> "PaymentIntent":
        """Build an intent with SERVER-set price. Client data never sets amount.

        Callers pass the server-resolved amount (from plan catalog / quote
        engine). The amount is never read from an HTTP request body here.
        """
        now = _utc_now()
        return cls(
            asset=asset,
            chain=chain,
            recipient=recipient,
            amount_atomic=amount_atomic,
            payer=payer,
            expiry=now + ttl_seconds,
            fulfillment_id=f"ful-{uuid.uuid4().hex[:16]}",
            idempotency_key=idempotency_key,
            usd_reference_cents=usd_reference_cents,
            created_at=now,
        )
