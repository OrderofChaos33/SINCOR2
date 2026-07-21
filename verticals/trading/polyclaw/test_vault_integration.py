#!/usr/bin/env python3
"""
Invariant + integration tests for the Polyclaw <-> SharedLiquidityVault bridge.

Invariant under test (mirrors on-chain):
    every proposed drawdown <= availableDraw(strategyId, lp, token)
    => drawDown can never revert InsufficientVirtualAlloc /
       InsufficientRealBalance / StrategyCapExceeded because of our sizing.

Run:  python -m pytest verticals/trading/polyclaw/test_vault_integration.py -q
"""

from __future__ import annotations

import random

from verticals.trading.polyclaw.core_agent import PolyclawCoreAgent
from verticals.trading.polyclaw.observer_improver import ObserverImproverAgent
from verticals.trading.polyclaw.vault_client import VaultClient, VaultState

LP = "0xdba7180cdd90D12B9Bc2F15080ddFD9B14fEf31a"
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"  # Base USDC


def make_state(**kw) -> VaultState:
    base = dict(
        strategy_id=0,
        lp=LP,
        token=USDC,
        virtual_alloc=10_000.0,
        outstanding=2_000.0,
        strategy_outstanding=5_000.0,
        cap=20_000.0,
        real_balance=50_000.0,
        lp_outstanding_total=6_000.0,
    )
    base.update(kw)
    return VaultState(**base)


def make_agent() -> PolyclawCoreAgent:
    return PolyclawCoreAgent(name="test-core", vault_client=VaultClient())


# ----------------------------------------------------------------------
# availableDraw mirror
# ----------------------------------------------------------------------
def test_available_draw_mirrors_contract():
    s = make_state()
    # commitLeft = 10000-2000 = 8000; freeReal = 50000-6000 = 44000
    # capLeft = 20000-5000 = 15000 -> min = 8000
    assert s.available_draw() == 8_000.0


def test_available_draw_cap_bound():
    s = make_state(cap=6_000.0)  # capLeft = 1000
    assert s.available_draw() == 1_000.0


def test_available_draw_uncapped():
    s = make_state(cap=0.0)
    assert s.available_draw() == 8_000.0


def test_available_draw_free_real_bound():
    s = make_state(real_balance=7_000.0, lp_outstanding_total=6_500.0)
    assert s.available_draw() == 500.0


# ----------------------------------------------------------------------
# Drawdown sizing invariant (fuzzed)
# ----------------------------------------------------------------------
def test_drawdown_never_exceeds_available_draw_fuzz():
    rng = random.Random(8453)
    agent = make_agent()
    market = {"id": "m1", "probability": 0.50, "liquidity": 250_000}

    for _ in range(500):
        state = make_state(
            virtual_alloc=rng.uniform(0, 100_000),
            outstanding=rng.uniform(0, 50_000),
            strategy_outstanding=rng.uniform(0, 50_000),
            cap=rng.choice([0.0, rng.uniform(1, 100_000)]),
            real_balance=rng.uniform(0, 200_000),
            lp_outstanding_total=rng.uniform(0, 100_000),
        )
        # keep the lane self-consistent like on-chain state
        state.outstanding = min(state.outstanding, state.virtual_alloc)
        state.lp_outstanding_total = min(
            state.lp_outstanding_total, state.real_balance
        )

        opp = agent.evaluate_yield_opportunity(
            market=market,
            model_probability=0.62,  # 12% edge, clears the bar
            vault_state=state,
            bankroll=250_000,
        )
        if opp is not None:
            assert 0 < opp.drawdown_amount <= state.available_draw() + 1e-6
            assert opp.expected_value > 0
            assert opp.drawdown_calldata.startswith("0x0ca63fb1")


def test_no_proposal_below_edge_threshold():
    agent = make_agent()
    market = {"id": "m1", "probability": 0.50}
    opp = agent.evaluate_yield_opportunity(
        market=market,
        model_probability=0.51,  # 1% edge < 2.5% bar
        vault_state=make_state(),
        bankroll=100_000,
    )
    assert opp is None


def test_no_proposal_when_lane_exhausted():
    agent = make_agent()
    state = make_state(outstanding=10_000.0)  # commitLeft = 0
    opp = agent.evaluate_yield_opportunity(
        market={"id": "m1", "probability": 0.50},
        model_probability=0.62,
        vault_state=state,
        bankroll=100_000,
    )
    assert opp is None


def test_lending_loop_only_when_spread_clears_bar():
    agent = make_agent()
    state = make_state()

    # No market edge; loop spread below bar -> None
    assert agent.evaluate_yield_opportunity(
        market=None, model_probability=None, vault_state=state,
        bankroll=100_000, lending_loop_apr=0.03, funding_cost_apr=0.02,
    ) is None

    # Spread above bar -> lending loop proposal
    opp = agent.evaluate_yield_opportunity(
        market=None, model_probability=None, vault_state=state,
        bankroll=100_000, lending_loop_apr=0.09, funding_cost_apr=0.02,
    )
    assert opp is not None and opp.kind == "lending_loop"
    assert opp.drawdown_amount <= state.available_draw()


# ----------------------------------------------------------------------
# settleUp callback -> calibration + observer
# ----------------------------------------------------------------------
def test_settle_up_updates_calibration_and_observer():
    agent = make_agent()
    observer = ObserverImproverAgent()
    ema_before = agent.win_rate_ema

    rec = agent.settle_up(
        make_state(), principal=1_000.0, fee=42.0,
        was_correct=True, observer=observer,
    )

    assert agent.win_rate_ema > ema_before            # win pushed EMA up
    assert len(observer.observations) == 1            # observer fed
    assert rec.settle_calldata.startswith("0xd77225c8")
    assert rec.protocol_fee_bps == agent.protocol_fee_bps

    obs = observer.observations[0]
    assert obs["metrics"]["protocol_fee"] == 42.0 * 1000 / 10_000


def test_settle_up_loss_pulls_ema_down():
    agent = make_agent()
    ema_before = agent.win_rate_ema
    agent.settle_up(make_state(), principal=500.0, fee=0.0, was_correct=False)
    assert agent.win_rate_ema < ema_before
