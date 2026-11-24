"""P44 — SocialFi revenue-share productization: the platform-leg book.

Covers src/sincor2/defi/p24/platform_book.py — accrual of the platform
leg (4940 bps of the 10% trade fee) with the 10% staking-rewards
earmark carved exactly, per-epoch counterparty statements, >= $10
batch settlement to the configured beneficiary attributed
oldest-epoch-first, and whole-fee conservation:
creator + treasury (incl. dust) + staking + share == fee.
"""

import pytest

from src.sincor2.defi.p24 import PARAMS
from src.sincor2.defi.p24.platform_book import PlatformShareBook
from src.sincor2.defi.p24.split import split_fee

BENEFICIARY = "0x00000000000000000000000000000000000BEEF1"
EPOCH = PARAMS["epoch_s"]
T0 = EPOCH * 10  # start cleanly inside epoch 10


def _book():
    return PlatformShareBook(beneficiary=BENEFICIARY)


def test_platform_leg_carves_staking_earmark_exactly():
    book = _book()
    acc = book.accrue("CREA", 1_000_000, now=T0)
    assert acc.platform_wei == 494_000       # 4940 bps of the fee
    assert acc.staking_wei == 49_400         # 10% of the leg
    assert acc.share_wei == 444_600          # the share due
    assert book.outstanding("CREA") == 444_600


def test_conservation_across_the_whole_fee():
    book = _book()
    for fee in (1, 7, 999, 1_000_000, 123_456_789):
        acc = book.accrue("CREA", fee, now=T0)
        legs = split_fee(fee)
        assert (
            legs.creator_wei + legs.treasury_wei + acc.staking_wei + acc.share_wei
            == fee
        )


def test_statement_aggregates_per_epoch():
    book = _book()
    book.accrue("CREA", 1_000_000, now=T0)
    book.accrue("CREA", 1_000_000, now=T0 + 60)
    book.accrue("CREA", 2_000_000, now=T0 + EPOCH)  # next epoch
    s10 = book.statement("CREA", 10)
    assert s10.gross_fees_wei == 2_000_000
    assert s10.platform_leg_wei == 988_000
    assert s10.share_due_wei == 889_200
    assert s10.outstanding_wei == 889_200
    s11 = book.statement("CREA", 11)
    assert s11.gross_fees_wei == 2_000_000
    assert s11.share_due_wei == 889_200


def test_settlement_below_floor_is_none_then_settles_to_beneficiary():
    book = _book()
    book.accrue("CREA", 10_000, now=T0)  # share 44,460 wei << $10 floor
    assert book.settle("CREA") is None
    # $10 floor in 6-decimal USDC wei = 10_000_000; accrue fees until the
    # share clears it: each 1_000_000 fee adds 444_600 share.
    for _ in range(30):
        book.accrue("CREA", 1_000_000, now=T0)
    settlement = book.settle("CREA")
    assert settlement is not None
    assert settlement.to == BENEFICIARY
    assert settlement.amount_usdc_wei == book.statement("CREA", 10).settled_wei
    assert book.outstanding("CREA") == 0
    assert book.statement("CREA", 10).outstanding_wei == 0


def test_settlement_attributes_oldest_epoch_first():
    book = _book()
    for _ in range(30):
        book.accrue("CREA", 1_000_000, now=T0)            # epoch 10
    for _ in range(30):
        book.accrue("CREA", 1_000_000, now=T0 + EPOCH)    # epoch 11
    book.settle("CREA")
    assert book.statement("CREA", 10).outstanding_wei == 0
    assert book.statement("CREA", 11).outstanding_wei == 0
    assert book.statement("CREA", 10).settled_wei == 30 * 444_600
    assert book.statement("CREA", 11).settled_wei == 30 * 444_600


def test_negative_fee_and_missing_beneficiary_raise():
    book = _book()
    with pytest.raises(ValueError):
        book.accrue("CREA", -1, now=T0)
    with pytest.raises(ValueError):
        PlatformShareBook(beneficiary="")
