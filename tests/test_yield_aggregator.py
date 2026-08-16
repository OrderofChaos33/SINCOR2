"""Enterprise unit tests for YieldAggregator — pure, no network."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from sincor2.defi.yield_aggregator import (  # noqa: E402
    YieldAggregator,
    get_default_aggregator,
)


def test_plan_rebalance_basic():
    agg = YieldAggregator()
    plan = agg.plan_rebalance(capital_usd=325.0, risk_budget=0.30)
    assert plan.total_capital_usd == 325.0
    assert plan.mode == "dry_run"
    assert len(plan.allocations) >= 1
    assert plan.expected_blended_apr >= 0.0
    assert plan.max_risk_score <= 0.30 + 1e-6
    assert plan.treasury.startswith("0x")
    assert plan.plan_id.startswith("yp-")
    assert "source" in plan.audit
    weights = sum(a.weight for a in plan.allocations)
    assert abs(weights - 1.0) < 1e-5


def test_concentration_cap():
    agg = YieldAggregator()
    plan = agg.plan_rebalance(capital_usd=1000.0, risk_budget=0.40)
    for a in plan.allocations:
        assert a.weight <= 0.35 + 1e-6


def test_cash_floor_small_capital():
    agg = YieldAggregator()
    plan = agg.plan_rebalance(capital_usd=8.0, risk_budget=0.20)
    assert any(a.strategy_id == "cash_reserve" for a in plan.allocations)


def test_simulate_year_pnl():
    agg = get_default_aggregator()
    result = agg.simulate_year_pnl(325.0)
    assert "expected_gross_usd" in result
    assert "expected_fee_to_treasury_usd" in result
    assert result["capital_usd"] == 325.0
    assert result["blended_apr"] >= 0.0
    assert "plan_id" in result


def test_negative_capital_raises():
    agg = YieldAggregator()
    try:
        agg.plan_rebalance(capital_usd=-1.0)
        assert False, "should have raised"
    except ValueError:
        pass


if __name__ == "__main__":
    test_plan_rebalance_basic()
    test_concentration_cap()
    test_cash_floor_small_capital()
    test_simulate_year_pnl()
    test_negative_capital_raises()
    print("All YieldAggregator tests passed.")
