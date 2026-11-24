"""P25 client statements & invoices: the product layer on the FeeLedger.

``fees.FeeLedger`` accrues the per-user management fee wei-exact and
settles it to treasury in >= $10 batches — but a client of the
Agent-Managed Portfolio SKU (P45) never sees any of it: there was no
statement, no invoice, no period accounting a client could reconcile.
This module is that layer (P45 productization):

- :class:`StatementBook` wraps a FeeLedger and journals every accrual
  and settlement it delegates, with timestamps (the ledger's own
  history carries none).
- :meth:`StatementBook.statement` renders a per-client period
  statement: accruals in the window, settlements in the window, and
  the outstanding balance — every figure traceable to the ledger.
- :meth:`StatementBook.generate_invoice` issues the client-facing
  invoice record for a period (content-addressed ID, the repo's
  hold/invoice pattern), refusing overlapping windows so a period can
  never be billed twice. Lifecycle: DRAFT -> ISSUED -> PAID / OVERDUE.

RATE NOTE (founder ratification item, see PRODUCTIZATION.md): the
ledger accrues at ``PARAMS["fee_bps"]`` = 10 bps p.a. The SKU's catalog
price is 0.6% of AUM per year (60 bps). This layer reports the rate
actually applied — statements and invoices carry ``fee_bps`` from
PARAMS — and the discrepancy is flagged for the founder to ratify;
nothing here silently reprices the product.

Records-only, like the rest of P25: advisory + accounting; no keys, no
broadcasts, no transfers are executed here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import PARAMS
from .fees import FeeAccrual, FeeLedger, Settlement

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


class StatementError(ValueError):
    """Statement/invoice rule violation."""


@dataclass(frozen=True)
class ClientStatement:
    user_id: str
    period_start: float
    period_end: float
    denomination: str
    fee_bps: int  # the rate actually applied (PARAMS), never a quoted rate
    accrual_count: int
    total_fee_token_wei: int
    total_fee_usd: float
    settled_token_wei: int
    settled_usd: float
    outstanding_token_wei: int
    outstanding_usd: float


@dataclass
class ClientInvoice:
    invoice_id: str
    user_id: str
    period_start: float
    period_end: float
    denomination: str
    fee_bps: int
    amount_token_wei: int
    amount_usd: float
    status: str = STATUS_DRAFT

    @property
    def total_usd(self) -> float:
        return self.amount_usd


class StatementBook:
    """Journaling wrapper over FeeLedger producing statements/invoices."""

    def __init__(self, ledger: FeeLedger | None = None) -> None:
        self.ledger = ledger if ledger is not None else FeeLedger()
        self._accrual_journal: List[Tuple[float, FeeAccrual]] = []
        self._settlement_journal: List[Tuple[float, Settlement]] = []
        self.invoices: Dict[str, ClientInvoice] = {}

    # -- delegated ledger operations (journaled) -------------------------

    def accrue(
        self,
        user_id: str,
        aum_usd: float,
        tick_s: float,
        ts: float,
        denomination: str = "AXM",
        price_usd: float = 1.0,
    ) -> FeeAccrual:
        accrual = self.ledger.accrue(
            user_id, aum_usd, tick_s, denomination=denomination, price_usd=price_usd
        )
        self._accrual_journal.append((ts, accrual))
        return accrual

    def settle(self, user_id: str, ts: float) -> Optional[Settlement]:
        settlement = self.ledger.settle(user_id)
        if settlement is not None:
            self._settlement_journal.append((ts, settlement))
        return settlement

    # -- statements --------------------------------------------------------

    def statement(
        self, user_id: str, period_start: float, period_end: float
    ) -> ClientStatement:
        if period_end <= period_start:
            raise StatementError("statement period is empty")
        accruals = [
            a for ts, a in self._accrual_journal
            if a.user_id == user_id and period_start <= ts < period_end
        ]
        settlements = [
            s for ts, s in self._settlement_journal
            if s.user_id == user_id and period_start <= ts < period_end
        ]
        all_accrued_wei = sum(
            a.fee_token_wei for _, a in self._accrual_journal if a.user_id == user_id
        )
        all_accrued_usd = sum(
            a.fee_usd for _, a in self._accrual_journal if a.user_id == user_id
        )
        all_settled_wei = sum(
            s.amount_token_wei for _, s in self._settlement_journal if s.user_id == user_id
        )
        all_settled_usd = sum(
            s.amount_usd for _, s in self._settlement_journal if s.user_id == user_id
        )
        denomination = accruals[-1].denomination if accruals else "AXM"
        return ClientStatement(
            user_id=user_id,
            period_start=period_start,
            period_end=period_end,
            denomination=denomination,
            fee_bps=PARAMS["fee_bps"],
            accrual_count=len(accruals),
            total_fee_token_wei=sum(a.fee_token_wei for a in accruals),
            total_fee_usd=sum(a.fee_usd for a in accruals),
            settled_token_wei=sum(s.amount_token_wei for s in settlements),
            settled_usd=sum(s.amount_usd for s in settlements),
            outstanding_token_wei=all_accrued_wei - all_settled_wei,
            outstanding_usd=all_accrued_usd - all_settled_usd,
        )

    # -- invoices ----------------------------------------------------------

    def generate_invoice(
        self, user_id: str, period_start: float, period_end: float
    ) -> ClientInvoice:
        for existing in self.invoices.values():
            if (
                existing.user_id == user_id
                and period_start < existing.period_end
                and existing.period_start < period_end
            ):
                raise StatementError(
                    f"period overlaps invoice {existing.invoice_id} for {user_id!r}"
                )
        stmt = self.statement(user_id, period_start, period_end)
        content = {
            "user_id": user_id,
            "period_start": period_start,
            "period_end": period_end,
            "denomination": stmt.denomination,
            "fee_bps": stmt.fee_bps,
            "amount_token_wei": stmt.total_fee_token_wei,
            "amount_usd": round(stmt.total_fee_usd, 6),
        }
        invoice_id = hashlib.sha256(
            json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        invoice = ClientInvoice(invoice_id=invoice_id, **content)
        self.invoices[invoice_id] = invoice
        return invoice

    def set_invoice_status(self, invoice_id: str, status: str) -> ClientInvoice:
        try:
            invoice = self.invoices[invoice_id]
        except KeyError:
            raise StatementError(f"unknown invoice {invoice_id!r}") from None
        if status not in _TRANSITIONS[invoice.status]:
            raise StatementError(
                f"invoice cannot move {invoice.status} -> {status}"
            )
        invoice.status = status
        return invoice
