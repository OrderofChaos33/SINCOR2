# P23 — Intent Solver & Dark Pool (standalone package)

SINCOR DeFi P23 (suite name P03), SKU `SINCOR-DEFI-P03-DARKPOOL`.

Intent-solving and dark-pool execution module. Exists as a platform
module; **no solver is deployed or bonded for outside flow.**
State: **EXTERNAL** — a buyer/counterparty is required, and deployment
happens only inside a signed engagement.

## Contents

- `src/sincor_p23_intent_solver/intent_dark_pool.py` — extracted
  module: `DarkPool` (commitment-only intent lifecycle, nonces, replay
  protection), `Matcher` (off-chain matching, pro-rata partial fills,
  idempotent batches), `SettlementEngine` (net settlement, AXM/USDC
  only, 8 bps in-simulation fee, pull claims, non-bricking transfers),
  `SplitSolver` (split routing with per-leg `min_out`).
- `src/sincor_p23_intent_solver/engagement_fees.py` — catalog
  engagement fee math (secondary service form).
- `src/sincor_p23_intent_solver/price_oracle_stub.py` — vendored
  minimal price-oracle wiring surface (see PACKAGING.md).
- `tests/` — ported platform tests plus added coverage.
- `ENGAGEMENT.md` — scope template and priced terms.
- `SOLVER_BONDING.md` — solver-bonding requirements spec.
- `DEPLOY.md` / `deploy/` — deploy instructions (none performed).
- `PACKAGING.md` — extraction and handoff record.

## Quick start (local evaluation only)

```bash
pip install -e packages/defi/p23-intent-solver[dev]
python -m pytest packages/defi/p23-intent-solver/tests
```

Default mode is DRY_RUN; the module touches no chain and no funds.

## Terms summary

$127,500 one-time engagement; secondary service form: 2 bps of volume
+ 50% of price improvement, capped at 0.98% of order volume.
Full terms: ENGAGEMENT.md.
