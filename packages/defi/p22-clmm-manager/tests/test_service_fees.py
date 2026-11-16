# PACK-2 added coverage: engagement service form — 15% of earned LP
# trading fees (see ENGAGEMENT.md and p22_clmm_manager.service_fees).
# The platform original had no coverage of this commercial form; the core
# module's internal 15 *bps* treasury cut on captured JIT proceeds is a
# different quantity and is covered by the adapted platform tests.
from __future__ import annotations

import sys
from pathlib import Path

PKG_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PKG_SRC) not in sys.path:
    sys.path.insert(0, str(PKG_SRC))

import pytest

from p22_clmm_manager.service_fees import (
    SERVICE_FEE_BPS,
    ServiceFeeLedger,
    net_to_counterparty_wei,
    service_fee_wei,
)


def test_rate_is_exactly_15_percent():
    assert SERVICE_FEE_BPS == 1500


def test_fee_on_round_amount():
    assert service_fee_wei(1_000_000) == 150_000
    assert net_to_counterparty_wei(1_000_000) == 850_000


def test_fee_is_integer_exact_floor():
    # floor(101 * 1500 / 10000) = floor(15.15) = 15; net + fee == earned.
    assert service_fee_wei(101) == 15
    assert service_fee_wei(101) + net_to_counterparty_wei(101) == 101


def test_zero_earned_zero_fee():
    assert service_fee_wei(0) == 0
    assert net_to_counterparty_wei(0) == 0


def test_negative_earned_rejected():
    with pytest.raises(ValueError):
        service_fee_wei(-1)


def test_ledger_accumulates_per_pool_without_drift():
    led = ServiceFeeLedger()
    assert led.record_earned("pool-1", 100) == 15
    # Second recording: fee is recomputed on the total (200 -> 30),
    # so the delta is 15, never a per-record rounding drift.
    assert led.record_earned("pool-1", 100) == 15
    assert led.earned_wei("pool-1") == 200
    assert led.service_fee_wei("pool-1") == 30
    assert led.net_wei("pool-1") == 170


def test_ledger_totals_across_pools():
    led = ServiceFeeLedger()
    led.record_earned("pool-1", 1_000_000)
    led.record_earned("pool-2", 3_000_000)
    totals = led.totals()
    assert totals["earned_lp_fees_wei"] == 4_000_000
    assert totals["service_fee_wei"] == 600_000
    assert totals["net_wei"] == 3_400_000
    assert totals["service_fee_bps"] == 1500


def test_ledger_unknown_pool_is_zero():
    led = ServiceFeeLedger()
    assert led.earned_wei("nope") == 0
    assert led.service_fee_wei("nope") == 0


def test_service_fee_distinct_from_core_treasury_cut():
    # Guard against conflation: core TREASURY_CUT_BPS is 15 bps (0.15%)
    # of captured JIT proceeds; the engagement service fee is 15% of
    # earned LP trading fees — 100x larger rate, different base.
    from p22_clmm_manager.clmm_manager import TREASURY_CUT_BPS

    assert TREASURY_CUT_BPS == 15
    assert SERVICE_FEE_BPS == 100 * TREASURY_CUT_BPS
