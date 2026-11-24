"""P41 — DAO Treasury Management billing layer.

Covers src/sincor2/defi/treasury_billing.py at the SKU's published
terms: 1.0% of AUM per year management fee (pro-rata, settled at AUM
changes), 10% performance fee above a high-water mark that crystallizes
only at invoice time, content-addressed invoice IDs, and the
DRAFT -> ISSUED -> PAID/OVERDUE invoice lifecycle. All money is integer
cents; every fee assertion is exact.
"""

import pytest

from src.sincor2.defi.treasury_billing import (
    MGMT_FEE_BPS,
    PERF_FEE_BPS,
    BillingBook,
    BillingError,
)

T0 = 1_700_000_000
YEAR = 365 * 86_400
HALF_YEAR = YEAR // 2
AUM = 100_000_000  # $1,000,000.00 in cents


def _book(aum=AUM):
    book = BillingBook()
    book.open_engagement("dao-1", at=T0, aum_cents=aum)
    return book


def test_sku_terms_constants():
    assert MGMT_FEE_BPS == 100    # 1.0% of AUM / yr
    assert PERF_FEE_BPS == 1000   # 10% above the high-water mark


def test_management_fee_full_year():
    book = _book()
    inv = book.generate_invoice("dao-1", at=T0 + YEAR)
    assert inv.mgmt_fee_cents == 1_000_000  # 1% of $1,000,000 = $10,000
    assert inv.perf_fee_cents == 0
    assert inv.total_cents == 1_000_000


def test_management_fee_half_year_prorata():
    book = _book()
    inv = book.generate_invoice("dao-1", at=T0 + HALF_YEAR)
    assert inv.mgmt_fee_cents == 500_000


def test_accrual_uses_aum_in_force_per_interval():
    book = _book()
    book.snapshot_aum("dao-1", 200_000_000, at=T0 + HALF_YEAR)
    inv = book.generate_invoice("dao-1", at=T0 + YEAR)
    # 1% * (0.5yr * $1M + 0.5yr * $2M) = $5,000 + $10,000
    assert inv.mgmt_fee_cents == 1_500_000


def test_performance_fee_above_high_water_mark():
    book = _book()
    book.snapshot_aum("dao-1", 120_000_000, at=T0 + 30 * 86_400)
    inv = book.generate_invoice("dao-1", at=T0 + 30 * 86_400)
    assert inv.perf_fee_cents == 2_000_000  # 10% of the $200,000 gain
    assert inv.high_water_cents == 120_000_000


def test_no_performance_fee_until_mark_exceeded_again():
    book = _book()
    book.snapshot_aum("dao-1", 120_000_000, at=T0 + 10)
    book.generate_invoice("dao-1", at=T0 + 10)  # crystallizes HWM = $1.2M
    book.snapshot_aum("dao-1", 110_000_000, at=T0 + 20)
    inv = book.generate_invoice("dao-1", at=T0 + 20)
    assert inv.perf_fee_cents == 0  # below the mark
    book.snapshot_aum("dao-1", 120_000_000, at=T0 + 30)
    inv = book.generate_invoice("dao-1", at=T0 + 30)
    assert inv.perf_fee_cents == 0  # back AT the mark is not above it
    book.snapshot_aum("dao-1", 130_000_000, at=T0 + 40)
    inv = book.generate_invoice("dao-1", at=T0 + 40)
    assert inv.perf_fee_cents == 1_000_000  # 10% of the $100,000 above


def test_invoice_ids_are_content_addressed_and_deterministic():
    def run():
        book = _book()
        book.snapshot_aum("dao-1", 120_000_000, at=T0 + HALF_YEAR)
        return book.generate_invoice("dao-1", at=T0 + YEAR)

    assert run().invoice_id == run().invoice_id


def test_accrual_resets_at_invoice():
    book = _book()
    book.generate_invoice("dao-1", at=T0 + YEAR)
    inv = book.generate_invoice("dao-1", at=T0 + YEAR)
    assert inv.mgmt_fee_cents == 0
    assert inv.total_cents == 0  # zero statement of account


def test_invoice_lifecycle_transitions():
    book = _book()
    inv = book.generate_invoice("dao-1", at=T0 + YEAR)
    assert inv.status == "DRAFT"
    book.set_invoice_status(inv.invoice_id, "ISSUED")
    book.set_invoice_status(inv.invoice_id, "OVERDUE")
    book.set_invoice_status(inv.invoice_id, "PAID")
    with pytest.raises(BillingError):
        book.set_invoice_status(inv.invoice_id, "ISSUED")


def test_invalid_transitions_and_unknowns_raise():
    book = _book()
    inv = book.generate_invoice("dao-1", at=T0 + YEAR)
    with pytest.raises(BillingError):
        book.set_invoice_status(inv.invoice_id, "PAID")  # DRAFT -> PAID skips ISSUED
    with pytest.raises(BillingError):
        book.set_invoice_status("nope", "ISSUED")
    with pytest.raises(BillingError):
        book.generate_invoice("ghost-dao", at=T0)
    with pytest.raises(BillingError):
        book.open_engagement("dao-1", at=T0)
