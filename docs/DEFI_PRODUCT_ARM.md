# SINCOR Speculative DeFi — Product Arm Charter

**Status: UNDER TESTING. Zero live products. Zero mainnet funds.**

All 26 DeFi builds run under this single arm. Each build travels one pipeline —
`spec → build → test → audit → product → catalog` — and no stage may be
skipped, shortened, or bypassed by flag. After a build becomes a working
product, it gets the full catalog treatment: SKU, proof/testing records,
pricing, marketing, compliance scoring. The arm and the pipeline are built
now; the per-product fill comes later as products mature.

## Prime directive

Nothing in this arm touches mainnet funds until the product reaches the
`catalog` lifecycle stage. The Protocol OS `dry_run` default is load-bearing:
`executed` is always `False`, signing stays outside, and no environment flag
may enable broadcasting from this arm. (See `docs/DEFI_26_PROTOCOL_BUILD_STATUS.md`
for the runtime contract.)

Treasury routing follows standing policy: 5% platform fee to treasury,
converted to USDC/WETH before deposit. No burn.

## The pipeline

```
spec ──► build ──► test ──► audit ──► product ──► catalog
 │          │          │          │            │            │
 │ deep     │ code     │ unit     │ invariant  │ audit      │ pricing live
 │ spec on  │ lands    │ tests    │ + fuzz +   │ clean +    │ marketing
 │ file     │          │ pass     │ fork sim   │ not live-  │ approved
 │          │          │          │ evidence   │ blocked    │ score ≥ 70
```

### Gate rules (enforced in `src/sincor2/defi/gates.py`)

| Transition | Requires | Evidence source |
|---|---|---|
| spec → build | Deep spec exists | `docs/DEFI_PROJECTS_COORDINATION.md`, `docs/DEFI_16_DEEP_SPECS.md`, or `~/workspace/sincor2-auction-tasks/specs/pXX-*.md` |
| build → test | Implementation present + unit tests pass | Repo file + `test_run` entry in proof ledger |
| test → audit | Invariant/fuzz tests + fork simulation | `invariant_test` + `fork_sim` ledger entries |
| audit → product | Audit findings remediated; **live-blocked protocols can NEVER pass** | `audit_report` ledger entry; `catalog.py` `live_blocked` flag |
| product → catalog | Pricing live, marketing approved, compliance ≥ 70, proof ledger complete | Registry state, `compliance_score.py`, ledger |

Every refusal returns machine-readable reasons (`check`, `ok`, `reason`, `evidence`).
Anything that cannot be verified from repo evidence is an explicit
manual-checklist item in the refusal — never silently waived.

### Auction-task phase mapping

The 624 auction tasks already carry a `phase` each. `STAGE_MAP` (in `gates.py`)
maps build phases onto lifecycle stages — the mapping lives in arm code, the
task JSON files are untouched:

| Task phase | Lifecycle stage |
|---|---|
| `spec` | `spec` |
| `scaffold`, `core` | `build` |
| `testing` | `test` |
| `audit` | `audit` |
| `docs`, `deploy` | `product` |

Completing a project's task phases for the mapped stages makes the product
*eligible* for promotion; the gate evidence is still required independently.

## Compliance scoring rubric (`src/sincor2/defi/compliance_score.py`)

0–100 from verifiable signals only. Missing evidence scores 0 — never assumed.

| Dimension | Weight | What earns it |
|---|---|---|
| Audit evidence | 25 | `audit_report` ledger entry, 0 open criticals (25); report with open findings (10); none (0) |
| Test evidence | 20 | Best `test_run` pass rate × 20; none (0) |
| Oracle dependency risk | 15 | Oracle-less design (15); oracle with fallback/redundancy (10); oracle without (5); unverified (0) |
| Custody model | 15 | Non-custodial (15); pull payouts (12); custodial stated (8); unverified (0) |
| Fail-closed design | 10 | Fail-closed stated in spec (10); otherwise (0) |
| Regulatory surface | 15 | Formula from catalog category (compliance 15 … rwa/nftfi/social 5) |

Current scores sit in the high-20s to mid-40s. That is correct: the arm is
under testing.

## Proof ledger (`src/sincor2/defi/proof_ledger.py`)

Append-only JSON ledger (`data/defi_product_arm/proof_ledger.json`):
`test_run`, `invariant_test`, `fork_sim`, `audit_report`, `deploy_receipt`,
`note`. Entries are never edited or deleted — corrections are new entries.

## Pricing (`src/sincor2/defi/pricing.py`)

Base fee from catalog `fee_bps`; standard + performance tiers in AXM.
Status `draft` for all 26 until the `product` stage.

## Marketing rule

`marketing_status` starts `not-started` for all 26. **No marketing copy is
written until the `product` stage, and no copy may claim a product is live
until the `catalog` stage.** Copy template (fill only at product stage+):

> **{NAME}** ({SKU}) — {one-line mechanism}. {Fee}: {fee_bps} bps in AXM.
> Status: {UNDER TESTING | LIVE}. Proof: {ledger entry ids}. Compliance
> score: {score}/100. {Risk disclosure}: {top 2 risks from spec}.

Any copy claiming liveness before `catalog` stage is a charter violation.

## Swarm roster

Swarm N builds protocol N (1:1, derived from `catalog.py` — cannot drift).
SKU format: `SINCOR-DEFI-P{nn}-{SLUG}` (see `products.py` `SLUGS`).

## CLI

```
python -m sincor2.defi.products status      # SKU / stage / score / pricing / marketing / proofs
python -m sincor2.defi.products gates <SKU> # what blocks the next promotion
```

## Known honest gaps (not hidden)

- `src/sincor2/defi/engine.py` was referenced by the package `__init__`, the
  defi test suite, and the build-status doc but was never committed; it was
  rebuilt 2026-09-27 to the documented contract on this branch.
- `sinax/` does not exist in the repo; SINAX attestation references in specs
  are a reserved interface, not current code.
- Aave/Balancer/Polymarket/Morpho addresses are unpinned at spec time;
  deploy-phase tasks must pin them.
- `tests/test_yield_aggregator.py` has 3 pre-existing failures on main
  (strategy-table drift: tests expect `shared_liq_vault`).
- P20's fail-closed hardening needs founder ratification (migration call for
  pre-existing integrations).
