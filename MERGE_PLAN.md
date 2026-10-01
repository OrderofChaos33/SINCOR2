# Merge Reconciliation Plan — SINCOR Complete Build-Out (Wave 29)

**Date:** 2026-09-30 · **Base for all branches:** `fd96801`
**Scope:** 25 push-ready branches listed in `driver-state.json` (`push_ready` array).
Note: the task brief said 26; the driver-state `push_ready` list contains exactly 25
branches. Waves 25 (fork-sim), 27 (stake/slash design), 28 (audit artifacts) are still
`in_flight` and are NOT in this plan.

**Rules for the merge pass (human-gated, per standing directive):**
- Nothing here has been merged, rebased, or pushed. This plan only describes the order
  and the resolutions.
- Merge sequentially in the order below, running the neighbor test suites after each
  merge (each wave's report lists its suites).
- The ledger file `data/defi_product_arm/proof_ledger.json` must be merged by
  **entry-ID concatenation, never text-merge** (see §5).
- No deployment, broadcast, money movement, or production-auth change happens at merge
  time — but §6 lists the env/config changes that must be staged in Railway *before*
  the merged result is ever deployed.

---

## 1. Branch inventory

| # | Branch | Wave | Commits on fd96801 | Files touched (src) | Flags |
|---|--------|------|--------------------|---------------------|-------|
| 1 | `xioix/p1-housekeeping-stale-strings` | w01 | `811404c` | a2a_integration.py, agent_billing.py | — |
| 2 | `xioix/p1-fabricated-claims` | w02 | `11ab5d5` | 10 files (templates, outreach) | copy |
| 3 | `xioix/p1-mislabeled-files` | w04 | `6f012f1` | contract_net.py, order_fulfillment.py, marketplace/contract_net/engine.py | — |
| 4 | `xioix/p1-burn-stats-retire` | w03 | `0255ae9` | agent_billing.py, mvp_blueprints/billing.py | — |
| 5 | `xioix/buildout-05-p24-bridge` | w05 | `fbf9a6d` | NEW defi/p24/bridge.py + test | P2 |
| 6 | `xioix/buildout-08-issuance-route` | w08 | `277a102` | a2a_inbound_market.py, a2a_rate_limits.py, a2a_sdk.py | P2; adds `issuance` rate tier |
| 7 | `xioix/buildout-11-issuance-skill` | w11 | `d23e2eb` | a2a_integration.py, NEW defi/p24/skill.py | P2 |
| 8 | `xioix/buildout-12-p24-wiring-tests` | w12 | `4f87f06` | **onchain/src/p24/CreatorTokenFactory.sol** (ZeroCreator guard) + tests | CONTRACT CHANGE — Sepolia redeploy note |
| 9 | `xioix/buildout-07-registration-proof` | w07 | `5e73fe2` | a2a_inbound.py, a2a_inbound_ext.py, a2a_sdk.py | — |
| 10 | `xioix/buildout-09-heartbeat-auth` | w09 | `65da954` | a2a_inbound_ext.py, a2a_sdk.py, liveness/runner.py, many tests | **DEPLOY: AGENT_HEARTBEAT_TOKEN** |
| 11 | `xioix/buildout-06-caller-ownership` | w06 | `0d48f37` | a2a_inbound_market.py, a2a_integration.py, a2a_sdk.py | security |
| 12 | `xioix/buildout-13-payment-amounts` | w13 | `d300450` | a2a_integration.py, payment_verifier.py | money path |
| 13 | `xioix/buildout-10-settlement-proofs` | w10 | `df6367a` | a2a_integration.py, payment_verifier.py, NEW settlement_proofs.py, onchain/stake_ledger.py | money path |
| 14 | `xioix/buildout-14-write-idempotency` | w14 | `ee4ed10` | NEW a2a_idempotency.py, a2a_inbound_market.py, a2a_integration.py | money path |
| 15 | `xioix/buildout-18-caller-quotas` | w18 | `37cad5e` | a2a_integration.py, a2a_sdk.py | security |
| 16 | `xioix/buildout-24-reputation-identity` | w24 | `b215d18` | a2a_integration.py, a2a_inbound_market.py | **DEDUP with w18 at merge** |
| 17 | `xioix/buildout-17-error-envelopes-admin` | w17 | `90f4af8` | NEW a2a_errors.py; bounty_pool, inbound_ext, inbound_market, a2a_integration, recovery, sponsored_stake | **DEPLOY: ADMIN_PASSWORD replaces SINCOR_BOUNTY_POOL_ADMIN_KEY** |
| 18 | `xioix/buildout-16-rate-limits-sse` | w16 | `f93fd8c` | a2a_rate_limits.py (rewrite), a2a_inbound_market.py, wardrobe.py | **must re-add w08 `issuance` tier** |
| 19 | `xioix/buildout-15-durable-state` | w15 | `fdfb33e` | NEW a2a_shared_state.py; a2a_rate_limits.py (3 hunks), mvp_app.py, .env.example | **DEPLOY: A2A_STATE_STORE / REDIS_URL** |
| 20 | `xioix/buildout-20-defi-invariant-ledger` | w20 | `de8c66b` | proof_ledger.json (+19 entries), NEW record_invariant_evidence.py | LEDGER — JSON merge |
| 21 | `xioix/buildout-21-defi-fuzz-suites` | w21 | `fb7863d`, `7fa39b8` | proof_ledger.json (+7), 7 new fuzz suites | LEDGER — JSON merge |
| 22 | `xioix/buildout-26-ledger-hygiene` | w26 | `8e00a4a` | proof_ledger.json (3 superseded + notes), defi/gates.py, NEW docs/DEFI_LEDGER_SKU_CANON.md | LEDGER — JSON merge, merge LAST of the three |
| 23 | `xioix/buildout-19-axm-fee-listener` | w19 | `cff2e2d` | NEW onchain/fee_event_listener.py; treasury_policy.py (fail-closed), fee_conversion_executor.py, sadas_orchestrator.py | money path; executor stays disarmed |
| 24 | `xioix/buildout-22-money-path-copy` | w22 | `b7361f5` | templates/axiom.html, privacy.html, settlement.py, x402_payments.py, billing.py, a2a_inbound.py | copy; merge AFTER w02 |
| 25 | `xioix/buildout-23-p1-cleanup` | w23 | `4391476` | docs index (10 files), CANONICAL_PATHS.md, a2a_inbound*.py, sinc_payment_verifier.py, .gitmodules, foundry.lock | removes DEMO_SECRET |

All 25 branches verified present locally; each has exactly the commit(s) listed above
on top of `fd96801` (w21 has two commits; all others have one).

---

## 2. Recommended merge order

```
w01 → w04 → w03 → w02 → w05 → w08 → w11 → w12 → w07 → w09 → w06 → w13 → w10 →
w14 → w18 → w24 → w17 → w16 → w15 → w20 → w21 → w26 → w19 → w22 → w23
```

Rationale:
- **P1 housekeeping first** (w01, w04, w03, w02): smallest, mostly disjoint. w01 and w03
  both changed `agent_billing.py:52` with a **byte-identical** edit — git auto-resolves.
- **P2 before P3**: w08 must merge **before** w16, because w16's `a2a_rate_limits.py`
  rewrite does not contain w08's `issuance` policy — merging w16 first would silently
  drop the issuance rate tier (§3.3).
- **A2A identity chain**: w07 → w09 (disjoint hunks, same files) → w06 → w18 → w24.
  w18 then w24 back-to-back so the identity-helper dedup (§4.1) happens in one pass.
- **Money-path cluster**: w13 → w10 → w14 (settle/payment/idempotency touch the same
  regions; this order keeps the smaller payment-verifier change first).
- **w17 (error envelopes + admin)** after the A2A core settles — it touches 7 files
  and its test updates assume the new auth behavior.
- **w16 → w15**: w16's rewritten `a2a_rate_limits.py` defines the `RateLimitStore`
  Protocol seam explicitly designed for w15; port w15's 3 enforcer hunks onto w16's
  file and write the thin `SharedState → RateLimitStore` adapter (§4.2). w15's new
  `a2a_shared_state.py` merges cleanly as a new file either way.
- **Ledger last-but-one**: w20 → w21 → w26, JSON-aware (§5). w26 must come after w20/w21
  so its supersede markings land on entries that exist.
- **w19, w22, w23 last**: disjoint from everything except w02→w22 (axiom.html).

---

## 3. Conflict map (hunk-level, base-line ranges)

"Likely conflict" = hunks within 6 lines on the same file (git's context window).
"Disjoint" pairs are expected to merge cleanly. New-file-only branches (w05, w14's
`a2a_idempotency.py`, w15's `a2a_shared_state.py`, w19's listener, w20/w21 recorders,
w23's docs) have no conflicts by construction.

### 3.1 `src/sincor2/a2a_integration.py` — 10 branches, hottest file
- **w18 vs w24 — DEDUP REQUIRED** (not just a conflict): both added near-duplicate
  verified-identity helpers (`_keccak256`, `_recover_quota_signer` /
  `_recover_identity_signer`, canonical quota messages, `_resolve_quota_identity` /
  `_resolve_verified_wallet`) around lines 1428–1560 and 2245–2291. Overlapping hunks:
  `(52,52)`, `(1434±,1428–1437)`, `(2250–2291, 2245–2288)`. Resolution: keep ONE
  `_keccak256`, ONE recover function, w24's `identity_message_for_send/quote`
  (byte-identical to w18's), ONE resolver; keep w18's `QuotaStore` Protocol and
  w24's `_reputation_key` namespacing. w24's report already flags this.
- w10 vs w13 at ~(1707–1715): settlement-proof wiring vs payment-amount reconciliation
  in the settle path. Merge w13 first; hand-merge w10's adjudicator-signature block.
- w10 vs w14 at ~(1959–1963): idempotency wrapper vs settle proof. Merge w14 after w10;
  the idempotent settle path must wrap the proof-verified settle, not the reverse.
- w10 vs w18 and w10 vs w24 at ~(52,52): import-block additions. Trivial — keep both.
- w13 vs w18 at ~(2271–2276) and w13 vs w24 at ~(2597–2598): quota/payment region.
  Merge w13 first, then w18/w24 identity code around it.
- w06 vs w14 at ~(2090–2094), w06 vs w17 at ~(2695–2710), w06 vs w18 at ~(2284–2290),
  w06 vs w24 at ~(1547,1572,2284,2605): caller-ownership checks interleaved with
  quota/reputation/envelope code. All resolvable by keeping both blocks in merge order.
- w11 vs w14 at ~(74,74): import additions. Trivial.
- w14 vs w18 at ~(1949–1959): idempotent send vs quota consume. Merge w14 first; the
  quota `try_consume` must happen inside the idempotent execution, not before the
  idempotency check (else a retried request double-charges quota).
- w01, w02, w17 hunks are disjoint from everyone else in this file.

### 3.2 `src/sincor2/a2a_inbound_market.py` — 7 branches
- Conflict cluster at **~(861–875)**: w08 (issuance route mount), w06 (caller ownership),
  w14 (idempotency), w16 (stream auth), w17 (error envelopes, 861–865).
  Merge in plan order (w08 → w06 → w14 → w17 → w16); each hunk is a distinct route
  block, so conflicts are mechanical (adjacent inserts), but verify the pool-admin
  gate (`_require_pool_admin`, w17) and the stream slot cap (w16) both survive.
- w14 vs w23 at ~(29,29): import-line collision. Trivial.
- w24's hunks are disjoint from all others in this file.

### 3.3 `src/sincor2/a2a_rate_limits.py` — 3 branches, structural conflict
- w08 added the `issuance` policy (lines 71/94/260). **w16's rewrite does not contain
  it** (only a docstring mention). If w16 merges over w08, the P24 issuance route
  loses its rate tier silently. Resolution: after merging w16, re-add
  `"issuance": [Window(5, 3600), Window(20, 86400)]` and the
  `"POST /v1/a2a/socialfi/issue": "issuance"` endpoint mapping, plus the
  `policy in ("bid", "issuance")` per-agent keying branch.
- w16 vs w15: w16 rewrote the module (371+/90-); w15's 3 hunks (docstring @43,
  `_ENFORCER` @225–264, `a2a_rate_limit_check` @269–298) target pre-rewrite regions.
  Manual port required → see §4.2. Do NOT take either side wholesale.

### 3.4 `templates/axiom.html` — w02 vs w22
- Heavy hunk overlap (17 overlapping ranges: lines 7–269). w22's report states it
  **imported w02's adversarially reviewed wording**, so w22 supersedes. Merge w02
  first, then w22; where git conflicts, take w22's side and verify the final page
  contains no `80%`/`Uniswap V4`/`live on Base` strings (w22's test does this).

### 3.5 `src/sincor2/a2a_sdk.py` — w07 vs w09 vs w18
- Overlaps at ~(128–134): registration-proof helper (w07), heartbeat helper (w09),
  `quota_signature_payload` (w18). Three small adjacent inserts; keep all three,
  verify no duplicate function names.

### 3.6 `src/sincor2/a2a_inbound_ext.py` — w09 vs w23, w17 vs w23
- w09 vs w23 at ~(1–3): import block. Trivial.
- w17 vs w23 at ~(237–241): w17 registered error handlers in `mount()`; w23 made
  `mount()` idempotent. Keep BOTH: idempotency guard first, handler registration
  inside the first-mount branch (registering handlers twice is harmless, but the
  guard must not skip it on first mount).

### 3.7 `src/sincor2/payment_verifier.py` — w10 vs w13 at ~(291–298)
- Settlement-proof verification vs chain-amount reconciliation. Merge w13 first,
  then w10's adjudicator-signature check around the reconciled amount.

### 3.8 `src/sincor2/agent_billing.py` — w01 vs w03 at (52,52)
- Byte-identical edit → git auto-resolves. No action.

### 3.9 Test files
- `tests/pytest/test_a2a_smoke.py`: w10 vs w06 at ~(190–195); w06 vs w18 at ~(5,5).
  Adjacent test-function edits; keep both.
- `tests/pytest/test_reputation_earned.py`: w07 vs w09 at ~(71–76). Adjacent; keep both.
- `tests/pytest/test_a2a_rate_limit_wiring.py`: w09 vs w16 at ~(177–183). w16
  intentionally rewrote the "unmapped routes" assertion (heartbeat/proofs are now
  mapped) — take w16's side.
- `tests/pytest/test_task_board.py`: w09 (insert @248) vs w17 (@36, @340, @352).
  Offset hunks; expect clean auto-merge, run the suite to confirm.

---

## 4. Required manual dedup / porting work (not mechanical merges)

### 4.1 Identity helpers (w18 + w24) — DEDUP
Single shared helper set in `a2a_integration.py`:
`_keccak256`, `_recover_signer` (one), `identity_message_for_send/quote` (w24's names;
byte-identical to w18's), one `_resolve_verified_identity` (merge w18's
`_resolve_quota_identity` and w24's `_resolve_verified_wallet` — same field layout),
plus w18's `QuotaStore` Protocol and w24's `_reputation_key` namespacing.
One signature must keep serving both quota and reputation (both waves' tests pin this).

### 4.2 Rate-limit store seam (w16 + w15) — PORT, not overwrite
Keep w16's `RateLimitStore` Protocol + `MemoryRateLimitStore` + policy table + identity
tiers + `check_stream_auth`. Port w15's backend selection (`A2A_STATE_STORE`,
`get_shared_state()`) as a `SharedStateRateLimitStore` adapter implementing w16's
Protocol, and make `_get_enforcer()` build `SlidingWindowLimiter(store=adapter)`.
w15's `a2a_shared_state.py` and `IdempotencyStore` merge as-is (new files).
Verify with w15's `test_a2a_shared_state.py` AND w16's `test_wave16_rate_limits_stream.py`.

### 4.3 Mount idempotency + error handlers (w17 + w23)
As noted in §3.6: guard first, handler registration inside first-mount.

---

## 5. Ledger merge procedure (w20 → w21 → w26)

`data/defi_product_arm/proof_ledger.json` is append-only. Text-merge will corrupt it.
Procedure:
1. Merge w20 (adds 19 `invariant_test` entries via `record_invariant_evidence.py`).
2. Merge w21 (adds 7 entries; its two commits are suites + ledger — take both).
3. Merge w26: apply its 3 supersede markings (`ev_11b1b596f771`, `ev_e27e4f64ef46`,
   `ev_0adf75900649` → `status: "superseded"` + `superseded_by`) and its 2 `note`
   entries by **entry-ID lookup**, not by applying the text diff. Take w26's
   `src/sincor2/defi/gates.py` (`_is_superseded`) and `docs/DEFI_LEDGER_SKU_CANON.md`
   as normal file merges.
4. After all three: `python3 -c "import json; json.load(open('data/defi_product_arm/proof_ledger.json'))"`,
   then verify via `gates._passing_entries` that superseded entries are excluded and
   canonical SKUs still pass (w26's report documents the exact assertions).
5. (When waves 27/28 land later, their entries append the same way.)

---

## 6. Deployment-config checklist (stage BEFORE any deploy of the merged tree)

These are config/auth changes the merged code requires at runtime. None takes effect at
merge time, but deploying without them breaks or weakens production:

| Branch | Change | Action required in Railway/host env |
|--------|--------|--------------------------------------|
| w09 | Heartbeats require `AGENT_HEARTBEAT_TOKEN` or registered-wallet proof | Set the **same** token value in Railway AND on the liveness-runner host, or heartbeats 401 |
| w17 | Admin auth unified on `ADMIN_PASSWORD` env + `X-Admin-Key` header; legacy `SINCOR_BOUNTY_POOL_ADMIN_KEY` **retired** | Set `ADMIN_PASSWORD`; remove reliance on the old var or pool-admin routes 503 |
| w15 | `A2A_STATE_STORE` (memory\|sqlite\|redis); unset → sqlite in prod | No action needed for default sqlite; set `REDIS_URL` only if Redis wanted |
| w16 | `AGENT_HEARTBEAT_TOKEN` reused for stream auth (`X-Sincor-Heartbeat` / Bearer) | Covered by w09 row |
| w19 | `SINCOR_FEE_EXECUTOR_ARMED` gates `converted=True` (default **False**, fail-closed) | Leave unset until the item-32 arming ceremony; `FEE_LISTENER_*` vars only needed when running the listener |
| w23 | `DEMO_SECRET` hardcoded fallback **deleted**; `sign_payload` requires explicit secret | Confirm nothing in prod relied on the `"sincor-a2a-demo"` fallback (grep found zero callers) |
| w12 | `CreatorTokenFactory.sol` gained `ZeroCreator` guard | Contract source changed — the Sepolia deploy ceremony must deploy THIS source, not an older artifact |

---

## 7. Secret scan — CLEAN

Scanned every `+` line of all 25 branch diffs for: `sk-…`, `ghp_…`, `github_pat_…`,
`PRIVATE KEY` blocks, 64-hex `0x…` keys, `xox…`, `AKIA…`, `password/secret/api_key/token`
assignments, DB connection strings. **3 hits, all false positives:**
- `w06`: `_SECP256K1_N = 0xFFFFFFFF…4141` — the public secp256k1 curve-order constant.
- `w06`: `"token": "secret-token"` — a dummy value in `test_a2a_caller_ownership.py`
  (push-notification test fixture).
- `w19`: `TOKEN = "0x4c3fb66f…"` — a fake token address constant in
  `test_fee_event_listener.py`, alongside `0xaaaa…`/`0xbbbb…` fixtures.
  (The adjacent `RECIPIENT = 0x09E289…` is the already-public treasury address.)

No private keys, PATs, API keys, or connection strings in any branch diff.

---

## 8. Post-merge verification

After the full sequence, before any PR is opened:
1. Run the wave test suites in merge order (each wave's report lists exact suites;
   total is on the order of ~1,500 tests).
2. `create_app()` boot check (w15/w16/w17 touch app wiring).
3. Re-run the hunk-overlap analysis against the merged tree to confirm no silent drops
   — especially: w08's `issuance` rate tier present, w18+w24 single identity helper,
   w15's sqlite backend actually backing the w16 limiter, w02→w22 axiom copy final.
4. Confirm `git status` clean and `proof_ledger.json` validates.
5. Only then: fresh one-shot PAT → push branches → open human-gated PRs. **Never merge
   autonomously.**

---

## 9. Known residuals carried into the merge (not merge-blockers)

- w13: confirmed payment-tx reuse still possible (no spent-tx ledger) — needs the
  founder's design decision; do not "fix" during merge.
- w18/w24: fresh-wallet Sybil on quota/reputation — product call, documented.
- w06: replay cache still per-process until w15's durable state is wired in (happens
  via §4.2).
- w19: `record_axm_receipt` docstring vs listener wiring — small cleanup, separate wave.
- w12: Foundry `.t.sol` needs `forge-std` vendored for CI (w23 tracked `foundry.lock`).
- Blocked items unchanged: 13 (P24 custody), 31 (19 DeFi live-block decisions),
  37 (price-floor $0.15 vs $1.50), CertiK/Skynet URL evidence.
