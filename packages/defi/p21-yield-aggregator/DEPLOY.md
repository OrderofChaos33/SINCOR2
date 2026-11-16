# DEPLOY — P21 Yield Aggregator Vault

Instructions only. No deployment is performed as part of packaging,
and none may be performed outside a signed engagement (see
`ENGAGEMENT.md`). State: EXTERNAL.

## Dependencies

- Python >= 3.11. Runtime dependencies: **none** — the package uses
  only the Python standard library (`logging`, `os`, `time`,
  `dataclasses`, `enum`, `typing`).
- Test-only: `pytest >= 8` (`pip install -e ".[test]"`).
- External systems the allocator *references* (it does not transact
  with them; it emits plans and, in live-intent mode, intent payloads
  for an external signer/broadcaster supplied by the engagement):
  Morpho Blue / Aave v3 pools on Base, via the vault addresses in the
  config surface below.

## Config surface

Every environment variable the module reads, with its default. All are
read once at import time; changing one requires a process restart.

| Variable | Default | Meaning |
|---|---|---|
| `TREASURY_ADDRESS` | `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` | Address the internal protocol fee concept routes to. Set to the counterparty-agreed treasury inside an engagement. |
| `SHARED_LIQUIDITY_VAULT` | `0xeA90a257e5Dae20a0472C4812775F28614459bb6` | SINCOR SharedLiquidityVault address. Its strategy ships **disabled**; do not enable or fund it without the verification steps in the drill below. |
| `SHARED_LIQUIDITY_HOOK` | `0x5A20BfEc6Caa3A94246eCCCb36F27F4980152dC0` | Hook address paired with the shared-liquidity vault (informational in this module). |
| `MORPHO_USDC_VAULT` | `0xeE8F4eC5672F09119b96Ab6fB59C27E1b7e44b61` | Morpho Gauntlet USDC Prime vault on Base; the plan's primary vault reference. |
| `EXECUTE_LIVE` | `0` | `1` switches plans from `dry_run` to `live_intent` mode. Even then the module only emits intent payloads; signing and broadcast stay outside this module. |
| `YIELD_MAX_SINGLE_STRATEGY_PCT` | `0.40` | Concentration cap: max weight of any single strategy. |
| `YIELD_MAX_SLIPPAGE_BPS` | `50` | Max slippage stamped on live intents. |
| `YIELD_MIN_CAPITAL_USD` | `1.0` | Below this capital the plan warns and floors capital at 0. |

Named in the module docstring but **not read by this module**:
`EXECUTION_SIGNER_KEY` — a precondition of the surrounding platform's
live execution path. Signing keys are handled by the engagement's
external signer, never by this package, and must never be placed in
this package's configuration files.

A fill-in template is in `deploy/example.env`.

## Sepolia drill (inside a signed engagement only)

A deployer working under a signed engagement runs these steps against
Base Sepolia. The allocator itself is chain-agnostic planning logic;
the drill validates configuration, plan shape, and the external
signer path — it moves no mainnet funds.

1. Confirm the signed engagement is in force and names the Sepolia
   drill in scope. If it does not, stop.
2. Create an isolated environment: `python -m venv .venv && source
   .venv/bin/activate && pip install -e ".[test]"`.
3. Run the package suite: `python -m pytest tests/` — require all
   tests green before continuing.
4. Copy `deploy/example.env` to `.env`, replace every address with the
   Sepolia addresses agreed in the engagement, and keep
   `EXECUTE_LIVE=0`. Load it into the shell (`set -a; source .env`).
5. Dry-run smoke: in a Python shell, build
   `YieldAggregator().plan_rebalance(capital_usd=1000, risk_budget=0.30)`
   and confirm `mode == "dry_run"`, `executed is False`, allocations
   sum to 1000, and no disabled strategy appears.
6. Verify each configured vault address on the Sepolia explorer:
   contract exists, is the contract named in the engagement, and —
   for any strategy the engagement proposes to enable — has successful
   test deposits already recorded. Any strategy failing verification
   stays disabled.
7. Only if the engagement explicitly authorises intent emission:
   set `EXECUTE_LIVE=1`, rebuild the plan, and confirm `mode ==
   "live_intent"` and that intents carry the configured treasury,
   slippage cap, and vault addresses. Hand intents to the engagement's
   external signer; this package never signs or broadcasts.
8. Record the drill: config used (addresses and caps, never keys),
   plan output, and explorer links, in the engagement's drill log.
9. Reset `EXECUTE_LIVE=0` and tear down the drill environment.

## Hard stops

- No mainnet addresses, funds, or broadcasts in the drill.
- The shared-liquidity strategy remains disabled unless step 6
  verification passes under the engagement's own criteria.
- Any step requiring a signature is performed by the counterparty /
  engagement signer outside this package.
