# Deploy Guide — P23 Intent Solver & Dark Pool (instructions only)

No deployment has been performed for this package. This document is
the instruction set a deployer would run **inside a signed
engagement** (see ENGAGEMENT.md). State remains EXTERNAL until then.

## What this package is at deploy time

A pure-Python reference package (standard library only). The module is
a simulation: default mode is DRY_RUN, `executed` is always `False`,
and it touches no chain and no funds by itself. "Deploying" it inside
an engagement means standing up the package in the counterparty's
agreed environment and wiring the engagement-scoped adapters (price
feeds, transfer functions for claims, venue allowlist) that the
reference build deliberately leaves injected. Any on-chain components
are new engagement-scoped work, not part of this package.

## Config surface (every env var / config key the module reads)

All three are read once at import time in `intent_dark_pool.py`.

| Env var | Default | Effect |
|---|---|---|
| `TREASURY_ADDRESS` | `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` | Treasury address recorded on settlements as the fee recipient. |
| `DARKPOOL_SPLIT_THRESHOLD` | `5000` | Intents with `amount_in` at or above this value are eligible for split routing in `SplitSolver` (also settable per-instance via the constructor). |
| `DARKPOOL_DRY_RUN` | `1` (dry-run on) | Any value other than `0` keeps dry-run on. `Batch.dry_run` / `RoutePlan.dry_run` and `executed=False` reflect this. Keep `1` outside a signed engagement. |

Constructor / call-time config (not env):

- `SplitSolver(split_threshold=...)` — per-instance override.
- Role sets for pause/unpause are passed in as plain dicts of sets
  (`MATCHER_ROLE`, `GUARDIAN_ROLE`, `ADMIN_ROLE` keys); the engagement
  must define who holds each role.
- Settlement transfer behaviour is injected per claim via the
  `transfer` callable to `SettlementEngine.claim(...)`.
- Price oracle: an oracle object is injected into `price_feed_for(...)`;
  the package vendors only the wiring stub (see PACKAGING.md).

## Dependencies

- Runtime: Python >= 3.10, standard library only. No third-party
  runtime dependencies (see `pyproject.toml`: `dependencies = []`).
- Dev/test: `pytest>=8` (optional `dev` extra).
- Engagement-scoped (not in this package): real price-feed adapters,
  chain/RPC access, venue connectivity, and solver bonding capital —
  each defined by the signed engagement.

## Sepolia drill (inside a signed engagement only)

1. Confirm the signed engagement is in place, including completed
   solver-bonding terms (SOLVER_BONDING.md) and named role holders.
2. Create an isolated environment; install the package:
   `pip install -e packages/defi/p23-intent-solver[dev]`.
3. Run the package test suite and confirm it is green before any
   drill step: `python -m pytest packages/defi/p23-intent-solver/tests`.
4. Set drill config explicitly: `DARKPOOL_DRY_RUN=1`,
   `TREASURY_ADDRESS=<drill treasury>`,
   `DARKPOOL_SPLIT_THRESHOLD=<engagement value>`.
5. Point all chain interaction at Sepolia (chain id 11155111) using
   the engagement's own RPC endpoint and drill accounts. No mainnet
   endpoints, no production keys.
6. Inject the engagement's Sepolia price-feed adapter in place of the
   stub oracle and verify `price_feed_for(oracle).require_all()`
   returns prices for `ETH/USD` and `BTC/USD`, failing closed when
   the adapter is stopped.
7. Run the intent lifecycle drill in dry-run: submit intents, verify
   only commitments are recorded, cancel one, sweep an expired one,
   and confirm nonce replay is rejected.
8. Run matching and settlement drills in dry-run over AXM/USDC pairs
   only; confirm the asset gate rejects a non-AXM/USDC pair with no
   state change, and confirm per-asset conservation
   (gross == net + fees) in the settlement output.
9. Exercise pull claims with the engagement's Sepolia transfer
   function, including one deliberately failing transfer, and confirm
   funds remain claimable (non-bricking rule).
10. Record drill evidence (test output, config used, transaction
    hashes if the engagement scoped any on-chain drill contracts)
    and hand it to the founder gate before any discussion of
    non-drill environments.

## Hard stops

- No step above runs outside a signed engagement.
- No solver handles outside flow until bonding terms are set and
  executed per SOLVER_BONDING.md.
- Dry-run stays on (`DARKPOOL_DRY_RUN` unset or `1`) until the signed
  engagement expressly scopes otherwise.
