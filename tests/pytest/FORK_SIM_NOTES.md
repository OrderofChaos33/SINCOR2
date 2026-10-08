# Fork-Simulation Notes — DeFi Product Arm (audit-prep)

**Status (2026-09-30):** the harness now exists at `fork_sim/run_fork_sims.py`
(branch `xioix/buildout-25-fork-sim-harness`). It replays read-only against
Base mainnet state at a pinned block via public JSON-RPC — no Anvil, no
signing, no broadcasting. 17/26 products have passing `fork_sim` evidence in
the proof ledger; the other 9 remain findings (blockers below), never passes.

The original scaffolding sketch for P01–P03 is preserved below as the plan
record; the harness supersedes it for the products it covers.

**Standing rule:** never invent mainnet addresses. Every address used is
copied verbatim from the codebase. Anything not in the codebase stays a
finding, never a pass.

## Pinned in code (usable on a fork)

| Symbol | Address | Source |
|---|---|---|
| `TREASURY` | `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` | `yield_aggregator.py`, `clmm_manager.py`, `intent_dark_pool.py` |
| `MORPHO_USDC_VAULT` (gtUSDCp) | `0xeE8F4eC5672F09119b96Ab6fB59C27E1b7e44b61` | `yield_aggregator.py` (verified, deposit/redeem live) |
| `SHARED_LIQUIDITY_VAULT` | `0xeA90a257e5Dae20a0472C4812775F28614459bb6` | `yield_aggregator.py` — **UNVERIFIED, 0 txs. Excluded from any value-touching sim.** |
| `SHARED_LIQUIDITY_HOOK` | `0x5A20BfEc6Caa3A94246eCCCb36F27F4980152dC0` | `yield_aggregator.py` — same exclusion |

## Unpinned blockers (do NOT proceed to fork-sim until pinned)

- **Aave v3 Pool on Base** — referenced by strategy `aave_usdc` (`protocol="aave_v3"`)
  but no address is pinned anywhere in the repo. Unpinned at spec time.
- **Morpho Blue core / other Morpho markets** — only the gtUSDCp vault above
  is pinned; any other Morpho market address is unpinned.
- **Balancer Vault / pools on Base** — no address in the repo.
- **Polymarket CTF / conditional-tokens contracts** — no address in the repo.
- **Uniswap V4 PoolManager / position manager on Base** — P02 is a Python
  reference model of hook logic; no V4 deployment address is pinned.
- **AXM / USDC token addresses on Base** — P03 settles "AXM/USDC only" as
  symbols; no token contract addresses are pinned in the repo.

Until these are pinned (auditor-signed config, per the live-LP gate rule),
fork-sim stays at the "read-only replay" level described below. Nothing
here authorizes inventing an address to fill a gap.

## Proposed harness shape (per module)

Common setup (when built): `anvil --fork-url <BASE_RPC> --fork-block-number <PINNED>`
at a pinned block; all sims are `eth_call` / log-replay only — **zero
signed transactions, zero value movement**. Assert the fork state diff is
empty at the end of every sim (any write = harness bug).

### P01 — yield_aggregator
1. Fork-read `MORPHO_USDC_VAULT.convertToAssets(1e18)` / `totalAssets()` at the
   pinned block; feed the realized share price into `plan_rebalance` as the
   `estimated_apr` input (replacing the static table) and assert the plan
   invariants (conservation, cap, risk budget) still hold on live numbers.
2. Replay: take N historical blocks, re-run the allocator on each block's
   vault state, assert the allocation sequence is deterministic and the
   blended APR tracks the realized vault APR within a documented tolerance.
3. Blocked until: Aave v3 Pool address pinned (else the `aave_usdc` leg can
   only be simulated against the Morpho leg).

### P02 — clmm_manager
1. Replay `LiquidityEvent` streams reconstructed from Uniswap V4
   `PoolManager` logs (`ModifyLiquidity` / `Swap`) on the fork for a labeled
   window containing known JIT attacks; run `JITDetector.detect` and score
   precision/recall against the labels. This is the empirical calibration
   the `jit_sensitivity_bps` threshold needs before the auditor gate.
2. Feed realized tick data into `VolRangeAgent.range_width_ticks` and
   compare emitted re-range intents against the historically optimal ranges
   (hindsight IL benchmark) — informational only.
3. `ProceedsDistributor` / `TransientFee` have no on-chain counterpart yet;
   their fork-sim is conservation/fuzz only (already covered in
   `test_p02_audit_invariants.py`).
4. Blocked until: V4 PoolManager address + a labeled JIT-attack window are
   pinned; auditor-signed `LIVE_LP_AUDITOR_GATE` config exists.

### P03 — intent_dark_pool
1. Fork-read AXM/USDC balances for a set of test EOAs (impersonated via
   `anvil_impersonateAccount`, no keys) and run the full
   submit → match → settle → claim flow against forked balances in
   read-only mode: assert per-asset conservation and the exact 8 bps fee
   against `balanceOf` snapshots before/after the *simulated* (not
   executed) settlement.
2. Replay historical DEX quotes as `VenueQuote`s into `SplitSolver.route`
   and measure realized vs. modeled execution — the split-improvement claim
   needs this before any live routing.
3. Blocked until: AXM and USDC token addresses on Base are pinned; the
   `axm_only_settlement` allowlist is finalized in the same signed config.

## Explicit non-goals

- No live transactions, no signing, no broadcasting — from any module.
- No invented addresses, ever. A gap stays a gap until the auditor pins it.
- Passing fork-sims are claimed only via `fork_sim/run_fork_sims.py` runs
  whose entries land in the proof ledger with `passed: 1, failed: 0`; the
  original plan sketches below are not evidence by themselves.
