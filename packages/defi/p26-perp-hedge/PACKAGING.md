# PACKAGING — P26 Perp DEX Hedging Swarm (suite name P06)

PACK-5 handoff record for the PACK-1→PACK-5 fulfillment sequence.
Package directory: `packages/defi/p26-perp-hedge/`
Base commit: `5d20c8fa` (branch `xioix/pack-p26-perp-hedge`)
Catalogue state: EXTERNAL (buyer/counterparty). Catalogue description,
kept exact: "Perpetuals hedging-swarm module. Exists as a platform
module; no hedging service is running for outside accounts."

Naming note: the catalogue calls this module P26 and its suite name
is P06 (`ProtocolSpec(6, "P06_PERPS", "Perp DEX Hedging Swarm", ...)`
in `src/sincor2/defi/catalog.py`; SKU `SINCOR-DEFI-P06-PERPS` in the
module docstring). The unrelated directory `src/sincor2/defi/p26/`
(a self-improving DeFi OS meta layer, 8 files) is a different module
and was not touched or included.

## What existed (at the base commit, unmodified tree)

| Item | Path | Size | Notes |
|---|---|---|---|
| Source module | `src/sincor2/defi/perp_hedge_swarm.py` | 20,985 bytes, 496 lines | Python reference simulation: sizing, DeltaBand, FundingMonitor, LiqBuffer, LiveGate, PriceFeed, HedgeEngine. Untouched by this packaging. |
| Existing tests | `tests/pytest/test_p06_perp_units.py` | 14,870 bytes, 387 lines | 28 tests. **Baseline on the unmodified tree: 28 passed** (venv `~/.venvs/sincor2`, `PYTHONPATH=src:.` plus the CI test env). |
| Platform dependency | `src/sincor2/defi/price_oracle.py` | 29,726 bytes, 743 lines | Only `wiring_for` was used by the module (see stub record). Its own tests (`tests/pytest/test_defi_price_oracle.py`, which also references `P06_PERPS`) stay with the platform and were not adapted. |
| Catalogue entries | `src/sincor2/defi/catalog.py`, `products.py`, `gates.py` | — | `P06_PERPS` spec / product / gate references. Read for verification; not copied, not edited. |

## Dependency inventory (PACK-1)

Every import of the extracted module, classified:

- **Standard library only (runtime):** `__future__.annotations`,
  `hashlib`, `logging`, `os`, `time`, `collections.defaultdict`,
  `dataclasses` (`asdict`, `dataclass`, `field`), `typing`
  (`Any`, `Dict`, `List`, `Optional`, `Tuple`).
- **Third-party (runtime):** none. Nothing to pin; `pyproject.toml`
  declares `dependencies = []`.
- **Third-party (test only):** `pytest>=8` (declared in the `test`
  extra). The adapted tests also use stdlib `random` and `time`; the
  added tests use stdlib `os`, `subprocess`, `sys`, `pathlib`.
- **Platform-internal:** exactly one reach-in — a lazy
  `from .price_oracle import wiring_for` inside `price_feed_for()`.
  Handled by vendoring, below. No other platform import exists in
  the module (verified by AST scan of all imports).

### Stub / vendor record (exact)

- `src/perp_hedge_swarm/price_oracle_stub.py` (new, packaging-written)
  vendors the minimal surface the module uses: `wiring_for`,
  `ProductPriceFeed`, `OracleError`, and
  `PRODUCT_PRICE_NEEDS = {"P06_PERPS": ["ETH/USD", "BTC/USD"]}`,
  matching the platform semantics (`price()` refuses undeclared
  assets, delegates to `oracle.get_price(asset, now, strict=...)`,
  returns `.price_fp`; unknown product raises `KeyError`).
- The extracted module's single import line was changed to
  `from .price_oracle_stub import wiring_for`, and the comment block
  above `price_feed_for` was rewritten to say so, and that
  function's docstring was reworded to match. **Those are the only
  changes to the extracted module** (verified by diff against the
  platform original: the wiring section's comment block, docstring,
  and import line — no logic changes anywhere).
- Not vendored: the platform oracle itself, its feed adapters, its
  transport code, and every other platform module. An engagement
  supplies a real oracle object in the stub's documented shape.

## What was packaged (per PACK step)

- **PACK-1 Extract:** `src/perp_hedge_swarm/perp_hedge_swarm.py`
  (extracted module), `src/perp_hedge_swarm/price_oracle_stub.py`
  (vendored surface), `src/perp_hedge_swarm/pricing.py`
  (packaging-added engagement pricing constants / upkeep arithmetic
  — not part of the original module, labelled as such in its
  docstring), `src/perp_hedge_swarm/__init__.py` (public re-exports),
  `pyproject.toml` (standalone, src layout, zero runtime deps),
  `README.md`.
- **PACK-2 Test:** baseline 28 tests recorded above on the
  unmodified tree. `tests/test_p06_perp_units.py` is the platform
  suite adapted by import path only (test bodies unchanged;
  repo-root sys.path shim replaced by `tests/conftest.py`, which
  puts only this package's `src/` on the path).
  `tests/test_packaging.py` adds 18 tests where coverage was missing:
  a packaging smoke test (isolated subprocess import with only the
  package's declared dependencies; asserts no `sincor2` module is
  loaded), hedge sizing with custom headroom, delta-band exact
  boundaries, funding with no data, liquidation-buffer invalid
  input, multi-position swarm coordination (unique ids, proportional
  sizing, guardian pause blocking new opens but not check-ins, fees
  accumulating across unwinds, one unwind leaving a peer position
  open), vendored-oracle wiring (declared assets only, unknown
  product, `sync_from_oracle`), and upkeep accounting at the
  15%–25% of build cost/yr service form (median $17,500 build →
  $2,625–$4,375/yr). **Final package suite: 46 passed** (28 adapted +
  18 added), run standalone against the package alone.
- **PACK-3 Deploy package:** `DEPLOY.md` — full config surface
  (both env vars the module reads, `TREASURY_ADDRESS` and
  `PERP_DRY_RUN`, with defaults, plus constructor/in-code
  configuration), the dependency list, and numbered Sepolia drill
  instructions for inside a signed engagement. `deploy/DEPLOY_NOTES.md`
  marks the deploy area (no scripts, keys, or artifacts).
  No deployment was performed; the drill is instructions only.
- **PACK-4 Engagement sheet:** `ENGAGEMENT.md` — scope template and
  the priced terms exactly as catalogued: $17,500 one-time
  engagement; secondary upkeep service form at 15%–25% of build
  cost/yr ($2,625–$4,375/yr on the $17,500 build); payment-rail form
  is a scoped integration/deployment engagement, per counterparty.
  It states plainly that no hedging service is running for outside
  accounts today, that a buyer/counterparty is required, and that
  deployment happens only inside a signed engagement. It contains
  no performance promises and no results, invented or otherwise.
- **PACK-5 Handoff:** this file.

## What remains before deploy

### Founder gates (founder action required; packaging cannot clear these)

1. **Merge tap for the extracted package** — this branch
   (`xioix/pack-p26-perp-hedge`) is committed locally only; merging
   it is the founder's tap.
2. **Any deployment only inside a signed engagement** — no
   deployment, drill execution against any network, or conversion
   proof presentation (`LiveGate.present_conversion_proof`) happens
   without a signed counterparty engagement and the founder's go
   for that engagement.

### External dependencies (outside this package)

1. **A counterparty** — a buyer for the scoped engagement in
   `ENGAGEMENT.md`. None exists as part of this packaging.
2. **Venue access under the engagement** — venue accounts, a
   counterparty-controlled wallet and keys, Sepolia RPC/funding for
   the drill, and a real price-oracle adapter in the stub's
   documented shape for `ETH/USD` and `BTC/USD`. None of these are
   in the package.

## Status

State stays EXTERNAL. This package is an extracted reference build
awaiting the founder gates and external dependencies above; nothing
in it runs a hedging service for outside accounts.
