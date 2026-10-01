# Merge-Plan Corrections Ledger

**Wave:** 42 · **Branch:** `xioix/buildout-42-merge-corrections` · **Base:** `fd96801`
**Purpose:** every known correction to `MERGE_PLAN.md` (w29, `xioix/buildout-29-merge-recon`)
and `MERGE_PLAN_SUPPLEMENT.md` (w31, `xioix/buildout-31-merge-supplement`) discovered by
waves 32–41. The integrator reads this doc **alongside** the merge plan — the plan
branches are not edited. Nothing here authorizes push, merge, deploy, broadcast,
or money movement; all of those remain human-gated.

---

## C1 — Drop wave 23's root `foundry.lock` and `.gitmodules` (wave 41 finding)

**Source:** `docs/ops/UNTRACKED_FILES_RESOLUTION.md` (wave 41, branch
`xioix/buildout-41-untracked-files`), verified against base `fd96801`.

**What the plan says:** `MERGE_PLAN.md` table row 25 lists w23's `.gitmodules`
and `foundry.lock` as files to take; the residual note on w12 says
"w23 tracked `foundry.lock`" for the Foundry `.t.sol` forge-std need.

**Correction:**
- **Keep** base's `onchain/foundry.lock` — the complete canonical lock (6 pinned
  deps: forge-std v1.9.7, openzeppelin-contracts v5.5.0, permit2, uniswap-hooks,
  v4-core, v4-periphery). It already satisfies w12's forge-std need.
- **Drop** w23's root `foundry.lock` — a stale subset duplicate (only 2 deps).
  Do not add it; merging it would introduce a second, drifting lockfile.
- **Drop** w23's root `.gitmodules` — it declares `lib/forge-std` and
  `lib/openzeppelin-contracts` at the repo root, but the Foundry project lives
  in `onchain/` (`onchain/foundry.toml`, remappings to `onchain/lib/...`), and
  w23 committed **no gitlinks** — the file is dangling. If submodules are ever
  actually registered, paths must be `onchain/lib/<name>` with committed
  gitlinks matching `onchain/remappings.txt`.
- Keep everything else from w23's file resolutions (`docs/architecture/MONEY_FLOW.md`,
  both `toa_*.py` scripts, the `README.DRAFT.md` deferral note).

## C2 — `docs/README.md`: take wave 40's version (supersedes wave 33's)

**Sources:** wave 33 merge note (`docs/README.md`, branch
`xioix/buildout-33-docs-index-refresh`); wave 40 merge note (branch
`xioix/buildout-40-docs-index-2`, commit `3f61190`).

**Correction:** at merge time, take wave 40's `docs/README.md` — it
cherry-picks wave 33's refresh and adds rows for the five post-w33 docs
(`REGISTRATION_IDENTITY.md`, `P24_SCREENER_SELECTION.md`,
`PRODUCTION_DEPLOY_CHECKLIST.md`, `PAYMENT_TX_REPLAY_DESIGN.md`,
`TASK_DECOMPOSITION_SUMMARY.md`). It is a strict superset of wave 33's,
which is a strict superset of wave 23's. Do not text-merge the three
versions; take the newest. (Wave 33 already indexed wave 30's
`SHARED_STATE_ADOPTION.md` — no duplicate row exists.)

## C3 — Deduplicate the three EIP-191 identity helpers (waves 18/24/32)

**Sources:** wave 32 merge note (`docs/security/REGISTRATION_IDENTITY.md`);
wave 37 design doc (`docs/security/PAYMENT_TX_REPLAY_DESIGN.md`).

**Correction:** waves 18, 24, and 32 each carry a near-identical EIP-191
sign/recover helper (parallel branches, deliberate). The merge pass must
fold them into **one shared helper** (canonical home: the w24/w32 wallet-identity
module family — pick one location, update all call sites). Wave 37's
payment-tx replay design explicitly references the *deduped* helper for any
future sender-binding work — do not create a fourth copy.

## C4 — Shared-state adoption is a merge-time wiring task (wave 30)

**Source:** `docs/architecture/SHARED_STATE_ADOPTION.md` (wave 30, branch
`xioix/buildout-30-shared-store-adopt`). The adapters are **not** on the w30
branch (avoiding a duplicate-source situation) — they are applied at merge
time against wave 15's canonical `src/sincor2/a2a_shared_state.py`.

**Corrections / mandatory companions:**
- Keep **both halves** of `a2a_rate_limits.py`: wave 15's shared-state wiring
  + wave 16's policies/limits/SSE/wiring. Do not take either side wholesale.
- After merging w16's rewrite, **re-add w08's P24 `issuance` rate tier** —
  merging w16 over w08 silently drops it (first caught by wave 29).
- Wave 16's `SlidingWindowLimiter` must use **wall clock** (`clock=time.time`),
  not `time.monotonic`, on the shared backend — monotonic resets on every boot
  and makes pre-restart hits look fresh forever.
- Every shared-store call site must map `StateStoreUnavailable` to HTTP **503
  fail-closed** with `Retry-After` (this is what wave 15 did on its own branch).
- Scope `clear()` per domain in tests — the global `clear()` wipes rate-limit
  buckets, quota counters, and idempotency keys together.
- Settlement-idempotency residual is **resolved**: wave 14 already decorates
  the settle route with `@idempotent("settle", ...)`; nothing to re-add —
  the merge must keep that decorator.

## C5 — Wave 38 + wave 8 pre-screen: keep both or unify, `register` is authority

**Source:** wave 38 merge note (`docs/architecture/P24_SCREENER_SELECTION.md`,
branch `xioix/buildout-38-screener-interface`).

**Correction:** wave 8's issuance route pre-screens with `policy.require_clean`
directly before calling `agent.register`; wave 38 routes enforcement through
`require_screened` inside `onboarding.register`. At merge time, either keep
both (defense in depth) or route the pre-screen through `require_screened` —
either way `onboarding.register` is the single enforcement authority and cannot
be bypassed. The standing deny-list behavior is preserved by default;
`P24_SCREENER=deferred` halts issuance until a screener is pinned.

## C6 — `proof_ledger.json`: entry-ID concatenation, chain order, JSON-validate

**Sources:** `MERGE_PLAN.md` §-notes; w31 supplement; wave 36 deploy checklist
(Phase 0 pre-merge checks).

**Correction (restated as one rule):** merge the ledger by **entry-ID
concatenation only**, never text-merge. Chain order:
`w20 → w21 → w25 → w26 → w28`. Apply w26's 3 supersede markings by ID lookup
(the entries stay, marked superseded — nothing is deleted). Run
`python3 -c "import json; json.load(open(...))"` after **every** append.

## C7 — Extended merge order: waves 32–42 merge last

**Source:** wave 36 deploy checklist, Phase 0 (`docs/ops/PRODUCTION_DEPLOY_CHECKLIST.md`).

**Correction:** the plan's 25-branch order (w01 → … → w31) is unchanged; append
after w31, in wave-number order: **w32 (first-registration) → w34
(burn-path cleanup, code) → w35 (task decomposition, planning) → w36 (deploy
checklist, docs) → w37 (tx-replay design, docs) → w38 (screener interface,
code) → w40 (docs index #2) → w41 (untracked-files resolution) → w42 (this
corrections ledger)**. None of the docs/planning waves touch production code;
w34 and w38 do and are ordered with the other code waves.

## C8 — Wave 37 Phase A / Phase B gating

**Source:** `docs/security/PAYMENT_TX_REPLAY_DESIGN.md` (wave 37).

**Correction:** Phase A (spent-transaction ledger) is a strict improvement and
is safe to ship independently. **Phase B (sender-wallet binding) must not ship
before** the EIP-191 helper dedup (C3) and the founder-pinned relayer/forwarder
allowlist. The founder decision (A = combined phased [recommended], B = ledger
only, C = binding only) is still open — the merge must not pre-decide it.

## C9 — Retire the old inline `PaymentVerifier` at merge time

**Source:** wave 37 design doc, §"current state".

**Correction:** an older inline `PaymentVerifier` still exists in
`a2a_integration.py` (~line 1600 on base). The merge pass should retire it in
favor of the canonical `payment_verifier.py` module (which wave 13 extended
with `verified_amount_wei` / `_reconcile_axm_paid`). One verifier, one home.

---

## Acceptance

- [x] Every "merge note for parent" section found on wave branches 32–41 is
      folded in above (checked: w30 adoption guide, w32 identity doc, w33/w40
      README notes, w36 checklist Phase 0, w37 design doc, w38 screener doc,
      w41 resolution doc; w34 was code-only with no merge note; w35 is a
      planning deliverable with no merge interaction; w39 is the scratch
      integration branch itself).
- [x] The w41 `.gitmodules`/`foundry.lock` defect is recorded with the exact
      plan lines it corrects (table row 25; w12 residual note).
- [x] Docs only — no production code changed on this branch.
