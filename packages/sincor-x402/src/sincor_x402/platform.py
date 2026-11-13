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

from .errors import DuplicatePaymentRef, MissingPaymentRef
from .types import PaymentRequirement


@dataclass
class EscrowRecord:
    escrow_id: str
    payer: str
    pay_to: str
    amount_atomic: int
    # The payment reference this escrow consumed (W-146). Stored so the
    # platform can reject any second escrow for the same payment.
    payment_ref: str = ""
    status: str = "held"          # held | released | refunded
    release_receipt: dict[str, Any] = field(default_factory=dict)
    refund_receipt: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class PlatformClient(Protocol):
    """SEAL-side escrow surface the wrapper settles through."""

    def open_escrow(self, *, requirement: PaymentRequirement, payer: str,
                    payment_ref: str) -> str:
        """Hold the payment in escrow; returns an escrow id.

        Payment-consumption contract (W-146): implementations MUST store
        ``payment_ref`` on the escrow record and MUST reject reuse —
        raise :class:`~sincor_x402.errors.DuplicatePaymentRef` if the ref
        has already funded an escrow, whether that escrow is still held,
        was released, or was refunded. One payment authorization funds
        exactly one escrow. Production implementations must consume the
        ref durably (the check and the escrow creation are one atomic
        step), matching the facilitator-side requirement that the
        EIP-3009 nonce be settled/consumed before escrow opens.
        Implementations MUST also fail closed on an empty/missing ref —
        raise :class:`~sincor_x402.errors.MissingPaymentRef`: a ref that
        is not there cannot be consumed, so an escrow opened against one
        would bypass replay protection entirely.
        """
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
        # Consumed payment refs (W-146): a ref enters this set when its
        # escrow opens and never leaves it — release and refund settle the
        # escrow, they do not un-consume the payment.
        self._consumed_payment_refs: set[str] = set()
        self.released_total = 0
        self.refunded_total = 0

    def open_escrow(self, *, requirement: PaymentRequirement, payer: str,
                    payment_ref: str) -> str:
        # Fail closed (W-146): no escrow without a consumable ref. An
        # empty/missing ref cannot be deduped, so skipping the check for
        # falsy refs (the old behaviour) let unlimited escrows open.
        if not isinstance(payment_ref, str) or not payment_ref.strip():
            raise MissingPaymentRef(
                "payment_ref is empty or missing; refusing to open an "
                "escrow without a consumable payment reference")
        escrow_id = "esc_" + secrets.token_hex(8)
        with self._lock:
            # Check-and-consume is atomic under the lock: two concurrent
            # presentations of the same payment cannot both open escrows.
            if payment_ref in self._consumed_payment_refs:
                raise DuplicatePaymentRef(
                    f"payment_ref {payment_ref!r} already consumed by a "
                    "previous escrow; one payment funds exactly one escrow")
            self._consumed_payment_refs.add(payment_ref)
            self._escrows[escrow_id] = EscrowRecord(
                escrow_id=escrow_id, payer=payer, pay_to=requirement.pay_to,
                amount_atomic=int(requirement.max_amount_required),
                payment_ref=payment_ref)
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
