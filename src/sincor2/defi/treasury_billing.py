"""SINCOR DeFi P41 — DAO Treasury Management: client billing layer.

The P21 treasury swarm (``treasury_dao.py``, catalog SKU P41) manages
allocations, but until now it had no client engagement layer: no
per-client AUM tracking, no fee computation at the SKU's terms, and no
invoicing process — a named blocker on the SKU's path to collecting
(the fee is a percentage of AUM, which has no fixed-price PayPal link
form). This module is that layer. It is records-only: it computes fees
and issues invoice *records*; sending an invoice and collecting on it
are operator actions outside this code (see treasury_billing.md).

SKU terms (catalog, priced 2026-11-16 research):

- Management fee: 1.0% of AUM per year (``MGMT_FEE_BPS = 100``),
  accrued pro-rata to the second on the AUM in force during each
  interval. AUM changes settle the accrual up to the change first, so
  an interval is always charged at the AUM that was actually in force.
- Performance fee: 10% of positive yield above the high-water mark
  (``PERF_FEE_BPS = 1000``), crystallized at invoice time. The
  high-water mark moves only when a performance fee is charged (or an
  invoice crystallizes a new peak), never on a bare snapshot — a dip
  below the mark earns no performance fee until the mark is exceeded
  again.

Money is integer cents end to end; fee math is integer bps arithmetic
floored once per computation, so invoices are exact and reproducible.
Invoice IDs are content-addressed (SHA-256 of the canonical JSON of the
invoice content, the same pattern the treasury swarm uses for holds):
identical engagement history produces identical invoice IDs.

Safety: this module moves no money, imports no signing or networking
code, and touches no chain. It records what a client owes under the
SKU's published terms — nothing more.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

MGMT_FEE_BPS = 100          # 1.0% of AUM per year
PERF_FEE_BPS = 1_000        # 10% of yield above the high-water mark
BPS_DENOM = 10_000
SECONDS_PER_YEAR = 365 * 86_400

STATUS_DRAFT = "DRAFT"
STATUS_ISSUED = "ISSUED"
STATUS_PAID = "PAID"
STATUS_OVERDUE = "OVERDUE"

_TRANSITIONS = {
    STATUS_DRAFT: {STATUS_ISSUED},
    STATUS_ISSUED: {STATUS_PAID, STATUS_OVERDUE},
    STATUS_OVERDUE: {STATUS_PAID},
    STATUS_PAID: set(),
}


class BillingError(ValueError):
    """Base error for billing rule violations."""


def _canonical(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def _content_id(obj: dict) -> str:
    return hashlib.sha256(_canonical(obj)).hexdigest()


@dataclass
class TreasuryEngagement:
    client_id: str
    opened_at: int
    aum_cents: int = 0
    high_water_cents: int = 0
    accrued_mgmt_cents: int = 0
    last_accrual_at: int = 0
    last_invoice_at: Optional[int] = None

    def accrue(self, at: int) -> int:
        """Settle management-fee accrual up to ``at`` at the AUM in force."""
        if at < self.last_accrual_at:
            raise BillingError("accrual time moves backwards")
        elapsed = at - self.last_accrual_at
        fee = 0
        if elapsed and self.aum_cents:
            fee = (
                self.aum_cents * MGMT_FEE_BPS * elapsed
                // (BPS_DENOM * SECONDS_PER_YEAR)
            )
            self.accrued_mgmt_cents += fee
        self.last_accrual_at = at
        return fee

    def snapshot_aum(self, aum_cents: int, at: int) -> None:
        """Record a new AUM. Accrues at the old AUM up to ``at`` first."""
        if aum_cents < 0:
            raise BillingError("AUM cannot be negative")
        self.accrue(at)
        self.aum_cents = aum_cents


@dataclass
class Invoice:
    invoice_id: str
    client_id: str
    period_start: int
    period_end: int
    mgmt_fee_cents: int
    perf_fee_cents: int
    aum_cents: int
    high_water_cents: int
    status: str = STATUS_DRAFT

    @property
    def total_cents(self) -> int:
        return self.mgmt_fee_cents + self.perf_fee_cents


@dataclass
class BillingBook:
    engagements: Dict[str, TreasuryEngagement] = field(default_factory=dict)
    invoices: Dict[str, Invoice] = field(default_factory=dict)

    def open_engagement(
        self, client_id: str, at: Optional[int] = None, aum_cents: int = 0
    ) -> TreasuryEngagement:
        if client_id in self.engagements:
            raise BillingError(f"engagement already open for {client_id!r}")
        opened = int(at if at is not None else time.time())
        eng = TreasuryEngagement(
            client_id=client_id,
            opened_at=opened,
            aum_cents=aum_cents,
            high_water_cents=aum_cents,
            last_accrual_at=opened,
        )
        self.engagements[client_id] = eng
        return eng

    def _engagement(self, client_id: str) -> TreasuryEngagement:
        try:
            return self.engagements[client_id]
        except KeyError:
            raise BillingError(f"no engagement for {client_id!r}") from None

    def snapshot_aum(self, client_id: str, aum_cents: int, at: int) -> None:
        self._engagement(client_id).snapshot_aum(aum_cents, at)

    def generate_invoice(self, client_id: str, at: int) -> Invoice:
        """Crystallize fees for the period ending at ``at``.

        Settles the management accrual, charges the performance fee on
        any gain above the high-water mark, moves the mark to the
        current AUM when it has been exceeded, and resets the accrual.
        A zero-total invoice is a valid statement of account.
        """
        eng = self._engagement(client_id)
        eng.accrue(at)
        gain = max(0, eng.aum_cents - eng.high_water_cents)
        perf_fee = gain * PERF_FEE_BPS // BPS_DENOM
        if eng.aum_cents > eng.high_water_cents:
            eng.high_water_cents = eng.aum_cents

        content = {
            "client_id": client_id,
            "period_start": eng.last_invoice_at if eng.last_invoice_at is not None else eng.opened_at,
            "period_end": at,
            "mgmt_fee_cents": eng.accrued_mgmt_cents,
            "perf_fee_cents": perf_fee,
            "aum_cents": eng.aum_cents,
            "high_water_cents": eng.high_water_cents,
        }
        invoice = Invoice(invoice_id=_content_id(content), **content)
        eng.accrued_mgmt_cents = 0
        eng.last_invoice_at = at
        self.invoices[invoice.invoice_id] = invoice
        return invoice

    def set_invoice_status(self, invoice_id: str, status: str) -> Invoice:
        try:
            invoice = self.invoices[invoice_id]
        except KeyError:
            raise BillingError(f"unknown invoice {invoice_id!r}") from None
        if status not in _TRANSITIONS[invoice.status]:
            raise BillingError(
                f"invoice cannot move {invoice.status} -> {status}"
            )
        invoice.status = status
        return invoice
