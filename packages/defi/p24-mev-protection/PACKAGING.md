# P24 — MEV Protection & Capture (suite P04): Packaging Record

Package: `packages/defi/p24-mev-protection/`
Branch: `xioix/pack-p24-mev-protection`, based on platform commit
`5d20c8fa`. Catalog discrepancy found: **none** — the module exists in
the codebase substantially as the catalog describes (details below).

Commercial framing, binding on this package: the engagement sells
**protection / integration work only**. SINCOR's own backrun lane was
separately measured and closed. No document in this package makes a
capture-revenue claim, and none should be added.

## What existed (before packaging)

| Item | Path | Size |
|---|---|---|
| Module | `src/sincor2/defi/mev_capture.py` | 597 lines, 23,320 bytes, sha256 `46ab59af6fd4798d8473fb797e0f2e355a4cba4a6d6fc098d4de2fbee2478fdf` |
| Catalog entry | `src/sincor2/defi/catalog.py` — `ProtocolSpec(4, "P04_MEV", "MEV Protection & Capture", "mev", fee_bps=20, live_blocked=True, gates=("flow_threshold", "no_treasury_key"))` | — |
| Unit tests | `tests/pytest/test_p04_mev_units.py` | 397 lines, 14,463 bytes, 33 tests |
| Invariant/fuzz tests | `tests/pytest/test_p04_mev_fuzz_invariants.py` | 333 lines, 12,757 bytes, 12 tests |

The module is a Python reference build: per-block swap-flow metering
(`FlowMeter`, transient-storage gas model), sandwich / backrun
detection behind a flow gate (`MEVDetector`), a dry-run bid-intent
model with a 3x gas shading rule (`Bidder`), reference capture
accounting (`CaptureLedger`, `TreasuryRouter`), per-order protection
(`ProtectionPolicy`), a treasury-EOA signer deny-list
(`SignerRegistry`), a founder-release submission gate (`LiveGate`),
and role-based access control (`AccessControl`). Default mode is
dry-run; the submission path is gated and ships no execution path;
the source header itself states it is a reference build, not a
deployed protocol. Those internal class names are the platform
source's own vocabulary for the simulation; they do not change the
commercial scope stated above.

**Baseline test count (unmodified tree, commit `5d20c8fa`): 45 passed**
(33 unit + 12 fuzz/invariant), run with the platform test environment
(`PYTHONPATH=src:.`, venv `~/.venvs/sincor2`).

### Related platform artifacts — deliberately NOT packaged

- `src/sincor2/mev_protected_routing.py` (813 lines, 33,141 bytes) and
  its tests `tests/pytest/test_cnp9_mev_protection.py` +
  `tests/pytest/test_w172_mev_inclusion_reverify.py` (23 tests, all
  passing at baseline) belong to CNP-9, the sealed-bid auction routing
  layer. That module's own header states it is a separate concern from
  this DeFi product. It is a different module with platform-internal
  dependencies (`sincor2.a2a_inbound*`) and is out of scope for this
  package.
- `onchain/src/hooks/MoebiusMEVHook.sol` (350 lines) is the on-chain
  hook counterpart in the platform repo. This packaging task covers
  the Python module `mev_capture.py`; the Solidity artifact stays in
  the platform tree and is not vendored here.

## What was packaged

### PACK-1 — Extract

- `src/p24_mev_protection/mev_capture.py` — **byte-identical copy** of
  the platform source (sha256 verified equal after copy). The original
  platform file is untouched.
- `src/p24_mev_protection/__init__.py` — public API re-exports
  (`__all__`, `__version__ = "0.1.0"`).
- `pyproject.toml` — standalone package metadata, `src/` layout,
  zero runtime dependencies.

**Dependency inventory** (every import in the extracted module):

| Import | Classification |
|---|---|
| `__future__.annotations` | stdlib (compiler directive) |
| `hashlib`, `logging`, `os`, `time` | stdlib |
| `collections.defaultdict` | stdlib |
| `dataclasses` (`asdict`, `dataclass`, `field`) | stdlib |
| `typing` (`Any`, `Dict`, `List`, `Optional`, `Tuple`) | stdlib |
| third-party | **none** |
| platform-internal (`sincor2.*`) | **none** |

**Reach-ins / stubs / vendoring: none required, none performed.** The
module imports nothing from the platform — it is self-contained. Its
one platform-flavoured value, the treasury address, is a literal
default inside the module itself, overridable via the
`TREASURY_ADDRESS` environment variable; no catalog or config module
is consulted at runtime. Test-only dependency: `pytest >= 8`.

### PACK-2 — Test

- The two existing suites were copied into `tests/` with **import
  plumbing only** changed (`src.sincor2.defi.mev_capture` →
  `p24_mev_protection.mev_capture`; the repo-root `sys.path` shim
  replaced by `tests/conftest.py` putting the package `src/` on the
  path; one docstring provenance line). A diff against the platform
  originals confirms no test body, assertion, or fixture changed.
- Added `tests/test_protection_submission_config.py` (16 tests) for
  core paths the platform suites exercise thinly: protection-policy
  boundaries and precedence (exact-guardrail pass, custom guardrail,
  toxic-flag precedence, absolute-deviation slippage, loss-reduction
  edges), submission behaviour (post-release submission still has no
  execution path; deny-list re-checked at submission time; backrun
  flag intent shape; exact 3x shading boundary), and configuration
  behaviour (per-instance detector thresholds overriding defaults,
  flow-gate exact boundaries, zero-actual reconciliation, role/pause
  guards, commitment-id properties).
- Added `tests/test_packaging_smoke.py` (7 tests): the package imports
  clean in a fresh interpreter with only its own `src/` on the path
  and no platform package imported; every `__all__` name resolves; the
  environment-variable surface parsed from the source equals exactly
  the five variables documented in DEPLOY.md; the module declares no
  RPC/stream/network endpoint configuration; environment overrides and
  defaults drive the module constants as documented.
- **Final test count: 68 passed** (45 adapted + 23 added), run
  standalone from the package directory with no platform environment.
  A `pip install` of the package into a clean target directory was
  also verified: the installed package imports and evaluates a
  protected order correctly.

### PACK-3 — Deploy package

- `DEPLOY.md`: full configuration surface (the five environment
  variables with defaults, plus the fixed code constants),
  dependency list, an explicit note that the module has no RPC/stream
  configuration (that service is counterparty-side, pass-through at
  cost), and numbered Sepolia drill instructions for use inside a
  signed engagement. Instructions only — nothing was deployed.
- `deploy/env.example`: the complete environment surface, ready to
  copy into an engagement environment.

### PACK-4 — Engagement sheet

- `ENGAGEMENT.md`: scope template for a counterparty engagement with
  the priced terms stated exactly — **$61,050 one-time** scoped
  integration/deployment engagement per counterparty; underlying
  protected RPC/stream **$0–$300 as listed, pass-through at cost**.
  Protection/integration scope only; exclusions name live submission
  and capture activity as out of scope; no performance promises and
  no results are stated or implied. The sheet states plainly that the
  module is not separately deployed today, that a buyer/counterparty
  is required, and that deployment happens only inside a signed
  engagement.

### PACK-5 — Handoff

This file.

## What remains before any deployment

**Founder gates (all still closed):**

1. **Merge tap** — this extracted package reaches the platform main
   branch only on the founder's merge approval. Packaging was done on
   a local branch; nothing was pushed or merged.
2. **Signed-engagement gate** — any deployment of this module happens
   only inside a signed counterparty engagement (ENGAGEMENT.md).
   There is no other deployment path.
3. **Submission gate stays shut** — the module's founder-release gate
   (`LiveGate`) and its absent execution path are design properties.
   Releasing or building an execution path is not part of this
   package or its engagement scope and would require a separate
   founder decision outside this packaging work.

**External dependencies:**

- A buyer/counterparty. State remains **EXTERNAL**; without a
  counterparty there is nothing to deploy to and no one to deploy for.
- Where the counterparty elects a protected RPC/stream, that
  third-party service is procured by/for the counterparty and passed
  through at cost ($0–$300 as listed). The package does not bundle,
  resell, or configure it internally.
- The counterparty's own environment (order flow, Sepolia access,
  operators for the drill and acceptance review).

**Package status, in one line:** packaged and test-green as a
standalone extraction on a local branch; state EXTERNAL; awaiting a
founder merge tap and, separately, a signed counterparty engagement
before anything further happens.
