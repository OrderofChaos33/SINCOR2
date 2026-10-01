# Production Deploy Checklist — SINCOR complete build-out

**Base:** `fd96801` (2026-09-30) · **Scope:** the 33 push-ready build-out
branches. This checklist consolidates every production-config prerequisite
flagged across the build-out waves, in deploy order. Every item cites its
source wave.

**Standing rule:** every push, merge, deploy, broadcast, and production-env
change is human-gated. Nothing in this checklist authorizes a money movement,
a key handling, or a broadcast — those steps are marked
`[FOUNDER-EXECUTED]` and are performed only by the founder with explicit
authorization at the time.

---

## Phase 0 — Pre-merge checks (before any PR is opened)

From the merge-reconciliation waves (w29 `MERGE_PLAN.md`, w31
`MERGE_PLAN_SUPPLEMENT.md`):

- [ ] Apply the merge order: `w01 → w04 → w03 → w02 → w05 → w08 → w11 → w12 →
      w07 → w09 → w06 → w13 → w10 → w14 → w18 → w24 → w17 → w16 → w15 →
      w20 → w21 → w25 → w26 → w28 → w19 → w22 → w23 → w27 → w30 →
      w31 → w32 → w33` (w31; `xioix/buildout-34-burn-cleanup`,
      `xioix/buildout-35-task-decomp`, `xioix/buildout-36-deploy-checklist`
      merge last, no production code).
- [ ] `proof_ledger.json`: merge by **entry-ID concatenation only**, never
      text-merge — chain order `w20 → w21 → w25 → w26 → w28`; apply w26's 3
      supersede markings by ID lookup; JSON-validate after each append (w29,
      w31).
- [ ] Re-add w08's P24 `issuance` rate tier + endpoint mapping after w16's
      rate-limit rewrite — merging w16 over w08 silently drops it otherwise
      (w29).
- [ ] Deduplicate the near-identical EIP-191 verified-identity helpers from
      w18 (quota), w24 (reputation), and w32 (registration) into one shared
      helper (w24, w32).
- [ ] Take w33's `docs/README.md` over w23's (w33's is a superset) (w33).
- [ ] Post-merge verification: full suite in merge order (~1,500 tests),
      `create_app()` boot check, re-run hunk-overlap analysis against the
      merged tree (issuance tier present, single identity helper, w15 sqlite
      backing the w16 limiter, w02→w22 axiom copy final) (w29).
- [ ] Fresh one-shot PAT → push branches → open human-gated PRs. **Never
      merge autonomously.**

## Phase 1 — P0 security merges (existing open items)

- [ ] PRs #301–#309 merged by founder (open since 2026-09-29; p20-ofac,
      p20-compliance, sku-defi-price-oracle, six-phase test-matrix rewrite).
- [ ] W-8 bidder-loop cap decided (founder policy call: 500 vs 2000) and
      merged (context).
- [ ] Same-auction cross-bidder commitment-mirroring fix: approved or
      rejected by founder (context).

## Phase 2 — Railway / host environment variables

Every required variable, its source, and what breaks without it.

| Variable | Source | If missing / wrong |
|---|---|---|
| `SECRET_KEY`, `JWT_SECRET_KEY` (32-char) | pre-existing (CI) | app auth broken; issue #243 close-out needs verification |
| `FLASK_ENV` / `ENVIRONMENT` | pre-existing | test isolation (platform_bootstrap temp registry) |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | pre-existing + w17 | **w17 retired `SINCOR_BOUNTY_POOL_ADMIN_KEY`** — pool-admin routes 503 without `ADMIN_PASSWORD`; remove reliance on the old var |
| `AGENT_HEARTBEAT_TOKEN` | w09, w16 | heartbeats **and** SSE streams 401 — must be the **same** value in Railway AND on the liveness-runner host |
| `SINCOR_ADJUDICATOR_ID` | pre-existing (PR #261) | disputes route cannot enforce adjudicator-only slashing |
| `A2A_STATE_STORE` (`memory\|sqlite\|redis`), unset → sqlite in prod | w15 | default sqlite is fine; set `REDIS_URL` only if Redis wanted |
| `A2A_STATE_SQLITE_PATH` | w15 | defaults under `SINCOR_DATA_DIR` |
| `SINCOR_DATA_DIR` | pre-existing (w19 cursor) | fee-listener cursor + shared-state sqlite need a durable dir; on Railway use `/data` |
| `SINCOR_FEE_EXECUTOR_ARMED` | w19 | **leave unset/`false`** — fail-closed; `true` only in the item-32 arming ceremony |
| `FEE_LISTENER_*` (cursor path, rpc url, token, recipient, confirmations, poll secs, min wei, fee bps=500, backfill blocks, start block) | w19 | needed only when running the fee-event listener; `FEE_LISTENER_FEE_BPS` must stay 500 (locked 5% policy — the code refuses anything else) |
| `SINCOR_REGISTRATION_PROOF_REQUIRED=1` | w32 | recommended production posture; off by default — unsigned registrations stay `unverified` instead of being rejected |
| `STAKE_SLASH_ADDRESS`, `STAKE_RPC_URL`, `STAKE_CHAIN_ID=84532` | w27 | set only after the stake/slash deploy ceremony; `dry_run_onchain_deposit` must be green before any real deposit |
| `BASE_RPC_URL` (+ `BASE_RPC_URLS`, `BASE_RPC_TIMEOUT`) | pre-existing | chain reads for bridges/quotas |
| `STRIPE_SECRET_KEY` | pre-existing | billing endpoints |
| `CELERY_BROKER_URL` | pre-existing | background tasks |
| `BILLING_FORWARDER_PRIVATE_KEY` | pre-existing | **secret** — must be in Secure Vault, never committed or pasted |

Removed/retired by the build-out (verify nothing relies on them):

- [ ] `DEMO_SECRET` hardcoded fallback deleted by w23 — `sign_payload` now
      requires an explicit secret (grep found zero callers; confirm in prod).
- [ ] `SINCOR_BOUNTY_POOL_ADMIN_KEY` retired by w17 — replaced by
      `ADMIN_PASSWORD` via `X-Admin-Key`.

## Phase 3 — Deploy ceremonies (founder-executed, in order)

### 3a. Base Sepolia auction contracts (existing test matrix)

Per `docs/ops/AUCTION_TEST_MATRIX.md` and the six-phase rewrite: deploy
`ExecutionEscrowManager` + auction contracts with the **dedicated Sepolia
adjudicator EOA** (founder-held key, never shared). P20 fail-closed flip
implemented at ceremony time. All 72 matrix tests green before mainnet
consideration. `[FOUNDER-EXECUTED]`

### 3b. P24 creator-token factory (backlog item 8)

- [ ] Deploy `CreatorTokenFactory.sol` **from the w12 source** (gained the
      `ZeroCreator` guard — do not deploy an older artifact) (w12).
- [ ] P24 factory-admin custody decision by founder (backlog item 13).
- [ ] P24 content-policy screener identity/key decision (backlog).
- [ ] Secure Vault/forwarder vs relayer/multisig decision (backlog).
- [ ] Release-or-retain P24 live block (backlog).
- [ ] P24 wiring is deployed dry-run-only until broadcast authorization
      (w05, w08, w11). `[FOUNDER-EXECUTED]` for any broadcast.

### 3c. StakeSlashManager (w27 `docs/ops/STAKE_SLASH_DEPLOY_RUNBOOK.md`)

- [ ] Pin AXM collateral token address (constructor arg, immutable).
- [ ] Choose platform treasury address (senior clawback recipient) and admin
      address (only `setMinStakeWei`).
- [ ] Choose `minStakeWei`; confirm demo adjudicator EOA (founder-held).
- [ ] Fund deployer wallet on Base Sepolia; compile fresh via
      `scripts/gen_stake_slash_abi.py`; 12/12 tests green on exact source.
- [ ] Deploy → record address/tx/args → verify on Basescan + Sourcify.
- [ ] Sepolia smoke matrix (stake, 7-day timelock, signed slash, replay
      revert, rotation) — test token first.
- [ ] Set `STAKE_SLASH_ADDRESS`, `STAKE_RPC_URL`, `STAKE_CHAIN_ID=84532`;
      `dry_run_onchain_deposit` green before any real deposit.
- [ ] Monitor `SlashExecuted` / `AdjudicatorRotated` / `MinStakeUpdated`.
- [ ] Low-S rule: every adjudicator ruling signature must pass through
      `stake_bridge.canonicalize_signature()` — ~50% of naive signatures
      revert otherwise. `[FOUNDER-EXECUTED]`

### 3d. Fee-executor arming (backlog item 32 — founder call, not authorized)

Per `docs/ops/FEE_EXECUTOR_RUNBOOK.md` (w19): dedicated forwarder EOA (key
in Secure Vault only), pool-key discovery + pinning (never the rogue SINC
V2 pool), fork-simulated calldata, `SINCOR_FEE_EXECUTOR_ARMED=true` only
after all sign-offs. **The listener may run read-only before arming; the
executor must stay disarmed.**

## Phase 4 — Founder decisions still open (block deployment completeness)

From gap-audit + merge plan §9:

- [ ] P24 factory-admin custody (item 13).
- [ ] Live-block release/retain for 19 DeFi products (item 31) — products
      stay `test` stage / `live_blocked=True` until decided.
- [ ] Price-floor coherence: served `$0.15` vs whitepaper `$1.50` (item 37).
- [ ] Current CertiK/Skynet scan URLs for live SINC + AXM (w02's
      fabricated-claims branch must be re-examined once evidence arrives).
- [ ] Confirmed payment-tx reuse prevention design (spent-tx ledger vs
      sender-wallet binding vs combined) — w13 proved reuse is still
      possible; do not "fix" during merge.
- [ ] Fresh-wallet free-quota Sybil gating: KYA/stake or accept (w18, w24,
      w32 residuals).
- [ ] x402 billing default: SINC or AXM (product decision).
- [ ] `README.DRAFT.md` disposition; quarantined `sinc_payment_verifier.py`
      eventual deletion/retention (w23).
- [ ] Production callers self-declaring `0xSIMULATED…` (decision).
- [ ] P19 underwriting `burn_share` fee split (deferred — economics change).

## Phase 5 — Post-deploy smoke checks

- [ ] `GET /__boot` green; `create_app()` boots with merged tree.
- [ ] Liveness pass exit 0 (`liveness/runner.py --once`); heartbeats 200
      (proves `AGENT_HEARTBEAT_TOKEN` match).
- [ ] Pool admin route reachable with `X-Admin-Key` (proves `ADMIN_PASSWORD`).
- [ ] Fee-listener cursor advancing (if listener enabled); executor still
      reports `converted=False` with reason until armed.
- [ ] Basescan watch: first realized platform-fee ledger entries (standing
      monitoring rule); zero realized fees = failure signal.
- [ ] `/data` volume mounted on Railway (deploy log line "Mounting volume
      on:") — otherwise redeploys wipe pool/task ledgers.

## Rollback notes

- Every build-out branch is independent from `fd96801`; rollback = revert to
  the pre-merge main. The merge is human-gated PRs — no autonomous merge
  means rollback is a normal PR revert.
- W-8, P0 fixes, and contract deploys are onchain — they cannot be rolled
  back by reverting code; the Sepolia ceremonies are the rehearsal for
  exactly this reason.
- Env-var mistakes are the most common breakage: heartbeats 401 =
  `AGENT_HEARTBEAT_TOKEN` mismatch; pool-admin 503 = `ADMIN_PASSWORD`
  missing; SSE failures = same token row.
- Onchain deploys are append-only history: a bad deploy is replaced by a
  new deploy + `setAdjudicator`/`rotateAdjudicator` rotation, never by
  editing the old contract.

## Residuals carried (not deploy-blockers)

- w13: payment-tx reuse possible — awaiting founder design decision.
- w18/w24/w32: fresh-wallet Sybil — product call.
- w06: replay cache per-process until w15 durable state wired at merge
      (merge plan §4.2).
- w19: `record_axm_receipt` docstring vs listener wiring — cleanup wave.
- w12: Foundry `.t.sol` needs `forge-std` vendored for CI (w23 tracked
      `foundry.lock`).
- w20: P01-VAULT invariant entry lives on w20's branch; arrives at merge.
- w25 fork-sim notes: treasury USDC effectively empty (733 wei); treasury
      `0x09E2…9612Ac` is a contract, not an EOA; AXM supply 1,000,000,000
      confirmed, treasury holds 97.87%.
