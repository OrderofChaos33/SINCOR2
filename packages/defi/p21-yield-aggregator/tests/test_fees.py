"""Fee accounting at the engagement terms: 10% performance, 0% management.

Also pins the distinction between the engagement performance fee and
the allocator's internal 10 bps (0.10%) treasury fee concept.
"""

from __future__ import annotations

from p21_yield_aggregator import YieldAggregator
from p21_yield_aggregator.fees import (
    MANAGEMENT_FEE_RATE,
    PERFORMANCE_FEE_RATE,
    engagement_fee_breakdown,
    management_fee,
    performance_fee,
)


def test_rates_are_the_engagement_terms():
    assert PERFORMANCE_FEE_RATE == 0.10
    assert MANAGEMENT_FEE_RATE == 0.0


def test_performance_fee_is_ten_percent_of_positive_yield():
    assert performance_fee(1_000.0) == 100.0
    assert performance_fee(0.0) == 0.0
    assert performance_fee(-250.0) == 0.0  # no fee on a loss


def test_management_fee_is_always_zero():
    assert management_fee(0.0) == 0.0
    assert management_fee(10_000_000.0) == 0.0
    assert management_fee(10_000_000.0, periods=12.0) == 0.0


def test_breakdown_nets_correctly():
    out = engagement_fee_breakdown(capital_usd=100_000.0, gross_yield_usd=4_500.0)
    assert out["performance_fee_usd"] == 450.0
    assert out["management_fee_usd"] == 0.0
    assert out["total_fees_usd"] == 450.0
    assert out["net_yield_usd"] == 4_050.0


def test_breakdown_on_loss_charges_nothing():
    out = engagement_fee_breakdown(capital_usd=100_000.0, gross_yield_usd=-1_200.0)
    assert out["total_fees_usd"] == 0.0
    assert out["net_yield_usd"] == -1_200.0


def test_internal_treasury_fee_is_not_the_performance_fee():
    """The allocator stamps 10 bps on plans; that is a separate concept
    from the 10% engagement performance fee and must stay 10 bps."""
    agg = YieldAggregator()
    plan = agg.plan_rebalance(capital_usd=10_000, risk_budget=0.30)
    assert plan.fee_to_treasury_bps == 10
    sim = agg.simulate_year_pnl(capital_usd=10_000, risk_budget=0.30)
    if sim["expected_gross_usd"] > 0:
        internal = sim["expected_fee_to_treasury_usd"] / sim["expected_gross_usd"]
        assert abs(internal - 0.001) < 1e-6  # 0.10%, not 10%
