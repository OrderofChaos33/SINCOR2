# Whale-Edge Assessment — P14 Fork-Sim (Green Team Mission 1A)

**Date:** 2026-10-22
**Branch:** xioix/green-mission1a-p14-whale-edge
**Hypothesis:** entering the same side as a qualified whale's pre-decision
flow produces positive expectancy on resolved Polymarket markets.

## GO bar (identical to the momentum run)

```
GO  iff  net_pnl > 0
     AND win_rate >= 40%
     AND max_drawdown < 20% of bankroll
     AND trades >= 10          (degenerate guard: not trading != profit)
```

Net P&L = gross − product fees − 5% platform fee (platform fee on profit only).

## Parameters committed BEFORE the first sim run

| Parameter | Value |
|---|---|
| N episodes | 60, mechanical selection via `fetch_episodes(60, 10_000)` (same as momentum) |
| Volume floor | $10,000 |
| Decision time T | endDate − 48h (same as momentum) |
| Signal cap | 0.12 probability points (same as momentum) |
| Whale seeds | `discover_candidates_from_leaderboard(limit=50)` |
| Whale bar | ≥20 resolved trades AND ≥+$100 realized P&L (resolved markets only) — reused unchanged via `discover_whales` |
| Trade pull limit | 1000 trades/whale for pre-T flow matching |
| Direction rule | BUY yes-token or SELL no-token → bullish YES (+1); SELL yes-token or BUY no-token → bearish (−1); anything else → 0 |
| Weighting | price × size notional per trade, summed across trades and whales |
| Signal rule | sign(net_notional) × 0.12; 0.0 when net == 0 (direction-only, full cap) |
| Pre-decision filter | trade timestamp < T, STRICT (post-T trades excluded — no lookahead) |
| Trade matching | trade `asset` must equal the episode market's clobTokenIds yes/no token ID |

No parameter was tuned to results. Whatever the sim says is the verdict.

## Method

1. **Episodes:** 60 real resolved Polymarket markets, mechanical selection
   (closed=true, volume desc, first 60 with volume ≥$10k and clean binary
   outcomePrices). Prices from the public CLOB history API. Same pipeline
   as the momentum run, via the real `profit_p14.fetch_episodes`.
2. **Whale discovery:** re-ran from scratch — the VM restart wiped `/data`,
   so the whale registry and resolution cache were rebuilt with persistent
   workspace paths (`/home/hatch/workspace/green-mission1a/whale_registry.json`
   and `whale_cache.db`, set via `WHALE_REGISTRY_PATH` / `WHALE_CACHE_PATH`
   before import). Seeds: top-50 all-time-PnL leaderboard wallets; each
   scored on resolved markets only through the existing bar.
3. **Signal construction:** for each episode and each qualified whale,
   pull ≤1000 trades; keep only trades on the episode's market (token-ID
   match) with timestamp < T; sum direction × price × size; map to
   ±0.12 / 0.0. The signal function is precomputed per market and fed to
   the REAL `prediction_markets.run_backtest` (ingest → forecast → edge
   vetoes → Kelly sizing → fill → resolve → risk limits), same as momentum.
4. **Fees:** product fee (15bps of positive settled PnL, per the module)
   then the locked 5% platform fee on net profit.

## Known limitations (disclosed, not hidden)

- **Selection bias (mild):** whale qualification uses all-time resolved
  P&L, which includes post-T resolutions. The qualification bar selects
  historically profitable wallets; only the *directional signal* is
  lookahead-free (every counted trade is strictly pre-T). This mirrors how
  the product would use the whale registry in production (a persistent,
  evolving registry), so the bias matches the real deployment shape.
- **Coverage:** many episodes may carry zero whale flow (no qualified whale
  traded that market pre-T). Coverage is reported as nonzero/60; the
  product's edge vetoes will filter zero-signal markets.
- **Direction-only signal:** magnitude of whale conviction is collapsed to
  the sign at full cap, same cap as momentum, for comparability.
- **Multi-outcome event legs:** episodes include loser legs of multi-outcome
  events (only one outcome wins per event); the product's YES-first-outcome
  EV check vetoes these, same as the momentum run.

## Results

Live run 2026-10-22/23 (real APIs, real `run_backtest`; evidence, not a test):

| Bar | Value | Pass? |
|---|---|---|
| Net P&L | **+$10,564.72** (gross +$11,137.45 − product fee $16.70 − 5% platform $556.03) | ✅ > 0 |
| Win rate | 100% (1/1) | ✅ ≥ 40% |
| Max drawdown | $0.00 | ✅ < 20% of $1,000 bankroll |
| Trades | **1** | ❌ < 10 |

**Verdict: NO-GO** — "only 1 trades (< 10; not trading != profit)."

The single whale-signal trade was a WIN: market 558934 ("Will Spain win
the 2026 FIFA World Cup?", resolved YES), where qualifier `0xf0318c32…`
($1.22M realized P&L) bought $33,618.98 of YES tokens across 4 pre-T
orders (Jul 2–14, decision T = Jul 18 — no lookahead). Signal +0.12,
product bought YES, Spain won. Machinery health: brier 0.083 vs naive
0.167, fee ledger reconciles.

**Coverage: 1/60 episodes carried nonzero whale signal.** The other 59
had no qualified-whale pre-T flow inside the committed 1000-trade pull
window and were vetoed by the product's edge filters (correct behavior —
no signal, no trade).

**Discovery (rebuilt after the `/data` wipe):** 50 leaderboard seeds
scored; **5 qualified** (≥20 resolved trades, ≥+$100 realized P&L):
`0xf0318c32…` ($1,219,719, 41% WR, 371 trades), `0x1f3a646c…` ($351,115,
47%, 1917), `0xb52e8b3a…` ($169,960, 52%, 412), `0x52685279…` ($1,278,
55%, 1293), `0xce937f66…` ($61,128, 54%, 51). 36 seeds failed the bar
(mostly no resolvable trade history). **9 seeds were unscorable due to
API flakiness, not merit:** gamma-api intermittently tarpits connections
mid-response on specific event-family lookups (diagnosed via stack dump:
hang in `ssl.read` during chunked response read in `_market_resolution`;
stdlib socket timeout does not always fire), and the data-api trade pull
intermittently times out. Skipped wallets are listed in
`discovery_scored.json`; a retry pass confirmed the flakiness is
persistent for those lookups. No whale data was fabricated.

## Verdict

**NO-GO.** The hypothesis is not disproven — the one whale-signal trade
won decisively — but it is untested at scale: 1/60 coverage cannot clear
the ≥10-trade bar. The bottleneck is coverage (whale flow overlap with
the episode set inside the committed pull window), not the machinery
(which is proven) and not signal direction (1/1 correct). Per the
mission rules, parameters were committed before the run and were not
tuned; widening the pull window or the seed set would be a follow-up
experiment, not a revision of this result.

## What would make this a GO

A follow-up run with wider whale coverage (more qualifiers and/or a
deeper per-whale trade window reaching further back) that produces ≥10
trades. The wiring, tests, caches, and registry are all in place for
that run; only the committed parameters would change, explicitly and
before the run.

## Files

- `fork_sim/whale_edge_p14.py` — the wired edge (new module)
- `tests/pytest/test_whale_edge_p14.py` — wiring tests, synthetic only
- `fork_sim/whale_edge_results.json` — full stats from the live run
- `/home/hatch/workspace/green-mission1a/` — persistent whale registry,
  resolution cache, token-ID cache, trade cache, run log
