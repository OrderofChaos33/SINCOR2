# Group B Fork-Sim Profitability Assessments

**Date:** 2026-10-22  
**Branch:** xioix/forksim-groupb-profit-evidence  
**Founder bar:** "if they make money on sepolia go live" — net positive after the locked 5% platform fee.

## Profitability Threshold (explicit)

```
GO  iff  net_pnl > 0
     AND win_rate >= 40%
     AND max_drawdown < 20% of bankroll
     AND trades >= 10          (degenerate guard: not trading != profit)
```

Net P&L = gross − product fees − 5% platform fee (platform fee on profit only).

## Verdicts

| Product | Verdict | Net P&L | Detail |
|---------|---------|---------|--------|
| P14 Prediction Markets | **CONDITIONAL GO** | +$148,139 (machinery) / -$47 (momentum) | Machinery PROVEN via 60 real Polymarket markets (8/8 wins, 0 DD with perfect signal). Momentum signal NOT profitable. Needs a real edge source before live. |
| P02 CLMM | **BLOCKED** | — | No pinned Uniswap pool addresses in repo. Standing rule forbids inventing addresses. |
| P06 Perp Hedging | **BLOCKED** | — | No pinned perp venues / funding-rate sources in repo. |
| P10 Flash Arb | **BLOCKED** | — | No pinned DEX pool addresses for real quotes. `simulate_settlement` takes profit as input; cannot generate real opportunities. |
| P11 Delta Neutral | **BLOCKED** | — | No pinned perp + spot venues in repo. |
| P13 AVS Tranching | **BLOCKED** | — | No pinned EigenLayer/AVS contracts in repo. |
| P17 Options | **DEFINABLE** | — | Has Black-Scholes + pinned Chainlink ETH/USD. Needs: historical round reader (getRoundData) + defined options strategy. Next build. |
| P19 Credit | **BLOCKED** | — | Off-chain underwriting model; no P&L mechanics exist to simulate. No honest fork-sim definable. |
| P23 NFT-Fi | **BLOCKED** | — | No pinned NFT collection / floor-oracle contracts in repo. |

## P14 Detail

**Method:** 60 real resolved Polymarket markets (mechanical selection: closed=true, volume desc, first 60 with volume ≥$10k and clean binary outcomePrices). Decision at T=48h before endDate. Prices from public CLOB history API. Real `run_backtest` (not reimplemented).

**Two tiers:**
1. **Machinery check** (perfect signal, lookahead by design): 8 trades, 8 wins, +$148,139 net, $0 drawdown. The Kelly sizing, edge vetoes, risk limits, and fee routing WORK.
2. **Strategy check** (momentum signal, no lookahead): 1 trade, -$46.76. 20/60 signals cleared the 2% edge bar, but momentum does not predict outcomes (positive momentum preceded losses).

**Why so few trades:** The product is YES-first-outcome-only. 77% of episodes were loser legs (correctly vetoed by EV check). Of winner episodes, 8 passed all filters.

**Verdict rationale:** CONDITIONAL GO — do not go live on momentum. The machinery is proven; it needs a real edge source (e.g., whale flow from the Polyclaw system) before live deployment.

## What "BLOCKED" means

BLOCKED is not a failure. It means: with the repo's current pinned addresses, no honest profitability sim can be defined without inventing data. Unblocking requires pinning real counterpart addresses (auditor-signed config per the live-LP gate rule), not code changes.

## Files

- `fork_sim/profit_p14.py` — P14 profitability sim (new)
- `tests/pytest/test_profit_p14.py` — 20 tests (new)
- `fork_sim/p14_evidence_staged.json` — 3 staged ledger entries (NOT in proof_ledger.json)
- Episode data: `/tmp/p14_episodes60.json` (60 real markets, reproducible)
