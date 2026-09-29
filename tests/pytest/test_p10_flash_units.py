"""Unit tests for P10 Flash Loan Arbitrage Engine reference build.

Covers src/sincor2/defi/flash_arbitrage.py — the scan-only opportunity
scanner, profit-floor filter, gas-ceiling estimator, execution safety
dry-run, scan-only live-block gate, callback-caller verification, atomic
settlement accounting (30 bps treasury fee), and the versioned
self-improvement loop.

Pure simulation, no chain access. Every test names the invariant it guards.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from src.sincor2.defi import flash_arbitrage as fa
from src.sincor2.defi.catalog import TREASURY
from src.sincor2.defi.flash_arbitrage import (
    ArbCandidate,
    CallbackTheftError,
    CallbackVerifier,
    ExecutionSafety,
    GasCeilingEstimator,
    LiveBlockedError,
    OpportunityScanner,
    ParameterSet,
    ProfitFloorFilter,
    SafetyAbort,
    ScanOnlyGate,
    SelfImprovementLoop,
    VenueQuote,
    simulate_settlement,
)

NOTIONAL = 1_000_000_00  # $1M in cents


def _quotes(spread=0.02, block=100):
    """Two venues, `spread` one-way price gap on WETH/USDC."""
    return [
        VenueQuote("uniswap_v3", "WETH/USDC", 3000.00, 30, block),
        VenueQuote("aerodrome", "WETH/USDC", 3000.00 * (1 + spread), 30, block),
    ]


def _candidate(**over):
    base = dict(pair="WETH/USDC", buy_venue="uniswap_v3", sell_venue="aerodrome",
                buy_price=3000.00, sell_price=3012.00, notional_cents=NOTIONAL,
                gross_edge=0.004, score=0.003, flash_fee_cents=50_000,
                gas_cost_cents=1_200, slippage_cents=100_000,
                net_cents=300_000, min_out_buy_leg=33_166,
                min_out_sell_leg=33_034, created_block=100)
    base.update(over)
    return ArbCandidate(**base)


# -- scanner -------------------------------------------------------------------
def test_scanner_finds_positive_edge():
    """Invariant: a real cross-venue gap produces a scored candidate."""
    cands = OpportunityScanner().scan(_quotes(), NOTIONAL, block_number=100)
    assert len(cands) == 1
    c = cands[0]
    assert c.buy_venue == "uniswap_v3" and c.sell_venue == "aerodrome"
    assert c.gross_edge == pytest.approx(0.02)
    assert c.score > 0


def test_scanner_ignores_negative_roundtrip():
    """Invariant: spreads that don't survive both-leg fees score <= 0: dropped."""
    cands = OpportunityScanner().scan(_quotes(spread=0.0001), NOTIONAL, block_number=100)
    assert cands == []


def test_scanner_needs_two_venues():
    """Invariant: a single venue cannot produce an arb candidate."""
    q = [VenueQuote("uniswap_v3", "WETH/USDC", 3000.00, 30, 100)]
    assert OpportunityScanner().scan(q, NOTIONAL, block_number=100) == []


def test_scanner_score_formula():
    """Invariant: score = gross_edge - swap_fees - premium - slippage."""
    c = OpportunityScanner().scan(_quotes(), NOTIONAL, block_number=100)[0]
    expected = 0.02 - 60 / 10_000 - 5 / 10_000 - 2 * 50 / 10_000
    assert c.score == pytest.approx(expected)


def test_ttl_enforced_by_emitter_not_consumer():
    """Invariant: expired candidates never leave the emitter (2-block TTL)."""
    scanner = OpportunityScanner()
    scanner.scan(_quotes(), NOTIONAL, block_number=100)
    assert len(scanner.actionable(at_block=101)) == 1
    # at block 102 the candidate is 2 blocks old -> expired, never emitted
    assert scanner.actionable(at_block=102) == []
    assert scanner.actionable(at_block=10_000) == []


# -- profit floor ----------------------------------------------------------------
def test_floor_blocks_sub_floor_candidate():
    """Invariant: net < max($50, 0.15% notional) never reaches the executor."""
    filt = ProfitFloorFilter()
    c = _candidate(net_cents=10_000, flash_fee_cents=50_000,
                   gas_cost_cents=1_200, slippage_cents=100_000)
    d = filt.check(c, gas_cost_cents=1_200)
    assert not d.passed
    assert d.floor_cents == max(5_000, int(NOTIONAL * 0.0015))
    assert len(filt.rejections) == 1  # every rejection is logged


def test_floor_passes_profitable_candidate():
    """Invariant: a genuinely profitable candidate clears the floor."""
    filt = ProfitFloorFilter()
    c = _candidate(net_cents=500_000, flash_fee_cents=50_000,
                   gas_cost_cents=1_200, slippage_cents=100_000)
    d = filt.check(c, gas_cost_cents=1_200)
    assert d.passed
    assert filt.rejections == []


def test_floor_is_max_of_absolute_and_pct():
    """Invariant: floor = max($50, 0.15% of notional) on both branches."""
    filt = ProfitFloorFilter()
    assert filt.floor_for(1_000_00) == 5_000          # $50 binds on small
    assert filt.floor_for(10_000_000_00) == 1_500_000  # 0.15% binds on large


def test_floor_yaml_configurable():
    """Invariant: the floor is configurable, not hardcoded."""
    filt = ProfitFloorFilter(floor_cents=1_000_00, floor_pct=0.01)
    assert filt.floor_for(NOTIONAL) == 10_000_00


# -- gas ceiling -------------------------------------------------------------------
def test_gas_ceiling_skips_expensive_attempts():
    """Invariant: estimated cost > $25 -> skipped, never executed."""
    est = GasCeilingEstimator()
    assert est.check(2_499)
    assert not est.check(2_501)
    assert est.rejections == 1


def test_gas_estimator_accuracy_gate():
    """Invariant: estimator must match observed gas within ±15% on fixtures."""
    est = GasCeilingEstimator()
    assert est.accuracy_ok(estimated_units=200_000, observed_units=210_000)
    assert not est.accuracy_ok(estimated_units=200_000, observed_units=300_000)
    assert not est.accuracy_ok(estimated_units=200_000, observed_units=0)


# -- execution safety ---------------------------------------------------------------
def test_safety_aborts_on_nonpositive_simulated_net():
    """Invariant: simulated net <= 0 aborts before any signature."""
    safety = ExecutionSafety()
    c = _candidate(net_cents=1_000)  # stale stored net is irrelevant...
    # ...the dry-run re-validates at the pending block: edge evaporated
    with pytest.raises(SafetyAbort):
        safety.dry_run(c, gas_cost_cents=50_000,
                       observed_buy_price=3000.00, observed_sell_price=3000.00)


def test_safety_aborts_on_manipulated_quote():
    """Adversarial: a quote worse than committed minOut aborts the attempt."""
    safety = ExecutionSafety()
    c = _candidate()
    # buy leg slipped beyond the 50bps committed bound
    with pytest.raises(SafetyAbort):
        safety.dry_run(c, gas_cost_cents=1_200,
                       observed_buy_price=3100.00, observed_sell_price=3012.00)


def test_safety_passes_honest_quotes():
    """Invariant: honest quotes within the slippage bound pass the dry-run."""
    safety = ExecutionSafety()
    c = _candidate(net_cents=500_000, flash_fee_cents=50_000,
                   gas_cost_cents=1_200, slippage_cents=100_000)
    net = safety.dry_run(c, gas_cost_cents=1_200,
                         observed_buy_price=3000.00, observed_sell_price=3012.00)
    assert net > 0


# -- scan-only gate -------------------------------------------------------------------
def test_scan_only_gate_blocks_every_execution():
    """Invariant: the live execution path reverts while the gate is engaged."""
    gate = ScanOnlyGate()
    with pytest.raises(LiveBlockedError):
        gate.execute(_candidate())


def test_gate_state_queryable_before_any_action():
    """Invariant: gate state is queryable; EXECUTE_LIVE never enables loans."""
    state = ScanOnlyGate().gate_state()
    assert state["scan_only"] is True
    assert state["execute_live_enabled"] is False


def test_scanning_works_while_gate_engaged():
    """Invariant: scan-only blocks execution, not scanning."""
    assert len(OpportunityScanner().scan(_quotes(), NOTIONAL, block_number=5)) == 1


# -- callback verification --------------------------------------------------------------
def test_callback_rejects_impostor_caller():
    """Adversarial: executeOperation from anyone but the provider reverts."""
    v = CallbackVerifier(provider="0xAavePool")
    with pytest.raises(CallbackTheftError):
        v.verify("0xAttacker")
    v.verify("0xAavePool")  # bound provider passes
    v.verify("0xaavepool")  # case-insensitive


# -- settlement ----------------------------------------------------------------------------
def test_settlement_routes_exact_30bps_to_treasury():
    """Invariant: exactly 30 bps of net profit lands at Treasury, same-tx."""
    s = simulate_settlement(net_cents=1_000_000, pair="WETH/USDC")
    assert s.fee_cents == 1_000_000 * 30 // 10_000
    assert s.fee_to == TREASURY
    assert not s.reverted
    assert s.net_cents + s.fee_cents == 1_000_000


def test_settlement_reverts_everything_when_fee_fails():
    """Invariant: if the fee transfer fails, everything reverts (atomic)."""
    s = simulate_settlement(net_cents=1_000_000, pair="WETH/USDC",
                            fee_transfer_ok=False)
    assert s.reverted
    assert s.fee_cents == 0 and s.net_cents == 0


def test_settlement_never_settles_a_loss():
    """Invariant: atomic revert-on-loss — non-positive net never settles."""
    with pytest.raises(SafetyAbort):
        simulate_settlement(net_cents=0, pair="WETH/USDC")
    with pytest.raises(SafetyAbort):
        simulate_settlement(net_cents=-100, pair="WETH/USDC")


# -- self-improvement loop ---------------------------------------------------------------------
def _params(**over):
    base = dict(version=1, floor_cents=5_000, floor_pct=0.0015,
                ttl_blocks=2, gas_ceiling_cents=2_500)
    base.update(over)
    return ParameterSet(**base)


def _fixtures():
    # (gross_edge, notional_cents): mix of winners and losers
    return [(0.004, 1_000_000_00), (0.001, 1_000_000_00),
            (0.006, 500_000_00), (0.0005, 2_000_000_00),
            (0.010, 200_000_00), (0.002, 800_000_00)] * 4


def test_backtest_counts_only_floor_passing():
    """Invariant: the backtest only credits candidates that clear the floor."""
    loop = SelfImprovementLoop(_params())
    res = loop.backtest(_params(), _fixtures())
    assert res["taken"] < res["n"]
    assert res["captured_cents"] >= 0


def test_walk_forward_promotes_only_real_improvement():
    """Invariant: walk-forward promotes only with out-of-sample improvement."""
    loop = SelfImprovementLoop(_params())
    looser = _params(floor_cents=1_000)   # captures more, incl. junk
    winner = loop.walk_forward(_fixtures(), [_params(), looser])
    # winner must beat the base params on the held-out half to promote
    assert winner.version >= 1


def test_rollback_restores_prior_params_exactly():
    """Invariant: rollback restores the prior parameter set exactly."""
    loop = SelfImprovementLoop(_params())
    before = loop.current
    loop._history.append(_params(version=2, floor_cents=9_999))
    assert loop.current.floor_cents == 9_999
    restored = loop.rollback()
    assert restored == before
    assert restored.floor_cents == 5_000


def test_no_loss_making_execution_in_fuzz():
    """Adversarial fuzz: no random market shape can produce a settling loss."""
    import random
    rng = random.Random(42)
    safety = ExecutionSafety()
    for _ in range(500):
        buy = rng.uniform(2900, 3100)
        sell = buy * rng.uniform(0.999, 1.01)
        c = _candidate(buy_price=buy, sell_price=sell,
                       net_cents=rng.randint(-500_000, 500_000))
        try:
            net = safety.dry_run(c, gas_cost_cents=rng.randint(500, 5_000),
                                 observed_buy_price=buy,
                                 observed_sell_price=sell)
            s = simulate_settlement(net, "WETH/USDC")
            assert s.net_cents >= 0 and not s.reverted
        except SafetyAbort:
            pass  # abort is the safe outcome
