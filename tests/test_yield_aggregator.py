"""Unit tests for DeFi yield aggregator — pure logic, no chain."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.sincor2.defi.yield_aggregator import (
    YieldAggregator,
    get_default_aggregator,
)


def test_default_strategies_present():
    agg = get_default_aggregator()
    ids = {s.id for s in agg.list_strategies()}
    assert "cash_reserve" in ids
    assert "shared_liq_vault" in ids
    assert "morpho_usdc" in ids


def test_plan_rebalance_sums_to_capital():
    agg = YieldAggregator()
    plan = agg.plan_rebalance(capital_usd=10_000, risk_budget=0.40)
    assert plan.mode == "dry_run"
    assert plan.total_capital_usd == 10_000
    total = sum(a.capital_usd for a in plan.allocations)
    assert abs(total - 10_000) < 0.01
    weight_sum = sum(a.weight for a in plan.allocations)
    assert abs(weight_sum - 1.0) < 1e-6
    assert plan.expected_blended_apr >= 0.0
    assert plan.treasury.startswith("0x")
    assert plan.executed is False
    assert any("dry_run" in w for w in plan.warnings)


def test_risk_budget_excludes_aggressive():
    agg = YieldAggregator()
    # Very tight risk budget should push toward cash / low-risk only
    plan = agg.plan_rebalance(capital_usd=5_000, risk_budget=0.05)
    for a in plan.allocations:
        assert a.risk_score <= 0.05 + 1e-9


def test_single_strategy_cap():
    agg = YieldAggregator()
    plan = agg.plan_rebalance(capital_usd=20_000, risk_budget=1.0)
    for a in plan.allocations:
        assert a.weight <= 0.40 + 1e-6


def test_simulate_year_pnl_structure():
    agg = YieldAggregator()
    out = agg.simulate_year_pnl(capital_usd=8_000, risk_budget=0.25)
    assert out["capital_usd"] == 8_000
    assert "expected_gross_usd" in out
    assert "expected_fee_to_treasury_usd" in out
    assert out["expected_net_usd"] <= out["expected_gross_usd"]
    assert "plan" in out


def test_tiny_capital_still_returns_plan():
    agg = YieldAggregator()
    plan = agg.plan_rebalance(capital_usd=1.0, risk_budget=0.5)
    assert plan.total_capital_usd == 1.0
    # Should warn about min capital
    assert any("MIN_CAPITAL" in w or "below" in w for w in plan.warnings)


def test_cash_loading_window_allocates_shared_liq():
    """CEO 2026-08-17: ~$295 treasury must be eligible for SharedLiquidityVault after min_liquidity lowered to 250."""
    agg = YieldAggregator()
    plan = agg.plan_rebalance(capital_usd=295.0, risk_budget=0.30)
    assert plan.mode == "dry_run"
    ids = {a.strategy_id for a in plan.allocations}
    assert "shared_liq_vault" in ids, "SharedLiquidityVault must be eligible at $295 after min_liquidity=250"
    shared = next(a for a in plan.allocations if a.strategy_id == "shared_liq_vault")
    assert shared.capital_usd > 0
    assert plan.expected_blended_apr > 0.0


def test_morpho_gate_removed_cash_loading():
    """CEO 2026-08-19: Morpho min_liquidity set to 0. ~$310 treasury must be eligible for morpho_usdc."""
    agg = YieldAggregator()
    plan = agg.plan_rebalance(capital_usd=310.0, risk_budget=0.30)
    assert plan.mode == "dry_run"
    ids = {a.strategy_id for a in plan.allocations}
    assert "morpho_usdc" in ids, "morpho_usdc must be eligible after gate removal (min_liquidity=0)"
    morpho = next(a for a in plan.allocations if a.strategy_id == "morpho_usdc")
    assert morpho.capital_usd > 0
    assert plan.expected_blended_apr > 0.0
