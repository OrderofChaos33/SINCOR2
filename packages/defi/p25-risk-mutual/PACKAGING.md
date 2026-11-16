# PACKAGING.md — P25 DeFi Risk Mutual: Packaging Handoff (PACK-5)

Fulfillment sprint, DeFi Build Team 05. Module: **P25 — DeFi Risk
Mutual** (suite name **P05**), source file `insurance_mutual.py`.
Base commit: `5d20c8fa` (SINCOR2 `main`). Branch:
`xioix/pack-p25-risk-mutual`. Package directory:
`packages/defi/p25-risk-mutual/`.

**Status: packaged, not deployed.** State stays **EXTERNAL**. No mutual
is capitalized or open. Nothing in this record describes the package as
deployed or live.

## What existed

| Item | Path (platform tree) | Size / count |
|---|---|---|
| Module | `src/sincor2/defi/insurance_mutual.py` | 516 lines, 19,918 bytes, sha256 `7975265e…c572de` |
| Existing tests | `tests/pytest/test_p05_insurance_units.py` | 411 lines, **28 tests** |
| Catalog entry | `src/sincor2/defi/catalog.py` | `ProtocolSpec(5, "P05_INSURANCE", "DeFi Risk Mutual", …)` |
| Product page template | `templates/products/p05-defi-insurance.html` | reference only, not packaged |
| Task decomposition | `liveness/defi_catalog/p05-defi-insurance.json` | reference only, not packaged |

**Baseline (PACK-2, recorded before any packaging work):** the existing
suite on the unmodified tree at `5d20c8fa` ran **28 passed / 0 failed**
(`tests/pytest/test_p05_insurance_units.py`, venv `~/.venvs/sincor2`,
`PYTHONPATH=src:.` with the CI test env).

Naming note: the platform also contains `src/sincor2/defi/p25/`, which
is a *different* module (Agent-Managed Portfolio). It is not this
module's source, was not read into this package, and was not touched.
The catalog-described source for the DeFi Risk Mutual is
`insurance_mutual.py` (suite P05), exactly as the task specified.

## What was packaged

### PACK-1 — Extract

- `src/sincor_p25_risk_mutual/insurance_mutual.py` — **verbatim copy** of
  the platform file (sha256 identical: `7975265e…c572de`). The original
  platform file is untouched.
- `src/sincor_p25_risk_mutual/__init__.py` — re-exports the module's
  public API (classes, errors, constants, `commitment_id`).
- `pyproject.toml` — standalone metadata; **zero runtime dependencies**.
- Dependency inventory — every import in the module, classified:

  | Import | Class |
  |---|---|
  | `__future__.annotations` | stdlib (compiler directive) |
  | `hashlib`, `logging`, `os`, `time` | stdlib |
  | `collections.defaultdict` | stdlib |
  | `dataclasses` (`asdict`, `dataclass`, `field`) | stdlib |
  | `typing` (`Any`, `Dict`, `List`, `Optional`, `Tuple`) | stdlib |
  | third-party | **none** |
  | platform-internal (`sincor2.*`, catalog, gates, …) | **none** |

  (`asdict`, `field`, and `Any` are imported but unused by the module
  body; they are stdlib and were left as-is to keep the copy verbatim.)
- **Reach-ins: none to stub or vendor.** The module depends on no
  platform internals — it does not import the catalog treasury (it reads
  its own `TREASURY_ADDRESS` env var with the same default address) and
  no other SINCOR2 code. The smoke test enforces this: the packaged
  module's imports are asserted stdlib-only, and a clean-interpreter
  import asserts no `sincor2` module is loaded.

### PACK-2 — Test

- `tests/test_p05_insurance_units.py` — the platform's 28 tests with
  only the header/import rewired to the packaged module; test bodies
  unchanged. All 28 pass against the extracted package.
- `tests/test_p25_additional.py` — **25 added tests** for core paths the
  platform suite did not cover: premium tier boundaries, full-year
  annualization, duration linearity, non-positive rejection, treasury-fee
  rounding, the engagement premium band (2.5%–3.25% of cover per year)
  as commercial-term arithmetic kept distinct from the code's tier
  schedule, deposit validation, pro-rata share pricing after premium
  accrual, partial redemption, outstanding-cover accounting, unknown /
  unapproved / majority-rejected claims, payout idempotency, solvent
  shortfall factor, timelock/governance/registry edges, and config
  defaults.
- `tests/test_packaging_smoke.py` — **3 packaging smoke tests**
  (standalone import, stdlib-only inventory, API surface).
- **Final suite: 56 passed / 0 failed** (28 adapted + 25 added + 3
  smoke), run inside the package with only its declared dependencies.

### PACK-3 — Deploy package

- `DEPLOY.md` — config surface (both env vars the module reads, with
  defaults and import-time semantics), dependency list, and a numbered
  12-step Sepolia drill for use inside a signed engagement. Instructions
  only; nothing was deployed.
- `deploy/env.example` — annotated config names/defaults, no secrets.

### PACK-4 — Engagement sheet

- `ENGAGEMENT.md` — scope template plus the priced terms exactly as
  catalogued: **$36,250 one-time engagement**; secondary service form
  **cover premium 2.5%–3.25% of cover amount per year**; payment-rail
  form: scoped integration/deployment engagement, per counterparty. It
  states plainly that no mutual is capitalized or open today, that a
  buyer/counterparty is required, and that deployment happens only
  inside a signed engagement. No coverage promises, no results claimed.
- `CAPITALIZATION.md` — the sprint-extra capitalization requirements
  spec (summary in §Discrepancies/notes below; also §4 of ENGAGEMENT.md).

### PACK-5 — Handoff

- This file, plus `README.md` (package overview and layout).

## Discrepancies and honest notes (found while packaging)

1. **Premium band vs code schedule.** The engagement premium band
   (2.5%–3.25% of cover/yr) does not appear in code. The code's tier
   schedule yields exactly 100/200/400 bps (1%/2%/4%). Both are recorded
   as what they are — a commercial term vs a reference-pricing
   mechanism — in ENGAGEMENT.md §1 and pinned by a test so they are
   never conflated.
2. **`MIN_CAPITAL_WEI` is not enforced.** The $200 (6dp wei) constant is
   defined and never referenced; `deposit_capital` checks only
   `amount > 0`. CAPITALIZATION.md §2.1 records this and declines to
   present $200 as a capitalization requirement. `MAX_ALLOC_PCT = 0.15`
   is likewise defined and never referenced (§2.2).
3. **The one enforced capitalization rule** is the 130% reserve floor
   on underwrite/redeem (CAPITALIZATION.md §1.1). Minimum pool
   capitalization, concentration limits, bond denominations, custody,
   and top-up triggers are not specified in code — each is recorded in
   the spec as "not specified in code — engagement term to be set".
4. **`DRY_RUN` is read but never consulted.** `MUTUAL_DRY_RUN` is parsed
   at import; no method branches on it. Behavior is dry-run by
   construction (no chain/fund code exists). Recorded in DEPLOY.md §2.
5. **Docstring vs code.** The module docstring names a `LiveGate` class
   and the suite describes a claim engine; in code the live gate is the
   `MutualPool.live_underwriting` boolean (default `False`, no setter —
   release is an external procedure) and claims are `MutualPool`
   methods. No behavior was changed; the difference is recorded here
   and in CAPITALIZATION.md §1.3.

No catalog discrepancy on the module itself: `insurance_mutual.py`
exists substantially as catalogued (risk-mutual / coverage pool,
Nexus-style, SINAX-shaped claims, 130% reserve gate, 30-day window).

## What remains before deploy

FOUNDER GATES:

- **Merge tap** — a founder merge decision for this extracted package
  branch (`xioix/pack-p25-risk-mutual`). Not pushed, not merged by this
  sprint.
- **Any deployment only inside a signed engagement** — no deploy step
  is authorized by this package alone; the Sepolia drill in DEPLOY.md
  is an in-engagement procedure, and any phase beyond the drill needs
  its own approval under that engagement.
- **Live release** — underwriting stays blocked until the auditor +
  actuarial + founder release described in CAPITALIZATION.md §1.3 is
  given under an engagement.

EXTERNAL DEPENDENCIES:

- **A counterparty** — a buyer for the $36,250 engagement (state:
  EXTERNAL; none is attached to this package).
- **Capitalization under the engagement** — reserves ≥ 130% of the
  initial cover schedule, delivered by the engagement's capital
  provider(s), plus every "engagement term to be set" item in
  CAPITALIZATION.md §§2–3 resolved in writing.
- **Assessor panel** — allowlisted assessors with bonds and voting
  weights satisfying the > 5× cover quorum rule, arranged under the
  engagement.
