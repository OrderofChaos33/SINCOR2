"""P45 — Agent-Managed Portfolio productization: statements & invoices.

Covers src/sincor2/defi/p25/statements.py on top of fees.FeeLedger —
period statements journaled from real accruals/settlements, invoices
with overlap refusal and lifecycle, and the applied-rate guarantee:
statements carry PARAMS["fee_bps"] (10 bps in code) — the rate the
ledger actually charges — while the catalog's 60 bps price awaits
founder ratification (see p25/PRODUCTIZATION.md). This suite pins the
code rate so the discrepancy can never drift silently.
"""

import pytest

from src.sincor2.defi.p25 import PARAMS
from src.sincor2.defi.p25.fees import SECONDS_PER_YEAR
from src.sincor2.defi.p25.statements import StatementBook, StatementError

T0 = 1_700_000_000.0
DAY = 86_400.0


def _book_with_accruals():
    book = StatementBook()
    # Two ticks inside the window, one outside it.
    book.accrue("user-1", 100_000.0, DAY, ts=T0 + DAY, price_usd=1.0)
    book.accrue("user-1", 100_000.0, DAY, ts=T0 + 2 * DAY, price_usd=1.0)
    book.accrue("user-1", 100_000.0, DAY, ts=T0 + 40 * DAY, price_usd=1.0)
    return book


def test_statement_aggregates_only_the_window():
    book = _book_with_accruals()
    stmt = book.statement("user-1", T0, T0 + 30 * DAY)
    assert stmt.accrual_count == 2
    # 10 bps p.a. on $100k for 2 days total, in AXM wei at $1.
    expected_usd = 100_000.0 * (10 / 10_000) * (2 * DAY / SECONDS_PER_YEAR)
    assert stmt.total_fee_usd == pytest.approx(expected_usd, rel=1e-9)
    assert stmt.outstanding_token_wei == book.ledger.accrued("user-1")


def test_statement_carries_the_applied_rate_not_the_catalog_rate():
    book = _book_with_accruals()
    stmt = book.statement("user-1", T0, T0 + 30 * DAY)
    assert stmt.fee_bps == PARAMS["fee_bps"] == 10
    # Catalog price is 60 bps (0.6% AUM/yr) — pinned here so the
    # discrepancy stays visible until the founder ratifies one number.
    assert stmt.fee_bps != 60


def test_full_year_at_code_rate_is_exact():
    book = StatementBook()
    book.accrue("user-1", 100_000.0, float(SECONDS_PER_YEAR), ts=T0, price_usd=1.0)
    stmt = book.statement("user-1", T0 - 1, T0 + 1)
    assert stmt.total_fee_usd == pytest.approx(100.0, abs=1e-6)  # 10bps of $100k
    assert stmt.total_fee_token_wei == 100 * 10**18


def test_settlement_reconciles_outstanding():
    book = StatementBook()
    # Accrue enough to clear the $10 settlement floor.
    book.accrue("user-1", 1_000_000.0, float(SECONDS_PER_YEAR), ts=T0, price_usd=1.0)
    settlement = book.settle("user-1", ts=T0 + 1)
    assert settlement is not None
    stmt = book.statement("user-1", T0 - 1, T0 + 2)
    assert stmt.settled_usd == pytest.approx(1000.0, abs=1e-6)
    assert stmt.outstanding_token_wei == 0
    assert stmt.outstanding_usd == pytest.approx(0.0, abs=1e-6)


def test_invoice_overlap_refused_and_lifecycle():
    book = _book_with_accruals()
    inv = book.generate_invoice("user-1", T0, T0 + 30 * DAY)
    assert inv.amount_usd > 0
    assert inv.fee_bps == 10
    with pytest.raises(StatementError):
        book.generate_invoice("user-1", T0 + 15 * DAY, T0 + 45 * DAY)
    book.set_invoice_status(inv.invoice_id, "ISSUED")
    book.set_invoice_status(inv.invoice_id, "PAID")
    with pytest.raises(StatementError):
        book.set_invoice_status(inv.invoice_id, "ISSUED")


def test_invoice_ids_deterministic_for_identical_history():
    def run():
        book = StatementBook()
        book.accrue("user-1", 100_000.0, DAY, ts=T0 + DAY, price_usd=1.0)
        return book.generate_invoice("user-1", T0, T0 + 30 * DAY)

    assert run().invoice_id == run().invoice_id


def test_denomination_carried_through():
    book = StatementBook()
    book.accrue("user-1", 100_000.0, DAY, ts=T0, denomination="SINC", price_usd=2.0)
    stmt = book.statement("user-1", T0 - 1, T0 + 1)
    assert stmt.denomination == "SINC"
