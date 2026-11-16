# PACKAGING — P21 Yield Aggregator Vault (suite name P01)

PACK-1 → PACK-5 record for the fulfillment-sprint packaging of one
platform module. Base commit: `5d20c8fa` (SINCOR2 main). Branch:
`xioix/pack-p21-yield-aggregator`. The original platform file was not
modified.

## What existed

| Item | Path | Size |
|---|---|---|
| Source module | `src/sincor2/defi/yield_aggregator.py` | 397 lines, 15,138 bytes, sha256 `0ac6c00543e5c593a0671697b179b7df385a03dd1f38f244c3c6db01de47f107` |
| Legacy tests | `tests/test_yield_aggregator.py` | 95 lines, 8 tests |
| P01 unit tests | `tests/pytest/test_p01_vault_units.py` | 268 lines, 24 tests |
| P01 invariant tests | `tests/pytest/test_p01_audit_invariants.py` | 266 lines, 7 tests |
| Catalog entry | `src/sincor2/defi/catalog.py` | `P01_YIELD_AGG`, "Yield Aggregator Vault" |

Platform consumers (context only, untouched): `defi/engine.py`,
`defi/__init__.py`, `agents/treasury_execution_agent.py`.

**Baseline test count (unmodified tree, commit 5d20c8fa): 39 tests
collected across the three files above — 36 passed, 3 failed.** All
31 P01 tests passed. The 3 failures were all in the legacy file and
are stale expectations, each verified against the source before being
corrected in the port (details in the ported test file's docstring):

1. `test_default_strategies_present` — expected `shared_liq_vault`
   enabled; the source has it `enabled=False` (unverified vault,
   zero on-chain activity, source directs no deposits).
2. `test_tiny_capital_still_returns_plan` — used capital exactly at
   the 1.0 floor and expected a below-minimum warning; the source
   check is strict `<`.
3. `test_cash_loading_window_allocates_shared_liq` — expected a
   disabled strategy to receive an allocation, contradicting the
   disabled-never-allocated invariant in the P01 audit suite.

## PACK-1 — Extract

- Copied `yield_aggregator.py` **verbatim** into
  `src/p21_yield_aggregator/yield_aggregator.py` (sha256 identical to
  the platform file, above). No behavioural changes.
- Dependency inventory — every import in the module:
  - Standard library only: `__future__`, `logging`, `os`, `time`,
    `dataclasses`, `enum`, `typing`.
  - Third-party: **none**.
  - Platform-internal: **none**. The module imports nothing from the
    SINCOR platform, so there were no reach-ins to stub or vendor.
- Platform coupling that is *not* imports, recorded exactly:
  - Configuration arrives via environment variables (full surface in
    `DEPLOY.md`), including three SINCOR/Base addresses as defaults.
  - The docstring references platform context (the DeFi Yield
    Aggregator agent and the shared-liquidity contracts) and an
    `EXECUTION_SIGNER_KEY` precondition that belongs to the
    surrounding platform's execution path; this module never reads
    that variable and never signs or broadcasts.
  - In-package stub/vendor work: **none required**. The only
    addition is `fees.py`, new engagement-level fee accounting that
    did not exist in the platform module (see PACK-2).

## PACK-2 — Test

- Ported all three existing suites into `tests/` with import-path
  rewrites only (plus removal of the repo `sys.path` boilerplate,
  replaced by `tests/conftest.py`). The 3 stale legacy tests were
  corrected as documented above and in the ported file.
- Added coverage where core paths lacked it:
  - Deposit routing / yield-source selection: risk-adjusted ordering,
    risk-budget boundary selection, zero-capital safety
    (`tests/test_yield_aggregator.py`, 11 tests total in that file).
  - Fee accounting at the engagement terms — 10% performance fee on
    positive yield only, 0% management fee at any AUM, and a guard
    pinning the internal 10 bps treasury concept apart from the 10%
    performance fee (`tests/test_fees.py`, 6 tests; implementation in
    `src/p21_yield_aggregator/fees.py`).
  - Packaging smoke test: package imports clean standalone, every
    import is standard library, no platform imports anywhere in the
    package source (`tests/test_packaging_smoke.py`, 3 tests).
- **Final suite: 51 tests, all passing** (24 vault units + 7 audit
  invariants + 11 allocator/routing + 6 fees + 3 smoke), run with
  the repo test venv against the package alone.

## PACK-3 — Deploy package

- `DEPLOY.md`: dependency list (Python >= 3.11, zero runtime
  dependencies), the full config surface (8 variables the module
  reads, with defaults, plus the one docstring-named variable it
  does not read), and a 9-step Sepolia drill for use inside a signed
  engagement. `deploy/example.env` is the fill-in template.
- No deployment was performed. Instructions only.

## PACK-4 — Engagement sheet

- `ENGAGEMENT.md`: status (not separately deployed or sold today;
  counterparty required; deployment only inside a signed
  engagement), scope template, and the priced terms exactly as
  catalogued: **$120,000 one-time engagement; secondary service form
  10% performance fee, 0% management fee; payment-rail form: scoped
  integration/deployment engagement, per counterparty.** No
  performance promises and no invented results.

## PACK-5 — Handoff: what remains

State stays **EXTERNAL**. This package is packaged, not deployed;
nothing about it is live.

Founder gates:

- **Merge tap**: the founder must approve merging this branch's
  extracted package into the platform repo. (This branch was
  committed locally only — no push, no merge.)
- **Any deployment only inside a signed engagement**: no drill, no
  configuration for a counterparty, and no live-intent use without a
  signed engagement in force.

External dependencies:

- **A buyer/counterparty** — required for any engagement, payment,
  or deployment.
- Counterparty-provided infrastructure, accounts, addresses, and an
  external signer/broadcaster (this module never signs).
- The two disabled strategies (shared-liquidity vault, Uniswap V4
  CLMM) stay disabled until the verification conditions recorded in
  their strategy notes are met under an engagement's own criteria.
