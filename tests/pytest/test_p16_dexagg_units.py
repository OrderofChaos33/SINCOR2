"""Unit tests for the P16 Best-Execution DEX Aggregator reference build.

Covers src/sincor2/defi/dex_aggregator.py — mock venue quotes, the
gas-penalized split optimizer, TOA forecast reweight, min_out
enforcement, 6 bps fee, non-bricking settlement, and the timelocked
venue allowlist behind SKU SINCOR-DEFI-P16-DEXAGG. Pure logic, no chain.

Each test cites the rule it guards (spec p16 acceptance criteria).
24/24 must pass.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from src.sincor2.defi.dex_aggregator import (
    DEFAULT_SLIPPAGE_BPS,
    DUST_USD,
    FEE_BPS,
    MAX_SPLITS,
    TIMELOCK_SECONDS,
    TREASURY,
    VENUE_AERODROME,
    VENUE_UNIV3,
    VENUE_UNIV4,
    AggregatorError,
    LiveExecutionBlocked,
    MinOutViolated,
    MockVenueAdapter,
    QuoteRequest,
    SplitOptimizer,
    VenueNotAllowlisted,
    VenueRegistry,
    apply_forecast,
    check_min_out,
    simulate_execution,
    wrap_unwrap_path,
)


def make_venues():
    # 1e18-scale reserves; distinct fee tiers reproduce V3/V4/Aero shapes.
    return [
        MockVenueAdapter(VENUE_UNIV3, 10_000 * 10**18, 10_000 * 10**18, 30, 120_000),
        MockVenueAdapter(VENUE_UNIV4, 8_000 * 10**18, 8_000 * 10**18, 5, 150_000),
        MockVenueAdapter(VENUE_AERODROME, 12_000 * 10**18, 12_000 * 10**18, 20, 100_000),
    ]


@pytest.fixture()
def registry():
    return VenueRegistry(now=1_000_000.0)


@pytest.fixture()
def venues():
    return make_venues()


@pytest.fixture()
def optimizer(venues, registry):
    return SplitOptimizer(venues, registry)


def req(amount_in_wei=1_000 * 10**18, amount_in_usd=1_000.0,
        gas_per_leg=5 * 10**15, slippage_bps=DEFAULT_SLIPPAGE_BPS):
    return QuoteRequest("USDC", "WETH", amount_in_wei, amount_in_usd,
                        gas_per_leg, slippage_bps)


# -- venue quotes --------------------------------------------------------------
def test_quote_deterministic(venues):
    """AC1 (python leg): same input always quotes the same output."""
    q1 = venues[0].quote(10**18)
    q2 = venues[0].quote(10**18)
    assert q1.amount_out_wei == q2.amount_out_wei > 0


def test_quote_zero_in_zero_out(venues):
    q = venues[0].quote(0)
    assert q.amount_out_wei == 0


def test_quote_negative_raises(venues):
    with pytest.raises(ValueError):
        venues[0].quote(-1)


def test_price_impact_worsens_rate(venues):
    """Constant-product: doubling size more than doubles slippage."""
    small = venues[0].quote(10**18).amount_out_wei
    big = venues[0].quote(100 * 10**18).amount_out_wei
    assert big < 100 * small  # price impact


def test_lower_fee_tier_quotes_better():
    """Same reserves, lower fee tier must quote more out."""
    a = MockVenueAdapter("low", 10**22, 10**22, 5, 100_000)
    b = MockVenueAdapter("high", 10**22, 10**22, 100, 100_000)
    assert a.quote(10**18).amount_out_wei > b.quote(10**18).amount_out_wei


# -- optimizer -------------------------------------------------------------------
def test_allocations_sum_to_10000(optimizer):
    """AC2: legs always sum to exactly 10_000 bps."""
    plan = optimizer.optimize(req())
    assert sum(l.in_bps for l in plan.legs) == 10_000


def test_max_three_splits(optimizer):
    """AC2: never more than 3 venue legs."""
    for usd in (100.0, 1_000.0, 100_000.0, 1_000_000.0):
        plan = optimizer.optimize(req(amount_in_usd=usd,
                                      amount_in_wei=int(usd * 10**18)))
        assert len(plan.legs) <= MAX_SPLITS, usd


def test_dust_legs_never_emitted(optimizer):
    """AC2: every leg notional >= $10 dust threshold."""
    plan = optimizer.optimize(req(amount_in_usd=50.0,
                                  amount_in_wei=50 * 10**18))
    for leg in plan.legs:
        leg_usd = 50.0 * leg.in_bps / 10_000
        assert leg_usd >= DUST_USD - 1e-9 or len(plan.legs) == 1


def test_net_beats_best_single_venue(optimizer, venues, registry):
    """AC2: on synthetic sets, net output >= best single-venue net."""
    import random
    rng = random.Random(16)
    for _ in range(30):
        reserves = [rng.randint(1_000, 50_000) * 10**18 for _ in range(3)]
        vs = [
            MockVenueAdapter(f"v{i}", reserves[i], reserves[i],
                             rng.choice([5, 30, 100]), 120_000)
            for i in range(3)
        ]
        reg = VenueRegistry(now=0.0)
        for v in vs:
            reg.propose_add(v.venue_id)
        reg.set_now(TIMELOCK_SECONDS + 1)
        for v in vs:
            reg.execute_add(v.venue_id)
        opt = SplitOptimizer(vs, reg)
        r = req(amount_in_usd=5_000.0, amount_in_wei=5_000 * 10**18)
        plan = opt.optimize(r)
        best_single = max(v.quote(r.amount_in_wei).amount_out_wei
                          - r.gas_cost_out_wei_per_leg for v in vs)
        assert plan.net_out_wei >= best_single


def test_marginal_gas_blocks_useless_split(optimizer):
    """Tip (net not gross): absurd gas forces a single-venue route."""
    r = req(gas_per_leg=10**22)  # gas dwarfs any split gain
    plan = optimizer.optimize(r)
    assert len(plan.legs) == 1


def test_zero_amount_raises(optimizer):
    with pytest.raises(ValueError):
        optimizer.optimize(req(amount_in_wei=0))


def test_min_out_formula(optimizer):
    """AC4 (python leg): minOut = quoted * (1 - slippage)."""
    plan = optimizer.optimize(req(slippage_bps=50))
    assert plan.min_out_wei == plan.quoted_out_wei * 9_950 // 10_000


def test_fee_exactly_6bps(optimizer):
    """AC5 (python leg): fee is exactly 6 bps of input."""
    plan = optimizer.optimize(req())
    assert plan.fee_bps == FEE_BPS == 6
    assert plan.fee_wei == plan.amount_in_wei * 6 // 10_000
    assert plan.treasury == TREASURY


def test_dry_run_default(optimizer):
    """Risk gate: dry_run unless the live path is explicitly enabled."""
    plan = optimizer.optimize(req())
    assert plan.mode == "dry_run"
    assert plan.executed is False


# -- min_out + settlement ----------------------------------------------------------
def test_check_min_out_passes():
    check_min_out(1_000, 999)  # no raise


def test_check_min_out_reverts():
    """AC4: realized < committed minOut reverts the swap."""
    with pytest.raises(MinOutViolated):
        check_min_out(998, 999)


def test_non_bricking_settlement(optimizer, venues):
    """AC7 (python leg): one reverting venue returns input, route settles."""
    plan = optimizer.optimize(req())
    venue_map = {v.venue_id: v for v in venues}
    failing = (plan.legs[0].venue_id,)
    res = simulate_execution(plan, venue_map, failing_venues=failing)
    assert res["returned_in_wei"] == plan.legs[0].amount_in_wei
    assert res["realized_out_wei"] > 0
    assert any(l["status"] == "reverted" for l in res["legs"])


def test_all_venues_fail_returns_everything(optimizer, venues):
    plan = optimizer.optimize(req())
    venue_map = {v.venue_id: v for v in venues}
    failing = tuple(l.venue_id for l in plan.legs)
    res = simulate_execution(plan, venue_map, failing_venues=failing)
    assert res["returned_in_wei"] == sum(l.amount_in_wei for l in plan.legs)
    assert res["realized_out_wei"] == 0


# -- allowlist -----------------------------------------------------------------------
def test_non_allowlisted_execution_reverts(registry):
    """AC6: routing through a non-allowlisted adapter raises."""
    with pytest.raises(VenueNotAllowlisted):
        registry.require_allowlisted("evil_venue")


def test_optimizer_ignores_non_allowlisted(registry):
    rogue = MockVenueAdapter("rogue", 10**30, 10**30, 0, 1)  # infinite value
    opt = SplitOptimizer(make_venues() + [rogue], registry)
    plan = opt.optimize(req())
    assert all(l.venue_id != "rogue" for l in plan.legs)


def test_add_timelock_not_elapsed(registry):
    registry.propose_add("new_venue")
    with pytest.raises(AggregatorError):
        registry.execute_add("new_venue")


def test_add_timelock_elapsed(registry):
    registry.propose_add("new_venue")
    registry.set_now(registry._now + TIMELOCK_SECONDS + 1)
    registry.execute_add("new_venue")
    assert registry.is_allowlisted("new_venue")


def test_remove_takes_effect_only_after_timelock(registry):
    """AC6: removal reverts pre-timelock, allowlist drops post-timelock."""
    registry.propose_remove(VENUE_AERODROME)
    assert registry.is_allowlisted(VENUE_AERODROME)  # still active
    with pytest.raises(AggregatorError):
        registry.execute_remove(VENUE_AERODROME)
    registry.set_now(registry._now + TIMELOCK_SECONDS + 1)
    registry.execute_remove(VENUE_AERODROME)
    assert not registry.is_allowlisted(VENUE_AERODROME)
    with pytest.raises(VenueNotAllowlisted):
        registry.require_allowlisted(VENUE_AERODROME)


# -- TOA forecast ----------------------------------------------------------------------
def test_toa_degraded_forecast_shifts_weights(optimizer, venues):
    """AC3: degraded-venue forecast JSON measurably shifts route weights."""
    plan = optimizer.optimize(req(amount_in_usd=50_000.0,
                                  amount_in_wei=50_000 * 10**18))
    before = {l.venue_id: l.in_bps for l in plan.legs}
    assert len(before) >= 2  # need a real split to observe a shift
    top_venue = max(before, key=before.get)
    forecast = json.loads(json.dumps({top_venue: 0.05}))  # 5% degradation
    venue_map = {v.venue_id: v for v in venues}
    r = req(amount_in_usd=50_000.0, amount_in_wei=50_000 * 10**18)
    shifted = apply_forecast(plan, forecast, venue_map, r)
    after = {l.venue_id: l.in_bps for l in shifted.legs}
    assert after[top_venue] < before[top_venue]
    assert sum(after.values()) == 10_000


def test_toa_empty_forecast_noop(optimizer, venues):
    plan = optimizer.optimize(req())
    before = [(l.venue_id, l.in_bps) for l in plan.legs]
    venue_map = {v.venue_id: v for v in venues}
    out = apply_forecast(plan, {}, venue_map, req())
    assert [(l.venue_id, l.in_bps) for l in out.legs] == before


# -- misc ------------------------------------------------------------------------------
def test_weth_path_wrapping():
    """WETH path: ETH wraps at entry, unwraps at exit, atomically."""
    p = wrap_unwrap_path("ETH", "USDC")
    assert p == {"in": "WETH", "out": "USDC", "wrap": "wrap_in"}
    p = wrap_unwrap_path("USDC", "ETH")
    assert p["out"] == "WETH" and p["wrap"] == "unwrap_out"
    p = wrap_unwrap_path("USDC", "WETH")
    assert p["wrap"] == "none"
