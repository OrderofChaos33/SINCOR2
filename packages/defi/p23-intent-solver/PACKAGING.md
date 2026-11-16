# Packaging Record — P23 Intent Solver & Dark Pool (PACK-1 → PACK-5)

Package: `packages/defi/p23-intent-solver/`
Branch base: SINCOR2 `main` at commit `5d20c8fa`.
State: **EXTERNAL**. This package is not described as deployed or
live anywhere in this record; deployment happens only inside a
signed engagement.

## What existed (PACK-1 survey)

- Source module: `src/sincor2/defi/intent_dark_pool.py` — 550 lines,
  21,060 bytes. Header identifies it as "SINCOR DeFi P03 — Intent
  Solver & Dark Pool (reference build)", SKU
  `SINCOR-DEFI-P03-DARKPOOL`, matching the catalog description
  (intent-solving and dark-pool execution module). Catalog cross-check:
  `src/sincor2/defi/catalog.py` lists `P03_INTENT_DARK` with fee 8 bps
  and gates `min_out`, `split_threshold`, `axm_only_settlement` — all
  present in the code. No catalog discrepancy found.
- Existing tests (baseline, run on the unmodified tree before any
  packaging work): **37 tests, all passing** —
  `tests/pytest/test_p03_darkpool_units.py` (24) and
  `tests/pytest/test_p03_audit_invariants.py` (13). A third file,
  `tests/pytest/test_solver_full_loop.py`, concerns a different
  subsystem (`sincor2.solver.*` quoting) and is not a test of this
  module; it was not ported.
- Platform-internal dependency: exactly one. At module bottom,
  `price_feed_for()` lazily imports `wiring_for` from the sibling
  platform module `sincor2.defi.price_oracle`. All top-level imports
  are standard library.
- Bonding: the source contains no bond, stake, collateral, slashing,
  or solver-registration logic (grep-verified). See SOLVER_BONDING.md.

## Dependency inventory (PACK-1)

Top-level imports of the source module, classified:

- Standard library: `__future__.annotations`, `hashlib`, `logging`,
  `os`, `time`, `dataclasses`, `typing`. That is the complete list —
  the module has **no third-party imports** and no other
  platform-internal imports at top level.
- Platform-internal (lazy, one function): `sincor2.defi.price_oracle`
  → `wiring_for` (see stubbing note below).
- Package runtime dependencies therefore: **none** (`dependencies =
  []` in pyproject.toml). Dev-only: `pytest>=8`.

## What was packaged

### PACK-1 Extract

- `src/sincor_p23_intent_solver/intent_dark_pool.py` — copy of the
  platform module. The platform original is untouched. Two changes
  only: (1) a header paragraph recording the extraction; (2) the lazy
  oracle import now reads `from .price_oracle_stub import wiring_for`
  instead of `from .price_oracle import wiring_for`. No logic changed.
- `src/sincor_p23_intent_solver/price_oracle_stub.py` — vendored
  minimal surface replacing the platform-internal reach-in: a
  `ProductPriceFeed` / `wiring_for` pair covering only this product's
  declared assets (`ETH/USD`, `BTC/USD`), delegating to any injected
  oracle object with the platform's `get_price(...).price_fp`
  contract, fail-closed on undeclared assets and oracle errors. No
  price data or feed adapters are vendored.
- `src/sincor_p23_intent_solver/engagement_fees.py` — catalog
  engagement fee math (see PACK-4), kept separate from the code's
  internal 8 bps settlement fee.
- `src/sincor_p23_intent_solver/__init__.py`, `pyproject.toml`
  (package name `sincor-p23-intent-solver`, version 0.1.0,
  Python >= 3.10, setuptools, `pythonpath = ["src"]` for tests).

### PACK-2 Test

- Baseline on unmodified tree: **37 tests, 37 passed** (command:
  pytest on the two P03 test files with the repo test env).
- Ported both files into `tests/` with only the import path changed
  (`src.sincor2.defi.intent_dark_pool` →
  `sincor_p23_intent_solver.intent_dark_pool`); test bodies verbatim.
- Added `tests/test_engagement_fees_and_packaging.py` (12 tests):
  engagement fee math (2 bps volume component; +50% of price
  improvement; 0.98% cap binding, at-cap, and floor-division cases;
  zero/negative inputs), an explicit test that the engagement fee
  differs from the code's internal 8 bps fee, a bonding-silence test
  asserting the module exposes no bond/stake/collateral surface,
  oracle-stub binding and fail-closed tests, a packaging smoke test
  (fresh interpreter, only the package on the path, imports clean,
  no platform modules loaded), and a docs hygiene test.
- Final suite: **49 tests, all passing** (run inside the package:
  `python -m pytest tests`).

### PACK-3 Deploy package

- `DEPLOY.md`: full config surface (env vars `TREASURY_ADDRESS`,
  `DARKPOOL_SPLIT_THRESHOLD`, `DARKPOOL_DRY_RUN`, with defaults and
  effects; constructor/call-time config), dependency list, and a
  numbered 10-step Sepolia drill for use inside a signed engagement.
  Instructions only — no deployment was performed.
- `deploy/README.md`: states the directory holds no scripts, keys, or
  env files by design.

### PACK-4 Engagement sheet

- `ENGAGEMENT.md`: scope template plus the priced terms exactly as
  catalogued — $127,500 one-time engagement; secondary service form
  2 bps of volume + 50% of price improvement, capped at 0.98% of
  order volume; payment-rail form: scoped integration/deployment
  engagement, per counterparty. It states plainly that no solver is
  deployed or bonded for outside flow today, that a
  buyer/counterparty is required, and that deployment happens only
  inside a signed engagement. No performance promises, no results.
- `SOLVER_BONDING.md` (sprint extra): solver-bonding requirements
  spec. It records what the code actually checks (role sets only; no
  bonding logic of any kind) and lists every bonding term — bond
  asset, sizing basis, amount, poster, custody, lock, return
  conditions, forfeiture, eligibility, backstop — as
  "not specified in code — engagement term to be set". No numbers
  invented.

### PACK-5 Handoff

This file. See "What remains" below.

## Fee distinction (read before quoting)

Two different fees exist and must not be conflated:

- Code: `FEE_BPS = 8` — 8 bps per settled leg inside the reference
  simulation, to the treasury address.
- Engagement (catalog): 2 bps of volume + 50% of price improvement,
  capped at 0.98% of order volume — the commercial secondary form,
  implemented in `engagement_fees.py`.

## What remains before deploy

FOUNDER GATES:

- Merge tap for this extracted package (branch
  `xioix/pack-p23-intent-solver`) — packaging is committed locally
  only; nothing pushed or merged by the packaging team.
- Any deployment only inside a signed engagement; founder signature
  on the engagement precedes all deployment work.
- Dry-run stays the default until a signed engagement expressly
  scopes otherwise.

EXTERNAL DEPENDENCIES:

- A buyer/counterparty (state is EXTERNAL).
- Solver bonding capital under the engagement, on terms the signed
  engagement sets (SOLVER_BONDING.md) — none exists today.
- Engagement-scoped adapters not in this package: real price-feed
  adapters, venue connectivity, claim transfer functions, and any
  on-chain components the engagement scopes.
