"""Platform escrow client — interface + LOCAL REFERENCE implementation.

HONESTY LABEL: no escrow/SEAL endpoint for this wrapper exists on
getsincor.com yet, and no SINCOR escrow contract is deployed on Base
mainnet. ``LocalReferencePlatform`` is an in-memory ledger that implements
the intended semantics (hold → release-on-verify / refund-on-fail) so the
full loop is testable offline. A production integration implements the
same ``PlatformClient`` protocol against the real platform API when it
ships; nothing in this package fakes that connection.
"""
from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .types import PaymentRequirement


@dataclass
class EscrowRecord:
    escrow_id: str
    payer: str
    pay_to: str
    amount_atomic: int
    status: str = "held"          # held | released | refunded
    release_receipt: dict[str, Any] = field(default_factory=dict)
    refund_receipt: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class PlatformClient(Protocol):
    """SEAL-side escrow surface the wrapper settles through."""

    def open_escrow(self, *, requirement: PaymentRequirement, payer: str,
                    payment_ref: str) -> str:
        """Hold the payment in escrow; returns an escrow id."""
        ...

    def release(self, escrow_id: str) -> dict[str, Any]:
        """SEAL release: pay the held amount to the seller (verify passed)."""
        ...

    def refund(self, escrow_id: str) -> dict[str, Any]:
        """Refund the held amount to the payer (verify failed / timeout)."""
        ...

    def get_escrow(self, escrow_id: str) -> EscrowRecord | None:
        ...


class LocalReferencePlatform:
    """In-memory reference PlatformClient. REFERENCE ONLY — not connected
    to getsincor.com and not an on-chain contract."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._escrows: dict[str, EscrowRecord] = {}
        self.released_total = 0
        self.refunded_total = 0

    def open_escrow(self, *, requirement: PaymentRequirement, payer: str,
                    payment_ref: str) -> str:
        escrow_id = "esc_" + secrets.token_hex(8)
        with self._lock:
            self._escrows[escrow_id] = EscrowRecord(
                escrow_id=escrow_id, payer=payer, pay_to=requirement.pay_to,
                amount_atomic=int(requirement.max_amount_required))
        return escrow_id

    def _settle(self, escrow_id: str, status: str) -> dict[str, Any]:
        with self._lock:
            rec = self._escrows.get(escrow_id)
            if rec is None:
                return {"ok": False, "error": "unknown_escrow"}
            if rec.status != "held":
                # Double-settle is impossible: a held escrow settles once.
                return {"ok": False, "error": f"already_{rec.status}",
                        "escrow_id": escrow_id}
            rec.status = status
            receipt = {"ok": True, "escrow_id": escrow_id, "status": status,
                       "amount_atomic": rec.amount_atomic,
                       "pay_to": rec.pay_to, "payer": rec.payer,
                       "rail": "local-reference (not on-chain)"}
            if status == "released":
                rec.release_receipt = receipt
                self.released_total += rec.amount_atomic
            else:
                rec.refund_receipt = receipt
                self.refunded_total += rec.amount_atomic
            return receipt

    def release(self, escrow_id: str) -> dict[str, Any]:
        return self._settle(escrow_id, "released")

    def refund(self, escrow_id: str) -> dict[str, Any]:
        return self._settle(escrow_id, "refunded")

    def get_escrow(self, escrow_id: str) -> EscrowRecord | None:
        with self._lock:
            return self._escrows.get(escrow_id)
