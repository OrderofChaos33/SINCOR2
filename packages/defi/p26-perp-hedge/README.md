# P26 — Perp DEX Hedging Swarm (suite name P06)

Standalone package extracted from the SINCOR2 platform module
`src/sincor2/defi/perp_hedge_swarm.py` under the PACK-1→PACK-5
fulfillment sequence. See `PACKAGING.md` for the full extraction
record, `DEPLOY.md` for the config surface and Sepolia drill, and
`ENGAGEMENT.md` for the counterparty engagement terms.

## What this is

A Python reference simulation of a delta-neutral funding harvest
(LST collateral + equal perp short):

- Integer-only hedge sizing: short notional = spot exposure ×
  (1 + 5% liquidation headroom).
- A 3% delta band on drift from entry, with 1% hysteresis.
- A funding monitor: entry requires annualized funding ≥ +8% now and
  a 24h average ≥ +5%; two consecutive negative hourly checkpoints
  trigger an unwind.
- A liquidation-buffer ladder at 4x / 2x / 1.5x maintenance margin.
- A hedge engine holding multiple positions (the swarm surface),
  charging 12 bps on positive realized P&L only, with a pull-fallback
  unwind that cannot brick on a reverting recipient.
- Hard safety defaults: DRY_RUN mode, isolated margin only, oracle
  prices older than 120s block new actions, and every execution path
  is blocked until a conversion proof is presented.

## State

EXTERNAL (buyer/counterparty). This package exists as a platform
module; no hedging service is running for outside accounts. Nothing
in this package contacts a venue, a wallet, or funds.

## Layout

- `src/perp_hedge_swarm/perp_hedge_swarm.py` — the extracted module
  (the only change from the platform original is the price-oracle
  import; see PACKAGING.md).
- `src/perp_hedge_swarm/price_oracle_stub.py` — vendored minimal
  price-oracle surface replacing the platform-internal import.
- `src/perp_hedge_swarm/pricing.py` — packaging-added engagement
  pricing constants and upkeep arithmetic.
- `tests/` — adapted platform tests plus packaging-added tests.
- `deploy/DEPLOY_NOTES.md` — deployment-area notes pointing at
  `DEPLOY.md`.

## Run the tests

```bash
python -m pip install -e ".[test]"
python -m pytest tests/ -q
```
