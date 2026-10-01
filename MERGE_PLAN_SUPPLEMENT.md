# Merge Plan Supplement — fork-sim + audit branches (Wave 31)

**Date:** 2026-09-30 · **Supplements:** `MERGE_PLAN.md` (wave 29, commit `d9b47d2` on
`xioix/buildout-29-merge-recon`), which read `driver-state.json` before these two
waves were banked. All branches still from base `fd96801`.

## 1. Branches added

| # | Branch | Wave | Commit | Files on fd96801 |
|---|--------|------|--------|------------------|
| 26 | `xioix/buildout-25-fork-sim-harness` | w25 | `094ab10` | NEW `fork_sim/__init__.py`, `fork_sim/run_fork_sims.py`, `fork_sim/last_run.json`; EDITED `tests/pytest/FORK_SIM_NOTES.md`, `tests/pytest/test_fork_sim_harness.py` (new test file); `data/defi_product_arm/proof_ledger.json` (+17 `fork_sim` entries) |
| 27 | `xioix/buildout-28-audit-artifacts` | w28 | `1934790` | NEW `record_audit_reports.py`; `data/defi_product_arm/proof_ledger.json` (+26 `audit_report` entries) |

## 2. Ledger entry-ID chain (full five-branch order)

`MERGE_PLAN.md` §5 covers w20 → w21 → w26. With these two branches the full append
order is:

```
w20 → w21 → w25 → w26 → w28
```

- **w25 fork_sim entries (17, verified on branch `094ab10`; zero pre-existing entries modified):**
  `ev_ca355ae68495` (P01-VAULT), `ev_287a01f2e4b9` (P03-DARKPOOL),
  `ev_354f46a89ebf` (P04-MEV), `ev_a05f5c13ddb8` (P05-MUTUAL),
  `ev_ad2bf440a5a2` (P07-BRIDGE), `ev_29752e58913d` (P08-RWA),
  `ev_db8aa2e25c89` (P09-GOV), `ev_426a6d8e67a7` (P12-TWAMM),
  `ev_80cd38582c82` (P15-LEND), `ev_391a5102fbf3` (P16-DEXAGG),
  `ev_c2fbc3f0e602` (P18-STRUCTURED), `ev_d3f04518d823` (P20-COMPLY),
  `ev_ffc35113cf84` (P21-TREASURY), `ev_dc10717015c3` (P22-STABLEYIELD),
  `ev_c1d155a78be1` (P24-SOCIALFI), `ev_0b982f3a6554` (P25-PORTFOLIO),
  `ev_abf2c49e3440` (P26-DEFIOS).
  All `kind=fork_sim`, `passed: 1, failed: 0`, canonical SKUs.
- **w28 audit_report entries (26, verified on branch `1934790`; zero pre-existing entries modified):**
  `ev_501e58c685c6` (P01), `ev_9aa5822f5b75` (P02), `ev_ce073b805f12` (P03),
  `ev_6b1556f75366` (P04), `ev_aa13c6432e81` (P05), `ev_4a2a71ca4ef6` (P06),
  `ev_96f87c62b1f1` (P07), `ev_47bdbeec1ab7` (P08), `ev_501ba0a20178` (P09),
  `ev_674193c2d098` (P10), `ev_69dc6e0c48885` (P11), `ev_2f673a81c9ab` (P12),
  `ev_44d2b6270829` (P13), `ev_9d648e15d37d` (P14), `ev_669ed4d28045` (P15),
  `ev_17b51ddb603f` (P16), `ev_1c1639f8a4d7` (P17), `ev_4a6454694340` (P18),
  `ev_b3b7fed131c1` (P19), `ev_868ac74d9ec1` (P20), `ev_ef6fc29c76fa` (P21),
  `ev_db5337afe7b8` (P22), `ev_18e511293ecc` (P23), `ev_fbbdabd95914` (P24),
  `ev_09b4baa6bc92` (P25), `ev_521d8811cf00` (P26).
  All `kind=audit_report`, all carry `not_a_third_party_audit: True`, canonical SKUs.

**Why this order:** w26's 3 supersede markings target pre-existing stale entries and
must land after w20/w21; w25 and w28 are pure appends with no modification of any
pre-existing entry (verified: `modified-preexisting: 0` on both branches), so they may
sit anywhere in the chain — placing w25 before w26 and w28 after keeps the
invariant→fuzz→forksim→hygiene→audit logical progression and matches each wave's
cross-references (w28 entries cite wave-21 fuzz findings by product; w25's fork sims
open test→audit for 17 products, which w28's audit gate then consumes).

**Merge procedure:** entry-ID concatenation only — never text-merge the JSON. Validate
with `python3 -c "import json; json.load(open('data/defi_product_arm/proof_ledger.json'))"`
after each branch.

## 3. File overlaps vs the other 25 branches

Scanned every other branch's diff against w25's and w28's touched files:

- **No conflicts.** `fork_sim/`, `record_audit_reports.py` are new paths touched by no
  other branch. `tests/pytest/test_fork_sim_harness.py` is a new file; no name clash
  with w21's seven new `test_pXX_*_fuzz_invariants.py` files.
- `tests/pytest/FORK_SIM_NOTES.md` exists on base `fd96801` and is edited only by w25
  (w25's report notes it supersedes the P01–P03 sketch). No other branch touches it —
  merges cleanly as a normal diff.
- Both branches' ledger entries use the canonical SKU format
  (`SINCOR-DEFI-P{swarm_id:02d}-{SLUG}`) consistent with w26's
  `docs/DEFI_LEDGER_SKU_CANON.md`; w28's entries honestly cite the wave-20/21 suites
  as "green (wave 20/21)" rather than claiming they ran on its base.

## 4. Deployment-config flags — none new

- **w25:** read-only replay harness against public Base RPC
  (`https://mainnet.base.org` default; optional `SINCOR_FORK_RPC_URL` override).
  No secrets, no signing surface (a test pins the banned call patterns), no env
  required. The live test in `test_fork_sim_harness.py` is skipped unless
  `SINCOR_FORK_LIVE=1`.
- **w28:** `record_audit_reports.py` is a one-shot run-evidence recorder; no runtime
  component, no env.

Nothing from w25 or w28 changes `MERGE_PLAN.md` §6 (deployment-config checklist) or §7
(secret scan — the w29 scan covered 25 branches; these two branches contain no keys or
credentials: w25's harness is read-only with a public RPC URL, w28 is ledger JSON).

## 5. Updated merge order (27 branches)

```
w01 → w04 → w03 → w02 → w05 → w08 → w11 → w12 → w07 → w09 → w06 → w13 → w10 →
w14 → w18 → w24 → w17 → w16 → w15 → w20 → w21 → w25 → w26 → w28 → w19 → w22 → w23
```

w19/w22/w23 remain last (disjoint from everything except w02→w22).
