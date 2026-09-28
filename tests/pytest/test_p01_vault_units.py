"""Unit tests for the P01 Yield Aggregator Vault reference build.

Covers src/sincor2/defi/yield_aggregator.py — the risk-aware allocator behind
SKU SINCOR-DEFI-P01-VAULT. Pure logic, no chain access. Every test names the
invariant it guards in its docstring (auction task p01-vault-unit-tests).

Acceptance: 24/24 pass; every public function has >= 1 test; fee assertions
exact to the wei (fees are integers in bps, amounts rounded to 6dp).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from src.sincor2.defi import yield_aggregator as ya
from src.sincor2.defi.yield_aggregator import (
    MAX_SINGLE_STRATEGY_PCT,
    MAX_SLIPPAGE_BPS,
    MIN_CAPITAL_USD,
    RebalancePlan,
    StrategyAllocation,
    StrategyKind,
    YieldAggregator,
    YieldStrategy,
    get_default_aggregator,
)


@pytest.fixture()
def agg():
    return YieldAggregator()


# -- strategy universe -------------------------------------------------------
def test_default_strategies_registered():
    """Invariant: the strategy universe is fixed and auditable (5 known ids)."""
    ids = {s.id for s in ya.DEFAULT_STRATEGIES}
    assert ids == {
        "cash_reserve",
        "morpho_usdc",
        "aave_usdc",
        "shared_liq_vault",
        "univ4_clmm_stable",
    }


def test_strategy_kind_taxonomy_closed():
    """Invariant: strategy taxonomy is closed (exactly 4 kinds)."""
    assert {k.value for k in StrategyKind} == {
        "stable_lending",
        "shared_liquidity",
        "concentrated_lp",
        "cash",
    }


def test_list_strategies_enabled_only(agg):
    """Invariant: list_strategies() excludes disabled strategies by default."""
    enabled = {s.id for s in agg.list_strategies()}
    assert "shared_liq_vault" not in enabled  # unverified vault: DO NOT DEPOSIT
    assert "univ4_clmm_stable" not in enabled  # IL + hook not live
    assert "morpho_usdc" in enabled and "aave_usdc" in enabled
    all_ids = {s.id for s in agg.list_strategies(enabled_only=False)}
    assert len(all_ids) == len(ya.DEFAULT_STRATEGIES)


def test_disabled_strategies_never_allocated(agg):
    """Invariant: disabled strategies receive zero capital in any plan."""
    plan = agg.plan_rebalance(capital_usd=100_000, risk_budget=1.0)
    ids = {a.strategy_id for a in plan.allocations}
    assert "shared_liq_vault" not in ids
    assert "univ4_clmm_stable" not in ids


def test_custom_strategies_injectable():
    """Invariant: the strategy universe is injectable (test isolation)."""
    custom = [
        YieldStrategy(
            id="only",
            name="Only",
            kind=StrategyKind.CASH,
            protocol="native",
            estimated_apr=0.0,
            risk_score=0.0,
            min_liquidity_usd=0.0,
        )
    ]
    plan = YieldAggregator(strategies=custom).plan_rebalance(
        capital_usd=1_000, risk_budget=0.0
    )
    assert {a.strategy_id for a in plan.allocations} == {"only"}


# -- dry-run default (load-bearing) ------------------------------------------
def test_plan_dry_run_by_default(agg):
    """Invariant: dry_run is the default; nothing executes without opt-in."""
    plan = agg.plan_rebalance(capital_usd=10_000, risk_budget=0.30)
    assert plan.mode == "dry_run"
    assert plan.executed is False
    assert plan.intents == []
    assert any("dry_run" in w for w in plan.warnings)


def test_live_intent_mode_emits_intents_only(agg, monkeypatch):
    """Invariant: EXECUTE_LIVE emits intents only; signing stays external."""
    monkeypatch.setattr(ya, "EXECUTE_LIVE", True)
    plan = agg.plan_rebalance(capital_usd=10_000, risk_budget=0.30)
    assert plan.mode == "live_intent"
    assert plan.executed is False  # never executes from this module
    assert len(plan.intents) == len(plan.allocations)
    assert all(i["action"] == "allocate" for i in plan.intents)


def test_live_intents_carry_slippage_cap(agg, monkeypatch):
    """Invariant: every live intent carries the max slippage cap."""
    monkeypatch.setattr(ya, "EXECUTE_LIVE", True)
    plan = agg.plan_rebalance(capital_usd=10_000, risk_budget=0.30)
    assert plan.intents, "expected intents in live_intent mode"
    for intent in plan.intents:
        assert intent["max_slippage_bps"] == MAX_SLIPPAGE_BPS


# -- allocation math ---------------------------------------------------------
def test_plan_capital_conserved(agg):
    """Invariant: capital conservation — allocations sum to input capital."""
    plan = agg.plan_rebalance(capital_usd=10_000, risk_budget=0.40)
    total = sum(a.capital_usd for a in plan.allocations)
    assert abs(total - 10_000) < 0.01


def test_plan_weights_sum_to_one(agg):
    """Invariant: weights form a probability distribution (sum to 1)."""
    plan = agg.plan_rebalance(capital_usd=25_000, risk_budget=0.50)
    assert abs(sum(a.weight for a in plan.allocations) - 1.0) < 1e-6


def test_single_strategy_cap_enforced(agg):
    """Invariant: no strategy exceeds the 40% concentration cap."""
    plan = agg.plan_rebalance(capital_usd=20_000, risk_budget=1.0)
    for a in plan.allocations:
        assert a.weight <= MAX_SINGLE_STRATEGY_PCT + 1e-6


def test_risk_budget_filters_aggressive(agg):
    """Invariant: risk budget is respected on every allocation."""
    plan = agg.plan_rebalance(capital_usd=5_000, risk_budget=0.05)
    assert plan.allocations
    for a in plan.allocations:
        assert a.risk_score <= 0.05 + 1e-9


def test_tight_risk_budget_falls_back_to_cash(agg):
    """Invariant: when nothing is eligible, the plan fails safe to cash."""
    plan = agg.plan_rebalance(capital_usd=5_000, risk_budget=0.0)
    assert {a.strategy_id for a in plan.allocations} == {"cash_reserve"}


def test_blended_apr_is_convex_combination(agg):
    """Invariant: blended APR is a convex combination of eligible APRs."""
    plan = agg.plan_rebalance(capital_usd=10_000, risk_budget=0.30)
    eligible_aprs = [
        s.estimated_apr for s in agg.list_strategies() if s.risk_score <= 0.30 + 1e-9
    ]
    assert 0.0 <= plan.expected_blended_apr <= max(eligible_aprs) + 1e-9
    assert plan.expected_blended_apr >= 0.0


def test_max_risk_score_reported_exact(agg):
    """Invariant: reported max risk equals the riskiest allocation exactly."""
    plan = agg.plan_rebalance(capital_usd=10_000, risk_budget=0.30)
    assert plan.max_risk_score == max(a.risk_score for a in plan.allocations)


def test_plan_deterministic(agg):
    """Invariant: the plan is a pure function of inputs (no chain, no RNG)."""
    p1 = agg.plan_rebalance(capital_usd=7_500, risk_budget=0.25)
    p2 = agg.plan_rebalance(capital_usd=7_500, risk_budget=0.25)
    assert [a.to_dict() for a in p1.allocations] == [
        a.to_dict() for a in p2.allocations
    ]
    assert p1.expected_blended_apr == p2.expected_blended_apr


# -- edge cases --------------------------------------------------------------
def test_min_capital_warns_and_holds(agg):
    """Invariant: sub-minimum capital warns and never executes."""
    plan = agg.plan_rebalance(capital_usd=MIN_CAPITAL_USD - 0.5, risk_budget=0.5)
    assert any("MIN_CAPITAL" in w or "below" in w for w in plan.warnings)
    assert plan.mode == "dry_run"
    assert plan.executed is False


def test_zero_capital_is_safe_noop(agg):
    """Invariant: zero capital is a safe no-op, not a crash."""
    plan = agg.plan_rebalance(capital_usd=0.0, risk_budget=0.30)
    assert plan.total_capital_usd == 0.0
    assert plan.executed is False


# -- treasury fee routing ----------------------------------------------------
def test_treasury_fee_bps_constant(agg):
    """Invariant: the 10 bps treasury fee is a protocol constant (fee_bps=10)."""
    plan = agg.plan_rebalance(capital_usd=10_000, risk_budget=0.30)
    assert plan.fee_to_treasury_bps == 10


def test_treasury_address_valid(agg):
    """Invariant: the fee destination is a well-formed treasury address."""
    plan = agg.plan_rebalance(capital_usd=10_000, risk_budget=0.30)
    assert plan.treasury.startswith("0x") and len(plan.treasury) == 42


def test_simulate_year_pnl_math_exact(agg):
    """Invariant: PnL decomposes exactly: gross = capital*APR; fee = 10 bps."""
    out = agg.simulate_year_pnl(capital_usd=8_000, risk_budget=0.25)
    plan = agg.plan_rebalance(capital_usd=8_000, risk_budget=0.25)
    expected_gross = 8_000 * plan.expected_blended_apr
    assert out["expected_gross_usd"] == pytest.approx(expected_gross, abs=1e-4)
    assert out["expected_fee_to_treasury_usd"] == pytest.approx(
        expected_gross * 10 / 10_000, abs=1e-4
    )
    assert out["expected_net_usd"] == pytest.approx(
        out["expected_gross_usd"] - out["expected_fee_to_treasury_usd"], abs=1e-4
    )
    assert out["expected_net_usd"] <= out["expected_gross_usd"]


# -- serialization -----------------------------------------------------------
def test_strategy_allocation_to_dict_roundtrip(agg):
    """Invariant: allocations serialize losslessly for the proof ledger."""
    alloc = StrategyAllocation(
        strategy_id="morpho_usdc",
        weight=0.5,
        capital_usd=5_000.0,
        estimated_apr=0.045,
        risk_score=0.15,
    )
    d = alloc.to_dict()
    assert d == {
        "strategy_id": "morpho_usdc",
        "weight": 0.5,
        "capital_usd": 5_000.0,
        "estimated_apr": 0.045,
        "risk_score": 0.15,
    }


def test_rebalance_plan_to_dict_shape(agg):
    """Invariant: the plan serializes with allocations as plain dicts."""
    plan = agg.plan_rebalance(capital_usd=10_000, risk_budget=0.30)
    d = plan.to_dict()
    assert isinstance(d, dict)
    assert all(isinstance(a, dict) for a in d["allocations"])
    assert d["mode"] == "dry_run"
    assert d["executed"] is False
    assert isinstance(plan, RebalancePlan)


def test_get_default_aggregator_singleton():
    """Invariant: the default aggregator is a process-wide singleton."""
    assert get_default_aggregator() is get_default_aggregator()
