# DEPLOY — P26 Perp DEX Hedging Swarm (suite name P06)

Instructions only. Nothing in this document authorizes a deployment,
and no deployment was performed as part of packaging. Any deployment
happens only inside a signed engagement (see `ENGAGEMENT.md`), after
the founder gates in `PACKAGING.md` are cleared.

State: EXTERNAL (buyer/counterparty). No hedging service is running
for outside accounts today.

## What would be deployed

The Python reference engine in `src/perp_hedge_swarm/` — hedge sizing,
delta-band / funding / liquidation-buffer gates, and dry-run intents.
The package contains no smart contract, no venue adapter, no wallet
integration, and no price-feed adapter. Those are engagement-scoped
integration work, not package contents.

## Config surface

Every environment variable / config key the extracted module reads
(grepped from the module source; there are exactly two env vars, both
read once at import time):

| Key | Where read | Default | Meaning |
|---|---|---|---|
| `TREASURY_ADDRESS` | `perp_hedge_swarm.py`, module constant `TREASURY` | `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` | Address recorded as the fee recipient in unwind intents. Set it to the engagement's treasury address before any engagement run. |
| `PERP_DRY_RUN` | `perp_hedge_swarm.py`, module constant `DRY_RUN` | `"1"` | Any value other than `"0"` keeps dry-run on. Intents carry `executed=False` regardless; this flag is a mode marker, not an execution switch. |

Constructor / in-code configuration (not env vars):

| Key | Default | Meaning |
|---|---|---|
| `HedgeEngine(treasury=...)` | the `TREASURY` constant above | Per-engine fee-recipient override. |
| `HedgeEngine(reverting=...)` | empty set | Position ids whose payouts use the pull fallback in simulation. |
| `HedgeEngine(live_gate=...)` | a fresh `LiveGate` with `live_enabled=False` | Execution paths (`open`, `rebalance`) raise `LiveBlockedError` until `LiveGate.present_conversion_proof(founder_marker)` is called with a non-empty founder marker. There is no env var that lifts this gate. |
| `DeltaBand(band_bps=300, rearm_bps=200)` | 3% band, 2% re-arm | Drift-band trigger tuning. Changing these changes trigger behaviour; re-run the full test suite after any change. |
| `size_short_notional(..., headroom_bps=500)` | 5% over-hedge | Sizing headroom parameter. |
| Module constants | `FEE_BPS=12`, `MIN_CAPITAL_MICRO=250_000_000` ($250), `MAX_ALLOC_PCT=0.15`, `ORACLE_STALENESS_S=120`, `CHECKIN_S=900` | Catalogued gate values mirrored as code constants. Treat as fixed for an engagement unless the engagement scope says otherwise in writing. |

Test-harness environment (only for running the repo's wider test
suites, not read by this module): `PYTHONPATH`, `FLASK_ENV`,
`SECRET_KEY`, `JWT_SECRET_KEY`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`,
`STRIPE_SECRET_KEY`.

## Dependencies

- Runtime: Python ≥ 3.11, standard library only. No third-party
  runtime dependencies, nothing to pin.
- Test: `pytest>=8` (via the `test` extra in `pyproject.toml`).
- Engagement-scoped (not in the package, supplied under the
  engagement): a price oracle object exposing
  `get_price(asset, now, strict=False) -> object with .price_fp`
  for `ETH/USD` and `BTC/USD` (the vendored stub's documented shape);
  venue access; a counterparty wallet. See `PACKAGING.md`.

## Sepolia drill (instructions only — do not run outside a signed engagement)

1. Confirm the engagement is signed and names: the counterparty, the
   venue, the Sepolia deployment scope, and the treasury address.
2. Clone the repository at the merged package commit and create an
   isolated environment:
   `python -m venv .venv && .venv/bin/pip install -e "packages/defi/p26-perp-hedge[test]"`
   (adjust the path form to the checkout layout in use).
3. Run the package suite and record the result before touching any
   network: `.venv/bin/python -m pytest packages/defi/p26-perp-hedge/tests -q`
   — expected at packaging time: 46 passed.
4. Set the config surface for the drill: `TREASURY_ADDRESS` to the
   engagement's Sepolia treasury address; leave `PERP_DRY_RUN`
   unset (or `"1"`).
5. Provision Sepolia access under the engagement: a Sepolia RPC
   endpoint and a counterparty-controlled Sepolia wallet funded with
   Sepolia test ETH from a faucet. Keys stay with the counterparty /
   founder — never in this package, never in chat or a repo.
6. Wire the drill price source: implement the engagement's Sepolia
   oracle adapter against the stub shape
   (`get_price(asset, now, strict=False) -> .price_fp`, 1e8
   fixed-point) for `ETH/USD` and `BTC/USD`, bind it with
   `price_feed_for(oracle)`, and sync it into the engine's `PriceFeed`
   via `sync_from_oracle`. Verify a stale price (>120s) raises
   `StaleOracleError` before proceeding.
7. Run the dry-run drill end to end in simulation: record 25 hourly
   funding checkpoints, open a simulated position, run 15-minute
   check-ins, force a band trip, a funding kill, and a
   liquidation-buffer breach, and confirm each produces the expected
   intent (`open` / `rebalance` / `unwind`) with `executed=False`.
8. Only if the signed engagement explicitly scopes it, the founder
   presents the conversion proof (`LiveGate.present_conversion_proof`)
   in the engagement environment. Packaging never does this step.
9. Record the drill transcript (test output, config used with secrets
   redacted, intents produced) in the engagement record and stop.
   Any step beyond the drill — a real venue, real funds, mainnet —
   is a separate written scope, not a continuation of the drill.
