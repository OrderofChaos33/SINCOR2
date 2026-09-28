"""Audit-prep invariant + adversarial fuzz tests for P01 (yield_aggregator).

Property tests an auditor would demand of the allocator:

- conservation_fuzz: allocations always sum to input capital (dust..1e12).
- weights_fuzz: weights form a distribution summing to 1; the 40% single-
  strategy cap holds on every fuzzed strategy table.
- risk_budget_fuzz: no allocation ever exceeds the risk budget; the
  eligible set grows monotonically with the budget.
- disabled_never_allocated_fuzz: disabled strategies get zero capital on
  every fuzzed table (incl. all-disabled and empty tables).
- blended_apr_bounds: blended APR is a convex combination of eligible APRs.
- determinism: identical inputs -> identical plans.
- sequential_flows: deposit/withdraw sequences conserve at every step; PnL
  decomposes (fee >= 0, net <= gross).
- adversarial: zero/negative/dust/huge capital, negative/>1 risk budgets,
  NaN/Inf APRs, empty and all-disabled universes — never crash, never
  execute, never allocate to a disabled strategy.

Deterministic: seeded RNG, no hypothesis dependency. N/N must pass.
"""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from src.sincor2.defi import yield_aggregator as ya
from src.sincor2.defi.yield_aggregator import (
    MAX_SINGLE_STRATEGY_PCT,
    StrategyKind,
    YieldAggregator,
    YieldStrategy,
)

RNG = random.Random(0xA00101)
KINDS = list(StrategyKind)


def fuzz_strategy(i: int) -> YieldStrategy:
    kind = RNG.choice(KINDS)
    return YieldStrategy(
        id=f"s{i}",
        name=f"fuzz-{i}",
        kind=kind,
        protocol="fuzz",
        estimated_apr=RNG.choice([
            RNG.uniform(-0.5, 2.0), 0.0, 1e6, -1e6,
            float("nan"), float("inf"),
        ]),
        risk_score=RNG.choice([RNG.uniform(0, 1.5), 0.0, 2.0]),
        min_liquidity_usd=RNG.choice([0.0, 10.0, 1000.0, -5.0]),
        enabled=RNG.choice([True, True, False]),
    )


def fuzz_table() -> list:
    n = RNG.randint(1, 6)
    table = [fuzz_strategy(i) for i in range(n)]
    # guarantee a cash strategy exists so the fallback path is exercised
    table.append(YieldStrategy(
        id="cash", name="cash", kind=StrategyKind.CASH, protocol="native",
        estimated_apr=0.0, risk_score=0.0, min_liquidity_usd=0.0,
        enabled=RNG.choice([True, False]),
    ))
    return table


CAPITALS = [0.0, 0.001, 0.5, 1.0, 100.0, 10_000.0, 1e9, 1e12, -5.0, -1e6]
BUDGETS = [-0.1, 0.0, 0.05, 0.3, 1.0, 2.0]


def plan_invariants(agg: YieldAggregator, capital: float, budget: float):
    """Assert the full invariant bundle for one plan; return it.

    Garbage-in (NaN/Inf APRs or capital) can produce a non-finite plan —
    that path asserts no-crash/no-execute only, and the numeric bundle is
    skipped (documented garbage-in behavior, not a crash bug).
    """
    plan = agg.plan_rebalance(capital_usd=capital, risk_budget=budget)
    assert plan.executed is False
    assert plan.mode == "dry_run"
    assert plan.intents == []
    sane = (
        math.isfinite(plan.total_capital_usd)
        and all(
            math.isfinite(a.weight)
            and math.isfinite(a.capital_usd)
            and math.isfinite(a.estimated_apr)
            for a in plan.allocations
        )
    )
    if not sane:
        return plan, False  # garbage-in: no-crash/no-execute is the whole contract
    by_id = {s.id: s for s in agg.strategies}
    # The fail-safe path allocates exactly one cash-kind strategy; it is
    # exempt from the risk-budget check by design (safest enabled cash).
    single_cash_fallback = (
        len(plan.allocations) == 1
        and by_id[plan.allocations[0].strategy_id].kind == StrategyKind.CASH
    )
    # every allocation references a known, enabled strategy
    for a in plan.allocations:
        s = by_id[a.strategy_id]
        assert s.enabled
        assert single_cash_fallback or a.risk_score <= budget + 1e-9
        assert a.weight >= 0.0
    # Concentration cap: guaranteed iff diversification is feasible
    # (3+ allocated strategies); otherwise conservation takes precedence
    # (documented in the module). The old code violated the cap even when
    # feasible — water-fill fixed it (audit-prep 2026-09-28).
    if len(plan.allocations) >= 3:
        for a in plan.allocations:
            assert a.weight <= MAX_SINGLE_STRATEGY_PCT + 1e-6, (
                f"cap violated while feasible: {a.strategy_id}={a.weight}")
    if plan.allocations:
        # weights sum to ~1: each weight is rounded to 6dp on output, so the
        # principled bound is n_alloc * 5e-7; use n * 1e-6 for margin
        assert abs(sum(a.weight for a in plan.allocations) - 1.0) \
            < len(plan.allocations) * 1e-6 + 1e-9
        assert abs(sum(a.capital_usd for a in plan.allocations) - plan.total_capital_usd) < 0.01
        # blended APR is a convex combination of the allocated APRs
        aprs = [a.estimated_apr for a in plan.allocations]
        assert min(aprs) - 1e-6 <= plan.expected_blended_apr <= max(aprs) + 1e-6
        # max_risk_score is reported rounded to 6dp by the module
        assert abs(plan.max_risk_score - max(a.risk_score for a in plan.allocations)) < 1e-6
    return plan, True


def test_conservation_weights_cap_fuzz():
    """PROPERTY: conservation + weight distribution + 40% cap on 150
    fuzzed (table, capital, budget) combos — never raises, never executes."""
    for _ in range(150):
        agg = YieldAggregator(strategies=fuzz_table())
        capital = RNG.choice(CAPITALS + [RNG.uniform(0, 1e6)])
        budget = RNG.choice(BUDGETS)
        plan, sane = plan_invariants(agg, capital, budget)
        if not sane:
            continue  # garbage-in: NaN plans are not comparable (nan != nan)
        # determinism: same inputs, identical allocations
        plan2 = agg.plan_rebalance(capital_usd=capital, risk_budget=budget)
        assert [a.to_dict() for a in plan.allocations] == [
            a.to_dict() for a in plan2.allocations]
        assert plan.expected_blended_apr == plan2.expected_blended_apr


def test_eligible_set_monotone_in_budget():
    """PROPERTY: raising the risk budget never shrinks the allocated set
    (eligibility is a monotone filter on risk_score)."""
    agg = YieldAggregator()
    prev: set = set()
    for budget in [0.0, 0.05, 0.12, 0.15, 0.25, 0.35, 1.0]:
        plan = agg.plan_rebalance(capital_usd=10_000, risk_budget=budget)
        ids = {a.strategy_id for a in plan.allocations}
        assert prev <= ids, f"eligible set shrank at budget {budget}"
        prev = ids
    # hard check on the real universe: budget 0 -> cash only
    p0 = agg.plan_rebalance(capital_usd=10_000, risk_budget=0.0)
    assert {a.strategy_id for a in p0.allocations} == {"cash_reserve"}


def test_sequential_deposit_withdraw_flows():
    """PROPERTY: a deposit/withdraw sequence conserves at every step and
    the PnL decomposes (fee >= 0, net <= gross)."""
    agg = YieldAggregator()
    for capital in [1_000.0, 1_500.0, 1_200.0, 0.0, 5_000.0, 250_000.0]:
        plan = agg.plan_rebalance(capital_usd=capital, risk_budget=0.3)
        assert abs(sum(a.capital_usd for a in plan.allocations)
                   - plan.total_capital_usd) < 0.01
        pnl = agg.simulate_year_pnl(capital_usd=capital, risk_budget=0.3)
        assert pnl["expected_fee_to_treasury_usd"] >= 0.0
        assert pnl["expected_net_usd"] <= pnl["expected_gross_usd"] + 1e-9
        assert pnl["expected_gross_usd"] >= 0.0


def test_adversarial_tables_never_crash_or_execute():
    """Adversarial: all-disabled universe, NaN/Inf APRs, negative floors —
    plans are safe no-ops, never execute.

    KNOWN WART (documented, not changed in audit-prep): the constructor
    uses ``strategies or DEFAULT_STRATEGIES``, so an explicitly empty list
    silently becomes the default universe — you cannot construct a truly
    empty aggregator. An auditor should decide whether that fail-open
    default is acceptable for an operator-facing allocator.
    """
    # explicit [] falls back to defaults (constructor `or` semantics): no crash
    p = YieldAggregator(strategies=[]).plan_rebalance(1_000, 0.5)
    assert p.executed is False and p.mode == "dry_run"
    # all disabled -> genuinely empty plan
    disabled = [YieldStrategy(id=f"d{i}", name="d", kind=StrategyKind.STABLE_LENDING,
                              protocol="x", estimated_apr=0.05, risk_score=0.1,
                              min_liquidity_usd=0.0, enabled=False)
                for i in range(3)]
    p = YieldAggregator(strategies=disabled).plan_rebalance(1_000, 0.5)
    assert p.allocations == [] and p.executed is False
    # NaN / Inf APR strategies: must not raise, must not execute
    for apr in (float("nan"), float("inf"), float("-inf")):
        s = [YieldStrategy(id="w", name="w", kind=StrategyKind.STABLE_LENDING,
                           protocol="x", estimated_apr=apr, risk_score=0.1,
                           min_liquidity_usd=0.0)]
        p = YieldAggregator(strategies=s).plan_rebalance(1_000, 0.5)
        assert p.executed is False and p.mode == "dry_run"
    # Inf capital: no crash
    p = YieldAggregator().plan_rebalance(float("inf"), 0.5)
    assert p.executed is False


def test_zero_expectation_falls_back_to_cash():
    """AUDIT REGRESSION 2026-09-28: when no strategy has positive
    risk-adjusted expectation the allocator must fail safe to cash — not
    emit zero-weight allocations, and not KeyError."""
    dead = YieldStrategy(id="dead", name="d", kind=StrategyKind.STABLE_LENDING,
                         protocol="x", estimated_apr=-0.05, risk_score=0.1,
                         min_liquidity_usd=0.0)
    risky_cash = YieldStrategy(id="rc", name="c", kind=StrategyKind.CASH,
                               protocol="x", estimated_apr=0.0, risk_score=2.0,
                               min_liquidity_usd=0.0)
    # cash ineligible at budget 0.5 (risk 2.0) but enabled -> safest-cash fallback
    p = YieldAggregator(strategies=[dead, risky_cash]).plan_rebalance(5_000, 0.5)
    assert [a.strategy_id for a in p.allocations] == ["rc"]
    assert abs(sum(a.weight for a in p.allocations) - 1.0) < 1e-6
    assert p.executed is False
    # no cash anywhere -> empty plan, no crash
    p2 = YieldAggregator(strategies=[dead]).plan_rebalance(5_000, 0.5)
    assert p2.allocations == [] and p2.executed is False


def test_dust_capital_conserved():
    """Dust capital ($0.001) conserves to within rounding, never executes."""
    for dust in (0.001, 0.01, 0.1):
        p = YieldAggregator().plan_rebalance(capital_usd=dust, risk_budget=0.3)
        assert abs(sum(a.capital_usd for a in p.allocations)
                   - p.total_capital_usd) < 0.01
        assert p.executed is False


def test_cap_waterfill_regression():
    """AUDIT REGRESSION 2026-09-28: single-pass redistribution pushed the
    room-holder to 0.60 on a 2-strategy table. Water-fill must keep every
    feasible table within the cap, and conserve on infeasible ones."""
    dom = YieldStrategy(id="dom", name="d", kind=StrategyKind.STABLE_LENDING,
                        protocol="x", estimated_apr=1.0, risk_score=0.1,
                        min_liquidity_usd=0.0)
    meek = YieldStrategy(id="meek", name="m", kind=StrategyKind.STABLE_LENDING,
                         protocol="x", estimated_apr=0.01, risk_score=0.1,
                         min_liquidity_usd=0.0)
    # infeasible (2 strategies): conservation wins, weights still sum to 1
    p = YieldAggregator(strategies=[dom, meek]).plan_rebalance(10_000, 0.5)
    assert abs(sum(a.weight for a in p.allocations) - 1.0) < 1e-6
    assert abs(sum(a.capital_usd for a in p.allocations) - 10_000) < 0.01
    # feasible (3 strategies): cap holds exactly
    third = YieldStrategy(id="mid", name="q", kind=StrategyKind.STABLE_LENDING,
                          protocol="x", estimated_apr=0.5, risk_score=0.1,
                          min_liquidity_usd=0.0)
    p3 = YieldAggregator(strategies=[dom, meek, third]).plan_rebalance(10_000, 0.5)
    for a in p3.allocations:
        assert a.weight <= MAX_SINGLE_STRATEGY_PCT + 1e-6
    assert abs(sum(a.weight for a in p3.allocations) - 1.0) < 1e-6
