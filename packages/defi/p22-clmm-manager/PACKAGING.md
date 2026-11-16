# Packaging Record — P22 Concentrated Liquidity Manager (PACK-1 → PACK-5)

Module: **P22 — Concentrated Liquidity Manager** (suite name **P02**)
SKU: `SINCOR-DEFI-P02-CLMM`
Catalog description: concentrated-liquidity position management module
for v3/v4-style pools. Exists as a platform module; not separately
deployed.
State: **EXTERNAL** (buyer/counterparty required)
Base commit: `5d20c8fa` (SINCOR2 main) · Branch: `xioix/pack-p22-clmm-manager`
Package location: `packages/defi/p22-clmm-manager/`

This package's status is EXTERNAL and packaged-only. Nothing in this
record describes the package as deployed or live, because it is
neither: deployment happens only inside a signed engagement.

## What existed (before packaging)

| Item | Detail |
|---|---|
| Platform source | `src/sincor2/defi/clmm_manager.py` — 518 lines, 20,630 bytes, SHA-256 `a98cb72c2cf77026806c21c2d1058dd1607ef9ded151c35ab789e33c0402fad5` at base commit `5d20c8fa`. A Python reference implementation: `JITDetector`, `TickShiftResponder`, `TransientFee`, `ProceedsDistributor`, `VolRangeAgent`, `AccessControl`, plus event/state dataclasses. Dry-run by default; live-position opening hard-blocked behind an auditor gate constant. The platform file was **not modified** by this packaging run. |
| Existing tests | `tests/pytest/test_p02_clmm_units.py` (24 tests) and `tests/pytest/test_p02_audit_invariants.py` (14 tests) |
| Baseline test count | **38 tests, all passing** on the unmodified tree at `5d20c8fa` (run 2026-11-16 with the SINCOR2 test venv, `PYTHONPATH=src:.` and the CI test env). |
| Catalog cross-check | `src/sincor2/defi/catalog.py` carries the matching entry: `ProtocolSpec(2, "P02_CLMM", "Concentrated Liquidity Manager", ...)` with gates `vol_band`, `tick_distance`, `dry_run_default`. |

Naming note (not a discrepancy): the directory `src/sincor2/defi/p22/`
in the platform tree is a *different* module (a stablecoin yield
maximizer) that shares the "P22" label under a different numbering
scheme. The catalogued P22/P02 Concentrated Liquidity Manager is
`clmm_manager.py`, which is what this package extracts — exactly the
source file named in the packaging task.

## PACK-1 — Extract

- Copied `clmm_manager.py` into
  `src/p22_clmm_manager/clmm_manager.py` as a standalone package
  (`pyproject.toml`, src layout, package name `p22_clmm_manager`).
- Dependency inventory: `DEPENDENCIES.md`. Result: the core imports
  **only the Python standard library**; there are no third-party
  runtime dependencies (`dependencies = []` in `pyproject.toml`).
- Platform reach-ins found: exactly **one**. `price_feed_for()` lazily
  imports `wiring_for` from the platform's shared
  `sincor2.defi.price_oracle`. Disposition: the minimal used surface
  was vendored as `src/p22_clmm_manager/price_oracle_stub.py`
  (`wiring_for` + `ProductPriceFeed` with `product_key` / `assets` /
  `price` / `require_all`, fail-closed on undeclared assets and oracle
  errors, declared assets `["ETH/USD", "USDC/USD"]` matching the
  platform's `PRODUCT_PRICE_NEEDS["P02_CLMM"]` and the module's
  `PRICE_ASSETS`). The stub fetches no prices and wires no feed.
- **Single deviation from a verbatim copy:** line 517 of the extracted
  core changed from `from .price_oracle import wiring_for` to
  `from .price_oracle_stub import wiring_for`. Every other byte is
  identical to the platform original (verified by diff; the one-line
  diff is the whole diff). The original platform file stays untouched.
- Added (new package code, not platform code):
  `src/p22_clmm_manager/service_fees.py` — engagement service-form
  accounting (PACK-2 need, below) — and
  `src/p22_clmm_manager/__init__.py` re-exporting the public surface.

## PACK-2 — Test

- Baseline on the unmodified tree: **38 passed** (24 unit + 14 audit
  invariants).
- The two platform test files were adapted into `tests/` with test
  bodies unchanged; only the `sys.path` setup and the import path were
  redirected from `src.sincor2.defi.clmm_manager` to
  `p22_clmm_manager.clmm_manager` (adaptation note at the top of each
  file). All 38 pass against the extracted package standalone.
- Added coverage where core paths lacked it (19 new tests):
  - `tests/test_service_fees.py` (9 tests) — fee accounting at the
    engagement's secondary service form: **15% of earned LP trading
    fees**, integer-exact, per-pool ledger without rounding drift,
    plus an explicit guard that this 15% is not the core module's
    internal 15 bps treasury cut on captured JIT proceeds.
  - `tests/test_position_lifecycle.py` (6 tests) — position open/close
    paths: re-range intent as the (dry-run-only) opening flow, the
    hard-blocked live open, close-out via remove events + one-time
    pull claim, and range/tick math (spacing snap, band clamp,
    vol scaling, realized-vol edge cases, commitment ids).
  - `tests/test_packaging_smoke.py` (4 tests) — packaging smoke test:
    the package imports clean with only its declared dependencies
    (stdlib-only import scan over every package module), no import
    reaches into the platform tree, and `price_feed_for` works against
    the vendored stub, failing closed on undeclared assets.
- **Final suite: 57 passed, 0 failed** (38 adapted + 19 added), run
  standalone from the package directory with only the package's `src/`
  on the path provided by the tests themselves.

## PACK-3 — Deploy package

- `deploy/DEPLOY.md`: full config surface (every env var the module
  reads, with defaults — `TREASURY_ADDRESS`, `CLMM_VOL_BAND_TICKS`,
  `CLMM_TICK_SHIFT_STEP`, `CLMM_JIT_SENSITIVITY_BPS`,
  `CLMM_JIT_SENSITIVITY`, `CLMM_COOLDOWN_BLOCKS`,
  `CLMM_MAX_SHIFT_TICKS`, `CLMM_JIT_FEE_CAP_BPS`, `CLMM_DRY_RUN` — plus
  the non-env code constants and the `PoolConfig` keys), the dependency
  list (Python >= 3.11, stdlib only; engagement-supplied oracle
  adapter), and a numbered 12-step Sepolia drill to run inside a
  signed engagement.
- `deploy/config.example.env`: the same surface as a copy-in template.
- No deployment was performed. Instructions only.

## PACK-4 — Engagement sheet

- `ENGAGEMENT.md`: status statements (not separately deployed today;
  a buyer/counterparty is required; deployment only inside a signed
  engagement), module scope, the priced terms exactly as catalogued —
  **$80,000 one-time engagement**; secondary service form **15% of
  earned LP trading fees**; payment-rail form: scoped
  integration/deployment engagement, per counterparty — a per-
  counterparty scope template, and a sign-off block. No performance
  promises and no results are stated anywhere in the sheet.

## PACK-5 — Handoff: what remains before deploy

FOUNDER GATES (founder action required; nothing here self-executes):

1. **Merge tap** — this extracted package reaches main only if the
   founder taps the merge of branch `xioix/pack-p22-clmm-manager`.
   This packaging run did not push and did not merge.
2. **Any deployment only inside a signed engagement** — the Sepolia
   drill in `deploy/DEPLOY.md` is run only after an engagement is
   signed with a counterparty; there is no speculative deployment.
3. **Auditor gate** — flipping `LIVE_LP_AUDITOR_GATE` (required before
   any live-position opening) happens only with a signed auditor
   config inside an engagement, as a separately signed step.

EXTERNAL DEPENDENCIES:

1. **A counterparty** — state stays EXTERNAL until a buyer/counterparty
   signs an engagement on the terms in `ENGAGEMENT.md`.
2. **A price-oracle adapter** supplied by the engagement, satisfying
   the stub contract in `price_oracle_stub.py`; the package wires no
   feed of its own.
3. **An agreed treasury address** for the engagement, set via
   `TREASURY_ADDRESS` at drill time (the code default is the platform
   default, not an engagement agreement).

## Catalog discrepancy

None. `clmm_manager.py` exists in `src/sincor2/defi/` substantially as
the catalog describes (concentrated-liquidity position management for
v3/v4-style pools; platform module; not separately deployed), and the
catalog entry in `src/sincor2/defi/catalog.py` matches it. The only
naming wrinkle — the unrelated `src/sincor2/defi/p22/` directory — is
recorded above and does not affect the extraction.
