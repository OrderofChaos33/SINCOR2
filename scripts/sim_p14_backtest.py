"""Dry-run backtest runner for the P14 prediction-market reference build.

Builds a scripted market corpus, runs the deterministic dry-run backtest
(`run_backtest` from src/sincor2/defi/prediction_markets.py), and prints the
report: markets traded, realized PnL, max drawdown, Brier score, fee totals
per market. Pure simulation — no chain, no keys.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.sincor2.defi.prediction_markets import (
    ForecastEngine,
    KellySizer,
    MarketSnapshot,
    run_backtest,
)

# Must match run_backtest()'s internal clock (deterministic dry-run).
NOW = 1_700_000_000
BANKROLL_CENTS = 100_000_00  # $100k


def build_corpus(n: int = 50, seed: int = 2026):
    rng = random.Random(seed)
    markets = []
    for i in range(n):
        m = rng.uniform(0.35, 0.65)
        signal = 0.06 if rng.random() < 0.7 else -0.02
        p = min(max(m + signal, 0.01), 0.99)
        outcome = 1 if rng.random() < p else 0
        snap = MarketSnapshot(
            market_id=f"sim-{i:03d}",
            question=f"Simulated market {i}",
            yes_price=m,
            volume_24h_cents=5_000_000,
            oracle="uma",
            resolves_at=NOW + 7 * 86400,
            as_of=NOW,
            category="sports" if i % 2 else "politics",
        )
        markets.append((snap, signal, outcome))
    return markets


def main() -> int:
    res = run_backtest(build_corpus(), ForecastEngine(), KellySizer(),
                       BANKROLL_CENTS)
    print("P14 dry-run backtest report")
    print(f"  markets_traded      : {res['markets_traded']}")
    print(f"  realized_pnl_cents  : {res['realized_pnl_cents']}")
    print(f"  max_drawdown_cents  : {res['max_drawdown_cents']}")
    print(f"  brier_score         : {res['brier']:.4f}")
    print(f"  naive_brier_score   : {res['naive_brier']:.4f}")
    print(f"  fee_total_cents     : {res['fee_total_cents']}")
    print(f"  fee_ledger_reconciles: {res['fee_ledger_reconciles']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
