"""Facilitator client — interface + LOCAL REFERENCE implementation.

In x402 the facilitator verifies a signed payment payload and settles it.
Here settlement means "fund the escrow", not "pay the seller directly" —
the seller is paid only on SEAL release after verification.

HONESTY LABEL: ``LocalFacilitator`` performs structural checks only
(signature present, authorization fields present, amounts equal to the
requirement it was handed). It does NOT verify EIP-712 signatures or touch
a chain. Production plugs a real facilitator (e.g. Coinbase CDP) behind
this same protocol.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .types import PaymentRequirement


@dataclass
class FacilitatorResult:
    ok: bool
    payer: str = ""
    payment_ref: str = ""
    error: str = ""


@runtime_checkable
class FacilitatorClient(Protocol):
    def verify_and_hold(self, *, payment_payload: dict[str, Any],
                        requirement: PaymentRequirement) -> FacilitatorResult:
        """Verify `payment_payload` against THE REQUIREMENT ARGUMENT.

        Callers (the server core) must pass their OWN retained requirement —
        never a dict reconstructed from client-supplied data.
        """
        ...


class LocalFacilitator:
    """Structural reference facilitator. REFERENCE ONLY."""

    def __init__(self) -> None:
        # Test observability: every requirement object this facilitator was
        # handed, in order. Lets tests prove the server passed its OWN
        # requirement, never the client's claimed one.
        self.seen_requirements: list[PaymentRequirement] = []
        self.calls = 0

    def verify_and_hold(self, *, payment_payload: dict[str, Any],
                        requirement: PaymentRequirement) -> FacilitatorResult:
        self.calls += 1
        self.seen_requirements.append(requirement)
        payload = payment_payload.get("payload") or {}
        auth = payload.get("authorization") or {}
        signature = payload.get("signature") or ""
        if not signature or not str(signature).startswith("0x"):
            return FacilitatorResult(False, error="missing_signature")
        payer = str(auth.get("from") or "")
        if not payer:
            return FacilitatorResult(False, error="missing_authorization_from")
        # The authorization must be for exactly the requirement's amount and
        # recipient — checked against the server's requirement argument.
        try:
            value = int(auth.get("value", -1))
        except (TypeError, ValueError):
            return FacilitatorResult(False, error="bad_authorization_value")
        if value != int(requirement.max_amount_required):
            return FacilitatorResult(False, error="amount_mismatch")
        if str(auth.get("to", "")).lower() != requirement.pay_to.lower():
            return FacilitatorResult(False, error="payto_mismatch")
        return FacilitatorResult(
            True, payer=payer,
            payment_ref="localref_" + str(abs(hash((payer, value))) % 10**12))
