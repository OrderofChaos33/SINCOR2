"""Ported legacy tests for the yield aggregator, run against the package.

Provenance: ``tests/test_yield_aggregator.py`` in the SINCOR2 repo
(8 tests). Five tests are ported unchanged in substance. Three tests
encoded expectations the current source no longer satisfies; each was
verified against the source of truth (``yield_aggregator.py`` at base
commit 5d20c8fa) before being corrected here — the package does NOT
change allocator behaviour to satisfy a stale test:

1. ``test_default_strategies_present`` expected ``shared_liq_vault``
   in the *enabled* list. In the source it is ``enabled=False`` (the
   vault address it names is unverified with zero on-chain activity;
   the source says do not deposit). Corrected: assert it is registered
   in the full universe and absent from the enabled list.
2. ``test_tiny_capital_still_returns_plan`` used capital 1.0 and
   expected a below-minimum warning, but ``MIN_CAPITAL_USD`` is 1.0
   and the check is strict ``<``. Corrected: use 0.5 for the warning
   case and assert 1.0 exactly produces no such warning.
3. ``test_cash_loading_window_allocates_shared_liq`` expected a
   disabled strategy to receive an allocation. That contradicts the
   disabled-never-allocated invariant guarded by the P01 audit suite.
   Replaced with ``test_disabled_strategies_never_allocated`` plus a
   Morpho eligibility test at the same small-capital scale.
"""

from __future__ import annotations

from p21_yield_aggregator import YieldAggregator, get_default_aggregator
from p21_yield_aggregator.yield_aggregator import MIN_CAPITAL_USD


def test_default_strategies_present():
    agg = get_default_aggregator()
    enabled_ids = {s.id for s in agg.list_strategies()}
    all_ids = {s.id for s in agg.list_strategies(enabled_only=False)}
    assert "cash_reserve" in enabled_ids
    assert "morpho_usdc" in enabled_ids
    # Corrected (see module docstring, item 1): registered but disabled.
    assert "shared_liq_vault" in all_ids
    assert "shared_liq_vault" not in enabled_ids


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
    # Corrected (item 2): below the 1.0 floor warns; exactly at it does not.
    plan = agg.plan_rebalance(capital_usd=0.5, risk_budget=0.5)
    assert plan.total_capital_usd == 0.5
    assert any("MIN_CAPITAL" in w or "below" in w for w in plan.warnings)
    at_floor = agg.plan_rebalance(capital_usd=MIN_CAPITAL_USD, risk_budget=0.5)
    assert not any("below MIN_CAPITAL" in w for w in at_floor.warnings)


def test_disabled_strategies_never_allocated():
    """Corrected replacement for the stale shared-liq allocation test (item 3)."""
    agg = YieldAggregator()
    disabled = {s.id for s in agg.list_strategies(enabled_only=False) if not s.enabled}
    assert disabled  # default universe has disabled rows to guard
    for capital in (295.0, 5_000.0, 100_000.0):
        plan = agg.plan_rebalance(capital_usd=capital, risk_budget=1.0)
        allocated = {a.strategy_id for a in plan.allocations}
        assert allocated.isdisjoint(disabled)


def test_morpho_gate_removed_cash_loading():
    """Morpho min_liquidity is 0; ~$310 must be eligible for morpho_usdc."""
    agg = YieldAggregator()
    plan = agg.plan_rebalance(capital_usd=310.0, risk_budget=0.30)
    assert plan.mode == "dry_run"
    ids = {a.strategy_id for a in plan.allocations}
    assert "morpho_usdc" in ids
    morpho = next(a for a in plan.allocations if a.strategy_id == "morpho_usdc")
    assert morpho.capital_usd > 0
    assert plan.expected_blended_apr > 0.0


# -- Added coverage: deposit routing and yield-source selection --------------


def test_deposit_routing_prefers_risk_adjusted_yield():
    """Selection scores APR / (1 + risk) with a 0.15 floor score for
    cash: Morpho (0.045/1.15 = 0.0391) must receive at least as much
    as Aave (0.038/1.12 = 0.0339), and the 40% cap binds every row —
    including cash, whose high floor score is capped like any other."""
    agg = YieldAggregator()
    plan = agg.plan_rebalance(capital_usd=10_000, risk_budget=1.0)
    by_id = {a.strategy_id: a for a in plan.allocations}
    assert by_id["morpho_usdc"].weight >= by_id["aave_usdc"].weight
    assert all(a.weight <= 0.40 + 1e-6 for a in plan.allocations)


def test_selection_respects_risk_budget_boundary():
    """A budget between Aave (0.12) and Morpho (0.15) admits Aave only."""
    agg = YieldAggregator()
    plan = agg.plan_rebalance(capital_usd=10_000, risk_budget=0.13)
    ids = {a.strategy_id for a in plan.allocations}
    assert "aave_usdc" in ids
    assert "morpho_usdc" not in ids


def test_zero_capital_plan_is_safe():
    agg = YieldAggregator()
    plan = agg.plan_rebalance(capital_usd=0.0, risk_budget=0.30)
    assert plan.executed is False
    assert sum(a.capital_usd for a in plan.allocations) == 0.0
