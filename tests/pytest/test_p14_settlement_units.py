"""Money-path settlement-unit invariant tests for P14 prediction markets.

Bug history (2026-10-22): OrderLifecycleManager.resolve() computed
`shares = pos.size_cents / pos.entry_price` (cents / dollars), missing the
/100 cents->dollars conversion, so every winning payout was inflated ~100x.
Correct math: payout_cents = round(size_cents / entry_price), because each
winning share redeems exactly 100 cents (CTF invariant):

    shares = (size_cents / 100) / entry_price
    payout_cents = shares * 100 = size_cents / entry_price

These tests pin the unit-correct math across many price/size cases,
including fractional-cent rounding, wins and losses, and a full-loop
integration test proving the open -> resolve -> payout -> fee_ledger
handoff end to end (the wired-PR gate: unit-green alone is not enough
for money-path code).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pytest

from src.sincor2.defi.catalog import TREASURY
from src.sincor2.defi.prediction_markets import (
    FEE_BPS,
    MarketSnapshot,
    OrderLifecycleManager,
    PolyclawWalletAdapter,
)
import src.sincor2.defi.prediction_markets as pm

NOW = 1_760_000_000


def _snap(**over):
    base = dict(market_id="m1", question="Will X happen?",
                yes_price=0.55, volume_24h_cents=5_000_000,
                oracle="uma", resolves_at=NOW + 7 * 86400,
                as_of=NOW, category="politics")
    base.update(over)
    return MarketSnapshot(**base)


def _lifecycle():
    return OrderLifecycleManager(PolyclawWalletAdapter(pm.POLYCLAW_KEY_ID))


def _win_payout(size_cents, price):
    """Expected payout under the unit-correct CTF invariant."""
    return int(round(size_cents / price))


# -- the core invariant, parameterized ----------------------------------------

@pytest.mark.parametrize("size_cents,price", [
    (500, 0.37),        # the canonical worked example: 500c @ $0.37
    (10_000, 0.50),
    (1_000, 0.55),
    (5_000, 1.00),      # price 1.00: payout equals size, pnl 0
    (10_000, 0.99),
    (1, 0.01),          # tiny size, tiny price
    (250_000, 0.73),    # large size
    (3_300, 0.41),
])
def test_winning_payout_equals_round_size_over_price(size_cents, price):
    """Invariant: payout_cents == round(size_cents / entry_price)."""
    lc = _lifecycle()
    pos = lc.open_position(_snap(), "YES", size_cents, price, NOW)
    pnl = lc.resolve(pos, outcome_yes=True)
    assert pos.payout_cents == _win_payout(size_cents, price)
    assert pnl == pos.payout_cents - size_cents


def test_canonical_worked_example_500c_at_37c():
    """500c @ $0.37 -> round(500/0.37) = 1351c, pnl = 851c."""
    lc = _lifecycle()
    pos = lc.open_position(_snap(), "YES", 500, 0.37, NOW)
    pnl = lc.resolve(pos, outcome_yes=True)
    assert pos.payout_cents == 1351
    assert pnl == 851
    assert lc.realized_pnl_cents() == 851

def test_one_winning_share_redeems_exactly_100_cents():
    """CTF invariant: 1 winning share == 100 cents (1 USDC)."""
    lc = _lifecycle()
    pos = lc.open_position(_snap(), "YES", 100, 1.00, NOW)
    pnl = lc.resolve(pos, outcome_yes=True)
    assert pos.payout_cents == 100
    assert pnl == 0  # bought at 1.00: no profit, but full redemption
    # 5 shares @ $0.50 -> 500c
    pos2 = lc.open_position(_snap(market_id="m2"), "YES", 250, 0.50, NOW)
    lc.resolve(pos2, outcome_yes=True)
    assert pos2.payout_cents == 500


def test_fractional_cent_rounding_rounds_up():
    """1000c @ $0.37 -> 2702.70... -> 2703c (rounds up to nearest cent)."""
    lc = _lifecycle()
    pos = lc.open_position(_snap(), "YES", 1_000, 0.37, NOW)
    pnl = lc.resolve(pos, outcome_yes=True)
    assert pos.payout_cents == 2703
    assert pnl == 1703


def test_fractional_cent_rounding_rounds_down():
    """1000c @ $0.333 -> 3003.003... -> 3003c (rounds down)."""
    lc = _lifecycle()
    pos = lc.open_position(_snap(), "YES", 1_000, 0.333, NOW)
    pnl = lc.resolve(pos, outcome_yes=True)
    assert pos.payout_cents == 3003
    assert pnl == 2003


# -- losses are untouched --------------------------------------------------------

@pytest.mark.parametrize("size_cents,price", [
    (10_000, 0.50),
    (500, 0.37),
    (1_000, 0.90),
])
def test_losing_position_pays_zero_pnl_is_negative_size(size_cents, price):
    """Invariant: losing position -> payout 0, pnl == -size_cents."""
    lc = _lifecycle()
    pos = lc.open_position(_snap(), "YES", size_cents, price, NOW)
    pnl = lc.resolve(pos, outcome_yes=False)
    assert pos.payout_cents == 0
    assert pnl == -size_cents
    assert lc.fee_ledger == []  # no fee on losses


def test_no_side_gets_inflated_payout():
    """NO-side wins use the same math: payout == round(size/price)."""
    lc = _lifecycle()
    pos = lc.open_position(_snap(), "NO", 5_000, 0.40, NOW)
    pnl = lc.resolve(pos, outcome_yes=False)  # NO wins when outcome is No
    assert pos.payout_cents == int(round(5_000 / 0.40))
    assert pnl == pos.payout_cents - 5_000


# -- full-loop integration: open -> resolve -> payout -> fee_ledger --------------

def test_full_loop_settlement_handoff_wired():
    """Wired-PR gate: the full money path handoff end to end.

    open_position -> resolve -> payout_cents -> fee_ledger -> reconciles.
    Proves the settlement math feeds the fee ledger correctly and the
    ledger total is exactly 15 bps of settled positive PnL.
    """
    lc = _lifecycle()
    # winning position: 10_000c @ $0.50 -> payout 20_000c, pnl +10_000c
    win = lc.open_position(_snap(), "YES", 10_000, 0.50, NOW)
    pnl_won = lc.resolve(win, outcome_yes=True)
    assert pnl_won == 10_000
    assert win.payout_cents == 20_000

    # fee handoff: exactly 15 bps of the positive settled pnl
    fee_won = pnl_won * FEE_BPS // 10_000
    assert fee_won == 15
    assert lc.fee_total_cents() == fee_won
    assert len(lc.fee_ledger) == 1
    entry = lc.fee_ledger[0]
    assert entry.market_id == "m1"
    assert entry.pnl_cents == pnl_won
    assert entry.fee_cents == fee_won
    assert entry.fee_to == TREASURY
    assert lc.fee_ledger_reconciles()

    # losing position on another market: no fee entry, ledger untouched
    loss = lc.open_position(_snap(market_id="m2"), "YES", 10_000, 0.50, NOW)
    pnl_lost = lc.resolve(loss, outcome_yes=False)
    assert pnl_lost == -10_000
    assert loss.payout_cents == 0
    assert len(lc.fee_ledger) == 1
    assert lc.fee_ledger_reconciles()

    # portfolio-level reconciliation stays consistent
    assert lc.realized_pnl_cents() == 10_000 - 10_000 == 0
