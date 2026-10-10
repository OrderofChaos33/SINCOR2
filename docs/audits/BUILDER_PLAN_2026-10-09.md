# SINCOR2 — Complete Builder Build-Out Plan

> **Purpose:** provide a dependency-ordered, micro-task implementation backlog for the builder. **Planning baseline:** clean upstream `main` at [`e980ada011dee4319ebbfc2cb6ade9b7692cc701`](https://github.com/OrderofChaos33/SINCOR2/commit/e980ada011dee4319ebbfc2cb6ade9b7692cc701). **Evidence basis:** read-only source review and seven completed planning workstreams; no implementation, tests, or deployment have been performed as part of this plan.

## 0. Read this before starting

The previous audit workflow stopped with the recorded status `creditNotEnough`. That describes the job’s execution stop; it does **not** establish whether a plan/paywall/account balance changed or whether credits/tokens were deducted. Account-specific billing or credit questions belong with [Manus Help](https://help.manus.im). The new seven-domain planning workflow completed, with eight subagents and no failed workstreams.

This plan is designed to be comprehensive against the inspected repository surfaces, but **no static review can promise that absolutely nothing was missed**. The first implementation tasks therefore require an inventory of every route, adapter, scheduler, persistence store, external sink, product claim, and deployment path. Anything undiscovered is a blocker to a completeness claim—not permission to assume it is safe.

### Builder mission — copy/paste brief

> Implement only the task IDs in this backlog, in dependency order, against the reviewed SINCOR2 repository. First confirm that upstream and the working tree still match the stated baseline; if the commit changed, stop and reconcile the plan to the new code. Create a feature branch and keep changes scoped to the listed task IDs. For each task, inspect the current callers and tests, make the smallest coherent change, add the listed adversarial tests using synthetic fixtures/fake adapters, run only the relevant safe tests, record exact commands/results, and update a task ledger. Do not read, print, copy, test, or expose real credentials, private keys, production logs, customer data, or wallet secrets. Do not use real provider APIs, real email/social/CRM/CMS actions, public RPC broadcasts, payments, signing, transfers, refunds, custody actions, or live trading. Do not commit/push/deploy or change production flags unless separately requested and authorized. When an owner decision is required, mark that task BLOCKED and record the precise decision; do not invent a default. A missing identity, policy, finality, storage, approval, health signal, source inventory item, test result, or deployment proof is HOLD/FAIL—not a pass. Preserve evidence and opt-outs; rollback disables a capability and never restores the vulnerable path. Do not claim “production-ready,” “shadow-protected,” “settled,” “paid,” “audited,” “compliant,” or “live” without the task’s evidence.

### Required execution discipline

- Work in the phases below. Keep each task ID traceable to commits, tests, migration notes, reviewer, and acceptance evidence. Prefer small PRs grouped by dependency-cohesive task IDs; never merge a later phase to bypass a blocked prerequisite.
- Before each code change, identify the exact production and test call paths. Extend shared interfaces rather than adding a parallel auth, payment, policy, or consent mechanism.
- Any migration must be additive/restartable, rehearsed on synthetic fixtures, reconciled, backed up, and have a documented rollback or forward-fix. Never backfill unknown historical records as verified identity, consent, payment, ownership, or entitlement.
- CI and staging tests must use fakes, mocks, local disposable stores, and network/credential isolation. A passing unit test proves only the tested contract; it does not prove deployment isolation or production behavior.
- At every task boundary, report: task IDs closed/blocked; files changed; tests actually run; static sink inventory delta; migration/rollback status; decisions needed; and what remains unverified.

## 1. Current evidence baseline and limits

Source-anchored comprehensive builder backlog for commit e980ada011dee4319ebbfc2cb6ade9b7692cc701. Source facts below are static observations only; deployment, flags, providers, data, funds, custody, production reachability, and test status are unknown. True overlaps are consolidated as RT-01+ID-05, ID-04+PAY-08, RT-06+OPS-06, OPS-01+ON-07, OPS-02+ON-08, and P1+P6. Failed workstreams: none supplied.

The workstreams observed source-level risks in customer identity/access, payment verification/entitlements, agent execution and shadow-mode bypass, outbound schedulers, contracts/treasury, CI/deployment operations, and product/claim consistency. These are code-path findings, **not proof of production exposure, exploitation, customer loss, active provider credentials, or deployment state**. No tests were run in the audit/planning workflow. For the findings and detailed static-review context, see the [provisional code and business audit](/home/ubuntu/sincor2_business_code_audit_2026-10-09.md).

Repository basis: `OrderofChaos33/SINCOR2`, commit `e980ada011dee4319ebbfc2cb6ade9b7692cc701`. Before work begins, verify current upstream SHA and current branch/worktree; do not apply this backlog blindly to a materially changed codebase.

## 2. Non-negotiable design principles

- Default deny on missing identity, policy, finality, storage, approval, or configuration.
- Authorize only server-derived principal, tenant, owner, resource, payment, and idempotency identifiers.
- Keep payment, task, consent, evidence, approval, and release transitions durable, monotonic, and auditable.
- Use fakes and isolated fixtures; no CI/staging live effects.
- Owner choices are explicit gates, never assumptions.
- Rollback disables capability and preserves evidence, opt-outs, and reconciliation state; it never restores insecure fallbacks.

## 3. Phases and hard exit gates

| Phase | Goal | Task IDs | Exit gates |
|---|---|---|---|
| **0-containment** | Fail closed known permissive routes and disable effects. | `CT-01`, `OB-01`, `ID-01`, `RT-03`, `RT-02`, `ID-06` | No unapproved signing, send, publish, identifier-only login, in-process agent execution, or dispatch fallback.<br>All containment tests use fake handlers/providers/signers and show zero sink calls on denial. |
| **1-architecture-data** | Establish shared identity, persistence, authorization, migration, custody, and claim contracts. | `ID-02`, `ID-03`, `RT-01_ID-05`, `PAY-01`, `OPS-03`, `OPS-04`, `OPS-05`, `RT-04`, `OB-03`, `OB-02`, `ON-06`, `P1_P6` | Selected owner decisions are recorded or affected paths remain disabled.<br>Legacy uncertain rows are unknown/quarantined, never assumed verified, paid, consented, or owned. |
| **2-implementation** | Implement bounded payment, entitlement, evidence, outbound, onchain, privacy, and supervised-pilot controls. | `PAY-02`, `PAY-03`, `PAY-04`, `PAY-06`, `PAY-05`, `PAY-07`, `ID-04_PAY-08`, `PAY-09`, `RT-05`, `RT-06_OPS-06`, `OB-04`, `OB-05`, `OB-06`, `ON-02`, `ON-03`, `ON-04`, `ON-05`, `P2`, `P3`, `P4`, `P5`, `ID-07` | Paid access, dispatch, refunds, campaigns, publication, conversion, payouts, and customer-data changes remain disabled/draft-only absent their task gates.<br>Each attempted effect has tenant context, policy, idempotency, and evidence. |
| **3-verification** | Make migrations, adversarial tests, CI, reliability, and incident response reproducible. | `OPS-01_ON-07`, `OB-07`, `OPS-07`, `OPS-08` | Offline CI is required and fail-closed for affected changes.<br>Migration/restore, incident, alert, and load evidence is produced only with synthetic/disposable environments. |
| **4-staging-release-promotion** | Require deployment evidence, immutable releases, shadow/draft canaries, and measured product gates. | `RT-07`, `OPS-02_ON-08`, `P7` | Missing deployment evidence, unhealthy readiness, unknown sink, or failed isolation test is a hold.<br>This phase authorizes no live effect; any later promotion is a separate owner-approved decision. |

**Important:** Phase 4 is a staging/release-evidence and promotion-gate design. This document does **not** approve a production deployment or any live side effect. Production promotion, public sending/publishing, signing, asset movement, refunds, escrow payouts, treasury conversion, and trading are explicitly outside authorization here.

## 4. Complete micro-task backlog

**Total tasks: 47.** Complete in dependency order; tasks can run in parallel only where dependencies and shared schemas are already agreed. Any unresolved owner decision blocks the affected behavior.

### 1. `CT-01` — Explicit no-live-onchain-effects policy

- **Phase:** `0-containment`
- **Priority:** **P0**
- **Objective:** Replace generic armed flags with a typed, fail-closed release policy for deployment and treasury conversion while preserving read-only plan/quote/dry-run.
- **Dependencies:** None
- **Evidence / files to inspect:**
- src/sincor2/onchain/fee_conversion_executor.py:185-201,597-646; src/sincor2/treasury_policy.py:136-194; onchain/script/07_DeployP24.s.sol:49-77
- **Micro-steps:**
  - Require chain, target/token allowlist, manifest digest, operator approval, expiry, and receipt schema before signer callback; invalid/missing inputs deny.
- **Tests and acceptance criteria:**
  - Fake signer receives zero callbacks for absent/stale/wrong-chain/unapproved/disarmed cases; approved synthetic manifest reaches fake callback; dry-run never signs.
- **Safety / release gate:** No deployment, broadcast, conversion, token movement, payout, custody action, or trading is authorized.
- **Owner decisions / blockers:**
- Approve any live capability, allowlists, limits, approvers, and canonical receipt-interface owner.

### 2. `OB-01` — Default-off outbound schedulers and publishers

- **Phase:** `0-containment`
- **Priority:** **P0**
- **Objective:** Require positive parseable authorization for every send/publish path and separate draft generation from effects.
- **Dependencies:** None
- **Evidence / files to inspect:**
- src/sincor2/scheduler.py:115-130; src/sincor2/outreach_scheduler.py:30-57; src/sincor2/content_agent.py:1030-1038; docs/safety/OWNER_DECISIONS_2026-10-09.md:6-17
- **Micro-steps:**
  - Inventory startup/worker/direct paths; gate registration and invocation; legacy OUTREACH_ENABLED alone cannot enable effects; reconcile conflicting docs/SALES_OUTREACH_SWARM.md:3,15-20.
- **Tests and acceptance criteria:**
  - Unset/malformed/unavailable policy registers no send/publish job and makes zero fake-provider calls; draft-only cannot publish.
- **Safety / release gate:** Email, social, CRM-triggered messages, and WordPress publishing remain disabled.
- **Owner decisions / blockers:**
- Confirm allowed transactional categories and whether any publish channel may be enabled and by whom.

### 3. `ID-01` — Proof-based customer authentication

- **Phase:** `0-containment`
- **Priority:** **P0**
- **Objective:** Require proof of account control for customer session/JWT issuance and throttle all admin credential verification.
- **Dependencies:** None
- **Evidence / files to inspect:**
- src/sincor2/mvp_blueprints/pages.py:124-154; src/sincor2/mvp_app.py:281-313,98-106,809-821; tests/test_mvp.py:146-174
- **Micro-steps:**
  - Define customer/admin/anonymous principal contract; implement selected password or expiring single-use verified challenge; remove identifier-only issuance; revoke old sessions and clear logout state.
- **Tests and acceptance criteria:**
  - Identifier alone never authenticates; valid proof succeeds; invalid/expired/replayed/cross-account proof, fixation, role interchange, and unthrottled admin attempts fail.
- **Safety / release gate:** Disable legacy login before replacement canary; rollback disables login, never restores identifier-only access.
- **Owner decisions / blockers:**
- Select factor/channel, expiry, enumeration, recovery/account-merge, and legacy re-verification policy.

### 4. `RT-03` — Remove planner-controlled execution and broad file reads

- **Phase:** `0-containment`
- **Priority:** **P0**
- **Objective:** Eliminate in-process agent exec/eval and replace paths with bounded tenant-authorized opaque handles.
- **Dependencies:** `RT-01_ID-05`
- **Evidence / files to inspect:**
- src/sincor2/agency_kernel_tools.py:25-29,87-147,187-267; src/sincor2/agency_kernel_runtime.py:74-103; src/sincor2/vertical_dispatch.py:305-327
- **Micro-steps:**
  - Remove python_exec/execution from registry/plans; resolve server-side handles with tenant, traversal/symlink, type/count/byte, and redaction controls; no silent tool substitution.
- **Tests and acceptance criteria:**
  - Crafted plans cannot reach exec; absolute/traversal/symlink/secret-like/cross-tenant/oversize fixtures return no bytes.
- **Safety / release gate:** No sandbox is authorized; any future one needs a separately approved credentialless, networkless disposable service.
- **Owner decisions / blockers:**
- Decide permanent removal versus separate sandbox and approved readable datasets.

### 5. `RT-02` — Fail-closed dispatch and queue errors

- **Phase:** `0-containment`
- **Priority:** **P0**
- **Objective:** Ensure policy/router/broker/worker exceptions cannot invoke raw agent, kernel, or inline dispatch.
- **Dependencies:** `RT-01_ID-05`, `OPS-04`
- **Evidence / files to inspect:**
- src/sincor2/vertical_dispatch.py:219-339; src/sincor2/a2a_integration.py:3463-3476; src/sincor2/task_queue.py:292-325; src/sincor2/async_tasks.py:20-70
- **Micro-steps:**
  - Use RuntimeContext policy decision for every attempt; remove direct fallbacks; broker failure returns retriable unaccepted state; reconcile ambiguous retries by effect key.
- **Tests and acceptance criteria:**
  - Injected policy/router/broker/store failures invoke zero handlers; concurrent/redelivered key has at most one effect-capable start.
- **Safety / release gate:** Effect-capable handlers remain disabled until failure tests pass; rollback pauses ingress/work, not policy.
- **Owner decisions / blockers:**
- Choose policy-outage response, retry window, idempotency retention, and ambiguous-work handling.

### 6. `ID-06` — Redact secrets/PII and secure cookies

- **Phase:** `0-containment`
- **Priority:** **P1**
- **Objective:** Remove credential diagnostics, allowlist identity logs, and drive session/JWT Secure flags from explicit HTTPS/proxy policy.
- **Dependencies:** `OPS-03`
- **Evidence / files to inspect:**
- src/sincor2/auth_system.py:203-228; src/sincor2/mvp_blueprints/auth.py:142-177,271-293; src/sincor2/mvp_blueprints/billing.py:669,702,1077,1115; src/sincor2/mvp_app.py:101-106,809-821
- **Micro-steps:**
  - Delete password/fallback output; use opaque IDs/sanitized codes; exclude tokens, payloads, emails, wallets, reasons; unify cookie transport policy.
- **Tests and acceptance criteria:**
  - Synthetic secret/PII/error sentinels never appear in scoped logs; session and JWT cookies share deterministic production/local security behavior.
- **Safety / release gate:** No actual secret or production-log inspection; raw logging is never a rollback option.
- **Owner decisions / blockers:**
- Approve proxy policy and log retention/access/pseudonymization.

### 7. `ID-02` — Verified OAuth and explicit linking

- **Phase:** `1-architecture-data`
- **Priority:** **P0**
- **Objective:** Authenticate only provider-verified stable subjects and forbid email-equality auto-linking.
- **Dependencies:** `ID-01`
- **Evidence / files to inspect:**
- src/sincor2/mvp_blueprints/auth.py:120-177; src/sincor2/mvp_app.py:348-382
- **Micro-steps:**
  - Require Google verified email+subject and GitHub subject+verified email when needed; unique provider+subject; explicit authenticated/fresh-proof linking with confirmation and sanitized state/nonce errors.
- **Tests and acceptance criteria:**
  - Mocked verified/unverified/missing claims; same-email different subjects cannot link; duplicate subject/cross-user link fails; no raw token logging.
- **Safety / release gate:** Unsupported providers stay disabled; legacy links require proof, never bulk email matching.
- **Owner decisions / blockers:**
- Approve provider allowlist, email-change recovery, and OAuth login versus link-only policy.

### 8. `ID-03` — Verified purpose-specific onboarding consent

- **Phase:** `1-architecture-data`
- **Priority:** **P1**
- **Objective:** Bind profile changes and consent evidence to verified identity/order proof rather than client email/CSRF.
- **Dependencies:** `ID-01`, `ID-02`
- **Evidence / files to inspect:**
- src/sincor2/mvp_blueprints/auth.py:189-285; src/sincor2/mvp_app.py:646-663
- **Micro-steps:**
  - Use verified principal/challenge; store immutable purpose, notice version, decision, actor/proof, time, withdrawal/supersession; make writes transactional/idempotent.
- **Tests and acceptance criteria:**
  - CSRF-only/forged/stale/cross-account/reused proof cannot mutate; affirmative/negative/withdrawal/retry/concurrency evidence is correct.
- **Safety / release gate:** Legacy consent becomes unknown/unverified; optional processing disables if proof/event storage fails.
- **Owner decisions / blockers:**
- Privacy/legal approves purpose, notices, lawful basis, and legacy treatment.

### 9. `RT-01_ID-05` — Tenant-scoped runtime context and API authorization matrix

- **Phase:** `1-architecture-data`
- **Priority:** **P0**
- **Objective:** Unify RT-01 and ID-05 so task/result/evidence/customer/admin APIs use verified role plus server-resolved tenant/owner.
- **Dependencies:** `ID-01`
- **Evidence / files to inspect:**
- src/sincor2/a2a_integration.py:3440-3476; src/sincor2/task_queue.py:247-267,354-377; src/sincor2/mvp_app.py:125-129,770-806; src/sincor2/shadow_monitor/events.py:30-125
- **Micro-steps:**
  - Persist immutable principal/tenant/role/agent/task/trace/purpose/idempotency context through queue/worker; classify APIs; use reusable guards/minimal DTOs; quarantine unowned legacy jobs.
- **Tests and acceptance criteria:**
  - A/B create/get/cancel/list/poll/result/evidence, forged IDs, and missing owner all deny without data; endpoint matrix proves intended guard.
- **Safety / release gate:** Task routes/multi-tenant workers remain closed until durable context and isolation tests pass.
- **Owner decisions / blockers:**
- Select principal-to-tenant mapping, public-status behavior, visible result schema, and legacy-job disposition.

### 10. `PAY-01` — Canonical durable payment state

- **Phase:** `1-architecture-data`
- **Priority:** **P0**
- **Objective:** Create one payment/order/claim/event/refund contract and selected shared transactional store.
- **Dependencies:** None
- **Evidence / files to inspect:**
- src/sincor2/platform_payments.py:115-169; src/sincor2/x402_payments.py:74-110; src/sincor2/stripe_routes.py:242-283; src/sincor2/agent_billing.py:30-60
- **Micro-steps:**
  - Map every store/consumer to payment_id, owner, amount, provider evidence, entitlement/refund; select backend; add restartable migrations, immutable history, legal transitions, and repair procedure.
- **Tests and acceptance criteria:**
  - Empty/current fixture migration is repeatable/preserving; illegal/concurrent transitions fail; every ingress maps to one documented contract.
- **Safety / release gate:** Use shadow/read-only migration until persistence, restore, reconciliation, and multi-process semantics are proven.
- **Owner decisions / blockers:**
- Choose backend/outage behavior, states, retention, and historical JSONL/orders.db treatment.

### 11. `OPS-03` — Production configuration and truthful readiness

- **Phase:** `1-architecture-data`
- **Priority:** **P1**
- **Objective:** Validate a versioned non-secret production config and separate liveness from selected-feature readiness.
- **Dependencies:** None
- **Evidence / files to inspect:**
- src/sincor2/settings.py:85-150; src/sincor2/blueprints/monitoring.py:46-77; src/sincor2/mvp_app.py:585-598,644-686; docker-compose.yml:12-36; railway.toml; Dockerfile; Procfile
- **Micro-steps:**
  - Specify settings/owners; preflight without values; required queue/store/db failure is unready; reconcile dev/deploy declarations and persistent paths.
- **Tests and acceptance criteria:**
  - Hermetic malformed/missing/weak setting tests fail without secret echo; readiness requires bounded backend health, not URL presence.
- **Safety / release gate:** Staging preflight/readiness required before deployment; repo declarations prove no runtime state.
- **Owner decisions / blockers:**
- Select hosting/platform, feature profiles, required dependencies, and readiness contract.

### 12. `OPS-04` — Durable idempotent asynchronous job lifecycle

- **Phase:** `1-architecture-data`
- **Priority:** **P1**
- **Objective:** Replace process-local/mismatched queue state and inline fallback with selected broker/store, leases, retries, and dead-letter recovery.
- **Dependencies:** `OPS-03`, `RT-01_ID-05`, `RT-02`
- **Evidence / files to inspect:**
- src/sincor2/task_queue.py:8-10,46-88,192-209,341-375; src/sincor2/async_tasks.py:34-42; docs/ASYNC_TASK_QUEUE.md:31-45
- **Micro-steps:**
  - Reject local/eager/mismatched production backends; persist accepted/running/retryable/failed/poison/completed, keys, attempts, leases, errors, and results; export actual health.
- **Tests and acceptance criteria:**
  - Fake broker/store restart, duplicate delivery, crash/lease/retry/poison/replay tests show zero lost accepted jobs and at most one committed effect.
- **Safety / release gate:** No effect queue/workers until durable recovery/idempotency and health alerts pass.
- **Owner decisions / blockers:**
- Choose broker/store, delivery semantics, retention, retry ceilings, and permitted synchronous classes.

### 13. `OPS-05` — Version migrations and prove restore

- **Phase:** `1-architecture-data`
- **Priority:** **P1**
- **Objective:** Replace startup DDL as evolution path with compatible migrations and tested off-host recovery.
- **Dependencies:** `OPS-03`, `OPS-04`, `PAY-01`
- **Evidence / files to inspect:**
- src/sincor2/mvp_app.py:644-686; docker-compose.yml:16-17,25-26,75-96; tests/pytest/test_recovery_track.py
- **Micro-steps:**
  - Inventory state/writers; add expand-contract versions, preflight, backup-before-change, resume, rollback/forward-fix; define encrypted backup, RPO/RTO, restore/reconcile/resume.
- **Tests and acceptance criteria:**
  - Disposable fresh/prior/interrupted migration and encrypted isolated restore preserve representative rows/links and record RPO/RTO.
- **Safety / release gate:** No production schema change absent reviewed migration, pre-change backup, and staging restore evidence.
- **Owner decisions / blockers:**
- Select database, backup provider/access/retention, rollback policy, RPO, and RTO.

### 14. `RT-04` — Typed effect gateway and sink manifest

- **Phase:** `1-architecture-data`
- **Priority:** **P0**
- **Objective:** Put every identified agent-facing network/write sink behind RuntimeContext, EffectIntent, policy, adapter, and proposal-only receipt.
- **Dependencies:** `RT-01_ID-05`, `RT-02`, `RT-03`
- **Evidence / files to inspect:**
- src/sincor2/mvp_app.py:62-65; src/sincor2/shadow_monitor/effect_boundary.py:194-278; src/sincor2/agency_kernel_tools.py:19-55; src/sincor2/content_agent.py:710-759,1030-1045
- **Micro-steps:**
  - Maintain source manifest; require tenant/trace/key/payload/policy; shadow uses LogOnlyAdapter; separate drafts; CI blocks unmanifested network/write/process sinks.
- **Tests and acceptance criteria:**
  - Every manifest fake emits executed=false and zero sink attempt; missing context/policy/type or duplicate key denies; scoped inventory coverage is 100%.
- **Safety / release gate:** No live app-facing adapter is enabled; stage remains default-deny proposal-only.
- **Owner decisions / blockers:**
- Approve effect taxonomy, risk tiers, egress/data allowlists, and repository-wide process-sink scan result.

### 15. `OB-03` — Lead provenance, eligibility, and global suppression

- **Phase:** `1-architecture-data`
- **Priority:** **P0**
- **Objective:** Make lead use auditable, disallow guessed/discovered-address marketing by default, and propagate opt-outs globally.
- **Dependencies:** `OB-01`, `OPS-05`
- **Evidence / files to inspect:**
- src/sincor2/outreach_engine.py:22-25,155-229; src/sincor2/webbuilder_crm.py:8-18,56-128; docs/safety/OWNER_DECISIONS_2026-10-09.md:6-17
- **Micro-steps:**
  - Store source/record/time/collector/use basis/observed-inferred/retention; purpose proof; canonical suppression; legacy contacts migrate unknown/ineligible.
- **Tests and acceptance criteria:**
  - Legacy rows never auto-opt-in; missing provenance, guessed, unknown/withdrawn/expired, suppressed, duplicate opt-out, or tenant mismatch denies.
- **Safety / release gate:** No lead outreach/sourcing enablement until source-use, retention, and suppression decisions are recorded.
- **Owner decisions / blockers:**
- Legal/owner approves source matrix, retention, suppression matching, and disputed opt-outs.

### 16. `OB-02` — Typed email policy boundary

- **Phase:** `1-architecture-data`
- **Priority:** **P0**
- **Objective:** Classify every email intent and enforce purpose, eligibility, suppression, policy, idempotency, and receipt before provider selection.
- **Dependencies:** `OB-01`, `OB-03`, `RT-04`
- **Evidence / files to inspect:**
- src/sincor2/email_sender.py:39-72,200-248; src/sincor2/outreach_engine.py:201-229; src/sincor2/webbuilder_crm.py:138-159; src/sincor2/launch_review_notify.py:112-120
- **Micro-steps:**
  - Define EmailIntent; route every sender through it; deny unclassified/marketing/missing-proof/missing-policy/suppressed/duplicate inputs; retain redacted durable decision receipt.
- **Tests and acceptance criteria:**
  - Inventory covers all callsites; denied intents make zero fake calls; approved transactional fake path calls once with redacted receipt.
- **Safety / release gate:** Production email remains transactional-only until later campaign and release gates pass.
- **Owner decisions / blockers:**
- Approve exhaustive transactional purposes/senders and any suppression exception; absent approval denies.

### 17. `ON-06` — Separate privileged onchain roles and custody

- **Phase:** `1-architecture-data`
- **Priority:** **P1**
- **Objective:** Enforce role separation and signer isolation across deploy, admin, screener, settlement, guardian, treasury, and forwarder.
- **Dependencies:** `CT-01`
- **Evidence / files to inspect:**
- onchain/script/07_DeployP24.s.sol:49-67; onchain/deployments/base-8453.json:4-7; onchain/deployments/base-sepolia-p24.json:2-20; docs/ops/FEE_EXECUTOR_RUNBOOK.md:12-20
- **Micro-steps:**
  - Write role/quorum/cap/recovery matrix; reject zero/aliased roles; use HSM/Vault/Safe authorization with unsigned construction only; define rotation/revocation/retirement.
- **Tests and acceptance criteria:**
  - Synthetic-key tests prove role separation, approved-address validation, and private-key non-exposure; fake broadcasts retain approval/digest/chain/role receipt.
- **Safety / release gate:** No key inspection, signing, deployment, or custody use occurs.
- **Owner decisions / blockers:**
- Approve provider, multisig, owners, limits, guardian, and existing Base role-attestation review.

### 18. `P1_P6` — Versioned product, pricing, and claim evidence ledger

- **Phase:** `1-architecture-data`
- **Priority:** **P0**
- **Objective:** Consolidate catalog and claim controls so SKU status/price/unit/limits and public capability/compliance/outcome claims do not conflict.
- **Dependencies:** None
- **Evidence / files to inspect:**
- data/sku_catalog.json:1-60; docs/WEBSITE_PRICING_COPY.md:7-26; config/x402_pricing.yaml:1-35; src/sincor2/obs_skus/registry.py:27-76,124-163; PRODUCTS/OBS-01/COMPLIANCE.md:3-23
- **Micro-steps:**
  - Create stable SKU/claim matrix; reconcile registries/checkout/collateral; attach appropriate code, deployment, customer, or qualified evidence with owner/expiry; preserve historical IDs/snapshot.
- **Tests and acceptance criteria:**
  - All public SKU fields agree; every claim has owner/current evidence/allowed wording; copy tests block unsupported 24/7, zero-human, certification, guarantee, and regulated claims.
- **Safety / release gate:** Unsupported/conflicting SKU or claim is unpublished/unquoted; copy approval does not override technical gates.
- **Owner decisions / blockers:**
- Product resolves catalog conflicts; qualified review approves regulated wording.

### 19. `PAY-02` — Attributable A2A chain-transfer verification

- **Phase:** `2-implementation`
- **Priority:** **P0**
- **Objective:** Remove synthetic verifier success and verify typed chain transfer evidence against server quote/config before paid task creation.
- **Dependencies:** `PAY-01`, `RT-02`
- **Evidence / files to inspect:**
- src/sincor2/payment_verifier.py:256-267; src/sincor2/a2a_bootstrap.py:28-42; src/sincor2/a2a_integration.py:3394-3425; tests/pytest/test_payment_amount_reconciliation.py:83-131,246-283
- **Micro-steps:**
  - Production verifier returns chain/tx/receipt/token/sender/recipient/amount/log/block evidence; reject malformed/reverted/wrong/ambiguous/unavailable input and never trust caller axmPaidWei.
- **Tests and acceptance criteria:**
  - Mock receipts reject synthetic/malformed/wrong chain/token/destination/amount/reverted/RPC failure without enqueue; amount is qualifying log plus server quote.
- **Safety / release gate:** Paid A2A disabled until finality/claims and owner sender-relayer policy pass.
- **Owner decisions / blockers:**
- Approve wallet binding/relayer model, chains, tokens, treasury, price, and overpayment policy.

### 20. `PAY-03` — Finality and reorg-aware settlement

- **Phase:** `2-implementation`
- **Priority:** **P0**
- **Objective:** Use pending-finality states and deterministic reversal/review handling across A2A, checkout, and x402.
- **Dependencies:** `PAY-01`, `PAY-02`
- **Evidence / files to inspect:**
- src/sincor2/platform_payments.py:592-641,781-856; src/sincor2/x402_payments.py:301-358; docs/security/PAYMENT_TX_REPLAY_DESIGN.md:75-76,136-140
- **Micro-steps:**
  - Record block/hash/confirmation; require finality before claim/entitlement/dispatch; recheck for reorgs, reverse/review, revoke eligible access, and alert.
- **Tests and acceptance criteria:**
  - Fake-chain below-threshold/threshold/changed-block/missing-receipt/outage/recovery tests show no pre-finality fulfillment and exactly one reorg exception.
- **Safety / release gate:** Fulfillment stays disabled until finality/reorg policy and runbook are approved.
- **Owner decisions / blockers:**
- Set thresholds, pending age, finalized-tag need, and remedy for delivered work reversed later.

### 21. `PAY-04` — Atomic global claims and idempotency

- **Phase:** `2-implementation`
- **Priority:** **P0**
- **Objective:** Ensure a finalized transfer/event can fund at most one operation and retries/races cannot duplicate work.
- **Dependencies:** `PAY-01`, `PAY-02`, `PAY-03`
- **Evidence / files to inspect:**
- src/sincor2/x402_payments.py:258-358; src/sincor2/a2a_integration.py:3440-3478; src/sincor2/stripe_routes.py:242-280; docs/security/PAYMENT_TX_REPLAY_DESIGN.md:21-134
- **Micro-steps:**
  - Enforce durable chain+tx+log claim, owner binding, unique Stripe event and operation keys, history-preserving fulfillment writes, and outbox.
- **Tests and acceptance criteria:**
  - Independent connection contention yields one winner and no loser entitlement/task; repeated event/checkout/outbox produces one effect; conflict payload rejects.
- **Safety / release gate:** Parallel ingress is prohibited until selected topology proves durable uniqueness; store outage fails closed.
- **Owner decisions / blockers:**
- Choose log versus transaction uniqueness and idempotency retention.

### 22. `PAY-06` — Canonical crypto checkout

- **Phase:** `2-implementation`
- **Priority:** **P1**
- **Objective:** Make checkout quote server-authoritative, finality-gated, uniquely claimed, and retry-safe.
- **Dependencies:** `PAY-01`, `PAY-03`, `PAY-04`
- **Evidence / files to inspect:**
- src/sincor2/platform_payments.py:115-169,510-641,781-856; tests/test_checkout_edge_cases.py:20-89; tests/test_platform_payments_usdc_fallback.py:19-75
- **Micro-steps:**
  - Validate plan/token/chain/treasury/price/precision/expiry from fixed quote; use monotonic canonical states and outbox; record quote/version/fallback evidence.
- **Tests and acceptance criteria:**
  - Altered quote/key, under/overpayment, contention, expiry, pre-finality, duplicate, and reorg tests never provision incorrectly.
- **Safety / release gate:** No checkout cutover until migration/reconciliation/rollback rehearsal; unavailable store/finality disables fulfillment.
- **Owner decisions / blockers:**
- Approve expiration, under/overpay policy, supported plans/tokens/chains.

### 23. `PAY-05` — Bounded x402 entitlement

- **Phase:** `2-implementation`
- **Priority:** **P1**
- **Objective:** Issue explicit single-use/credit/subscription entitlement only from finalized uniquely claimed x402 payment.
- **Dependencies:** `PAY-01`, `PAY-03`, `PAY-04`, `PAY-06`
- **Evidence / files to inspect:**
- src/sincor2/x402_payments.py:258-358,393-450; config/x402_pricing.yaml:4-29; tests/test_x402_payments.py
- **Micro-steps:**
  - Define SKU unit/expiry/uses/resource/reversal; atomically create and consume use before dispatch; bind credentials and persist across restart.
- **Tests and acceptance criteria:**
  - Concurrent redemption, same-operation retry, second operation, expiry, restart, refund/reorg, and resource mismatch are offline-stubbed and quota-safe.
- **Safety / release gate:** Paid x402 dispatch remains disabled for any undefined commercial/reversal policy.
- **Owner decisions / blockers:**
- Approve entitlement units, concurrency, retries, result access, expiry, and refund rules.

### 24. `PAY-07` — Durable Stripe webhooks and refunds

- **Phase:** `2-implementation`
- **Priority:** **P1**
- **Objective:** Persist signature-verified events before acknowledgement and process ordered/out-of-order completion/refund/dispute safely.
- **Dependencies:** `PAY-01`, `PAY-04`, `PAY-06`
- **Evidence / files to inspect:**
- src/sincor2/stripe_checkout.py:133-167; src/sincor2/stripe_routes.py:141-178,242-283; src/sincor2/mvp_blueprints/billing.py:269-348
- **Micro-steps:**
  - Write verified event inbox, canonical transitions/outbox, provider-state adapter, immutable full/partial refund records, and separate refund authority from observation.
- **Tests and acceptance criteria:**
  - Fake verifier/provider invalid/repeated/conflicting/out-of-order/refund/DB-restart cases cause one transition/effect and no refund over-capture.
- **Safety / release gate:** Live Stripe fulfillment/automated refunds disabled until recovery/signature/migration/refund authority gates pass.
- **Owner decisions / blockers:**
- Approve event authority/types, dispute/delayed handling, refund initiators, and suspension timing.

### 25. `ID-04_PAY-08` — Verified current-entitlement access and cancellation

- **Phase:** `2-implementation`
- **Priority:** **P0**
- **Objective:** Gate orders, vaults, paid results/materials, and subscription cancellation on verified owner/scoped capability plus active canonical entitlement.
- **Dependencies:** `ID-01`, `RT-01_ID-05`, `PAY-01`, `PAY-03`, `PAY-04`, `PAY-05`, `PAY-06`, `PAY-07`
- **Evidence / files to inspect:**
- src/sincor2/mvp_blueprints/billing.py:570-615,788-839,1064-1115; src/sincor2/platform_payments.py:728-747; src/sincor2/x402_payments.py:393-450
- **Micro-steps:**
  - Inventory all routes; query owner+payment+entitlement+resource+revocation; use hashed expiring single-use pre-account capabilities; authorize exact subscription before provider cancellation; invalidate caches/links.
- **Tests and acceptance criteria:**
  - Anonymous/A-to-B/malformed/expired/replayed/wrong-resource/state/cancellation tests deny before material/provider call; valid owner gets only selected resource.
- **Safety / release gate:** Routes stay unavailable/admin-restricted until cross-owner tests pass; rollback disables routes, never email/order-existence access.
- **Owner decisions / blockers:**
- Choose pre-account UX, sharing/seats, refund/cancel semantics, grace period, and cache SLA.

### 26. `PAY-09` — Auditable ledger and reconciliation

- **Phase:** `2-implementation`
- **Priority:** **P1**
- **Objective:** Reconcile canonical payments, provider/chain evidence, fulfillment, refunds, conversions, and treasury/inflow through immutable idempotent entries and exceptions.
- **Dependencies:** `PAY-01`, `PAY-03`, `PAY-04`, `PAY-07`, `ON-02`
- **Evidence / files to inspect:**
- src/sincor2/agent_billing.py:30-60; src/sincor2/x402_payments.py:369-390; src/sincor2/stripe_routes.py:242-283; docs/DEFI_LEDGER_SKU_CANON.md
- **Micro-steps:**
  - Define balanced entries; idempotent posting; read-only provider/chain reconciliation adapters; mismatch exceptions; dual-approved adjustments and runbooks.
- **Tests and acceptance criteria:**
  - Fixture exact/missing/duplicate/mismatch/refund/reorg/outage replay tests give one entry/exception and zero unexplained clean-batch variance.
- **Safety / release gate:** No reconciled-book claim, financial reporting, settlement, or automated refund until finance approves and independently reviews evidence.
- **Owner decisions / blockers:**
- Finance approves basis, fees, FX, cadence, SLA, thresholds, and provider/export access.

### 27. `RT-05` — Tenant-scoped durable decision/effect evidence

- **Phase:** `2-implementation`
- **Priority:** **P1**
- **Objective:** Persist linked redacted intent, policy, approval, receipt, and outcome lifecycle without conflating proposal with execution.
- **Dependencies:** `RT-01_ID-05`, `RT-04`, `OPS-05`
- **Evidence / files to inspect:**
- src/sincor2/shadow_monitor/events.py:30-125,173-223,239-328; src/sincor2/shadow_monitor/effect_boundary.py:194-278
- **Micro-steps:**
  - Reject/quarantine prohibited PII, persist hashes/allowlisted metadata, authorize tenant queries at evidence service, migrate JSONL with count/chain verification.
- **Tests and acceptance criteria:**
  - Append/restart/replay/tamper/concurrency/tenant-redaction tests pass; each gateway attempt records evidence or fails closed.
- **Safety / release gate:** Proposal counts are not outcomes or production audit proof until durable access/redaction/migration gates pass.
- **Owner decisions / blockers:**
- Select evidence backend, retention, encryption/access, export, and allowed fields.

### 28. `RT-06_OPS-06` — Durable telemetry, alerts, and dead-man monitoring

- **Phase:** `2-implementation`
- **Priority:** **P1**
- **Objective:** Combine runtime alert durability with operational telemetry: persist outbox/dedup/retries, heartbeats, redacted logs, bounded metrics, independent alert health, and human-only acknowledgement.
- **Dependencies:** `RT-05`, `OPS-03`, `OPS-04`
- **Evidence / files to inspect:**
- src/sincor2/shadow_monitor/alerting.py:210-334; src/sincor2/blueprints/monitoring.py:46-142; src/sincor2/task_queue.py:341-351; src/sincor2/observability.py; src/sincor2/liveness.py
- **Micro-steps:**
  - Persist alert attempts/receipts/paused state; heartbeat ingress/policy/logger/workers/adapters/sender; emit bounded redacted metrics/logs; separate alert delivery from agent runtime.
- **Tests and acceptance criteria:**
  - Restart, duplicate, sender failure, retry exhaustion, dead-man, queue stall, backup age, and alert-sink failure create durable deduplicated alert/health result without PII; agent cannot clear.
- **Safety / release gate:** Monitoring is not active until required heartbeats and independently tested approved alert route exist.
- **Owner decisions / blockers:**
- Choose monitoring backend, receiver/on-call, thresholds/SLOs, escalation, retry, and retention.

### 29. `OB-04` — Human campaign approval queue

- **Phase:** `2-implementation`
- **Priority:** **P0**
- **Objective:** Require immutable scoped human approval for draft cohort/content before each cold-outreach batch.
- **Dependencies:** `OB-01`, `OB-02`, `OB-03`
- **Evidence / files to inspect:**
- src/sincor2/outreach_engine.py:201-239; src/sincor2/outreach_scheduler.py:32-56; docs/safety/OWNER_DECISIONS_2026-10-09.md:6-12
- **Micro-steps:**
  - Version draft/cohort/provenance/content/sender/channel/limits; consume expiring one-shot approval atomically; recheck eligibility/suppression/flag/limits each recipient; support revoke/cancel.
- **Tests and acceptance criteria:**
  - Missing/expired/wrong/revoked/exhausted approval and changed eligibility make zero fake calls; concurrent consumption cannot exceed approved count.
- **Safety / release gate:** Campaigns stay draft-only absent explicit founder approval for exact version/scope.
- **Owner decisions / blockers:**
- Choose approver/quorum, expiry, batch/time limits, and material-change invalidation.

### 30. `OB-05` — Evidence-backed reviewed publishing

- **Phase:** `2-implementation`
- **Priority:** **P1**
- **Objective:** Prevent unsupported content claims and route WordPress/social effects through approved immutable review versions.
- **Dependencies:** `OB-01`, `RT-04`, `P1_P6`
- **Evidence / files to inspect:**
- src/sincor2/content_agent.py:1030-1038; src/sincor2/content_scheduler.py; src/sincor2/unified_content_engine.py; content/calendar.json; README.md:19-20
- **Micro-steps:**
  - Create claim ledger and immutable content review hash with evidence/audience/channel/time; block stale/unsupported/regulatory/customer/quantitative claims; recheck cancellation before publish.
- **Tests and acceptance criteria:**
  - Unsupported/expired/changed/revoked content makes zero fake publishes; approved fake receipt records hash, reviewer, evidence, channel, provider result; inventory detects bypass.
- **Safety / release gate:** No external content publication absent channel owner, claim policy, review, and separate authorization.
- **Owner decisions / blockers:**
- Set evidence standard/age and reviewer/channel policy.

### 31. `OB-06` — Bounded provider usage and kill switch

- **Phase:** `2-implementation`
- **Priority:** **P1**
- **Objective:** Bound source/sink APIs with durable quotas, idempotent outbox, retries, circuits, and effect kill switch.
- **Dependencies:** `OB-02`, `OB-03`, `OB-04`, `OB-05`, `RT-06_OPS-06`
- **Evidence / files to inspect:**
- src/sincor2/outreach_engine.py:155-239; src/sincor2/email_sender.py:219-248; src/sincor2/content_agent.py:1030-1038; src/sincor2/outreach_scheduler.py:30-57
- **Micro-steps:**
  - Inventory providers/operations/terms; atomically reserve campaign/tenant/provider budgets; classify accepted/rejected/unknown/retryable; use bounded backoff/circuit/kill switch and minimized telemetry.
- **Tests and acceptance criteria:**
  - Concurrency quota, timeout-after-acceptance, unavailable/policy/queue/circuit/kill tests never cause unapproved fake provider call and expose alert.
- **Safety / release gate:** No real provider calls in CI/staging; provider terms/quotas/monitoring/kill switch must be approved before any separate enablement.
- **Owner decisions / blockers:**
- Set limits, retry ceilings, SLO, owner, and allowed source APIs/terms.

### 32. `ON-02` — Durable receipt-backed conversion obligations

- **Phase:** `2-implementation`
- **Priority:** **P1**
- **Objective:** Make conversion lifecycle uniquely claimed, recoverable, finality-backed, and balance-reconciled rather than local JSON status.
- **Dependencies:** `CT-01`, `ON-06`, `PAY-02`, `PAY-03`
- **Evidence / files to inspect:**
- src/sincor2/onchain/fee_conversion_executor.py:221-305,537-703; docs/ops/FEE_EXECUTOR_RUNBOOK.md:22-102; tests/test_treasury_settlement.py:21-67
- **Micro-steps:**
  - Use source receipt/log obligation ID, transactional journal/leases/transitions, pinned route/quote/min-output validation, receipt/finality/balance checks, and reconcile-required state.
- **Tests and acceptance criteria:**
  - Mock RPC/signing covers replay/concurrency/revert/noncanonical/wrong target/short output/crash-resume; invariant permits at most one completed swap/forward.
- **Safety / release gate:** Remain disarmed until route/policy, fork simulation, approved dust procedure, and independent reconciliation evidence exist; no dust transaction is authorized here.
- **Owner decisions / blockers:**
- Choose asset, route/pools, caps, slippage, dust, confirmations, and durable journal backend.

### 33. `ON-03` — Authorized token-backed P24 fee settlement

- **Phase:** `2-implementation`
- **Priority:** **P1**
- **Objective:** Replace unrestricted counters with measured token receipt, authorized split writer, safe transfers, conserved liabilities, and one-time claims.
- **Dependencies:** `CT-01`, `ON-06`, `ON-02`
- **Evidence / files to inspect:**
- onchain/src/p24/FeeSplitDistributor.sol:8-57; onchain/test/P24Deploy.t.sol:29-34; onchain/test/P24AccessControl.t.sol:7-71
- **Micro-steps:**
  - Authorize one settlement interface; receive tokens atomically; apply 4940/4940/120 BPS and dust rule; safe transfer claims; immutable payment/recipient events.
- **Tests and acceptance criteria:**
  - Fuzz amounts/rounding and unauthorized/token-failure/replay/double-claim cases; invariant liabilities never exceed balance and payouts equal received fee.
- **Safety / release gate:** No trade fees route here until token custody, roles, economics, destination, and independent review pass.
- **Owner decisions / blockers:**
- Approve economics/dust, writer, supported tokens, platform destination, and migration treatment.

### 34. `ON-04` — Authorized snapshot-correct P24 revenue accrual

- **Phase:** `2-implementation`
- **Priority:** **P1**
- **Objective:** Replace public synthetic balances/revenue and nonpaying claims with source-backed snapshot accounting and token payouts.
- **Dependencies:** `ON-03`, `ON-06`
- **Evidence / files to inspect:**
- onchain/src/p24/RevenueAccrual.sol:8-85; onchain/test/P24Deploy.t.sol:32-34
- **Micro-steps:**
  - Select trusted balance source; remove public setBalance; authorize measured revenue only; define complete snapshot/epoch/eligibility/dust/closure; reserve and transfer one-time claims.
- **Tests and acceptance criteria:**
  - Late joiner/transfer/snapshot/empty-supply/reentrancy/dust/replay fuzz proves claimable never exceeds received and claims plus escrow equal accrual.
- **Safety / release gate:** No real accrual until snapshot policy and independent holder/payout conservation review pass.
- **Owner decisions / blockers:**
- Choose snapshot model, eligibility, accrual/pause roles, and unclaimed/dust disposition.

### 35. `ON-05` — Canonical escrow and task-settlement lifecycle

- **Phase:** `2-implementation`
- **Priority:** **P1**
- **Objective:** Choose one settlement contract and implement receipt-bound funded/claimed/verified/disputed/refunded/slashed/closed state machine.
- **Dependencies:** `CT-01`, `ON-06`, `PAY-02`, `PAY-03`
- **Evidence / files to inspect:**
- contracts/escrow/SincorTaskEscrow.sol:14-103; test/SincorEscrow.t.sol:47-177; src/sincor2/SincorsettlementEngine.sol:8-78
- **Micro-steps:**
  - Name canonical contract; restrict caller/reporter, bind task/payment/token/amount/deadline/authority, define dispute/refund/abandon rules, and emit settlement receipt.
- **Tests and acceptance criteria:**
  - Wrong token/amount, duplicate receipt, arbitrary reporter, invalid slash/refund/cancel/dispute transitions fail; invariant terminal allocation never exceeds escrow.
- **Safety / release gate:** No customer/task funds until contract, dispute authority, receipt source, and independent review are approved.
- **Owner decisions / blockers:**
- Select canonical contract; approve verifier/dispute/slash/recovery/refund policy and legacy migration.

### 36. `P2` — Select one paid-pilot wedge

- **Phase:** `2-implementation`
- **Priority:** **P0**
- **Objective:** Choose exactly one measurable supervised B2B buyer/workflow after evidence-based discovery, not collateral assumptions.
- **Dependencies:** `P1_P6`
- **Evidence / files to inspect:**
- PRODUCTS/AUD-01/SPEC.md:1-24; PRODUCTS/AUD-01/SALES.md:1-15; src/sincor2/obs_skus/registry.py:124-143; tests/pytest/test_obs_sku_registry.py:64-67
- **Micro-steps:**
  - Compare AUD-01 with one alternative; collect approved interviews/observed spend/commitments; record ICP, integration, baseline, target, disconfirming evidence, and stop date.
- **Tests and acceptance criteria:**
  - Decision record names one persona/workflow/integration/baseline/target/owner/stop condition and distinguishes interest from signed/collected evidence.
- **Safety / release gate:** No broad SKU/product expansion until an owner-selected wedge has at least one written paid-pilot acceptance.
- **Owner decisions / blockers:**
- CEO/Product selects offer/ICP/target; Sales secures sponsor/data access or stops.

### 37. `P3` — Bounded paid-pilot specification

- **Phase:** `2-implementation`
- **Priority:** **P1**
- **Objective:** Define customer inputs, allowed data, human review, deliverable, acceptance, rework, refund, support, exclusions, baseline, and numeric outcome.
- **Dependencies:** `P2`
- **Evidence / files to inspect:**
- PRODUCTS/AUD-01/SPEC.md:1-24; src/sincor2/obs_skus/forensic_audit.py:1235-1270,1529; tests/pytest/test_obs_sku_registry.py:121-137
- **Micro-steps:**
  - Write signed SOW and synthetic/customer-approved acceptance fixtures with provenance; require human review for consequential output and define data return/deletion.
- **Tests and acceptance criteria:**
  - Synthetic walkthrough meets each numbered criterion and traces claims to source; spec has finite scope, responsible people, baseline, target, acceptance/rework/refund terms.
- **Safety / release gate:** Do not accept payment/represent availability without signed scope, lawful data access, delivery owner, and acceptance/refund terms.
- **Owner decisions / blockers:**
- Product/Delivery, customer sponsor, and legal/compliance approve scope, data, metric, SLA, and terms.

### 38. `P4` — Offer-specific verified onboarding

- **Phase:** `2-implementation`
- **Priority:** **P1**
- **Objective:** Bind selected-pilot intake to verified account/tenant and canonical order/engagement with minimum necessary data and auditable purpose.
- **Dependencies:** `P3`, `ID-01`, `ID-03`, `ID-04_PAY-08`
- **Evidence / files to inspect:**
- src/sincor2/mvp_blueprints/auth.py:189-270; PRODUCTS/AUD-01/SPEC.md:1-24
- **Micro-steps:**
  - Map data/purpose/retention; use ID-01/ID-03 verified handoff; distinguish service authority from marketing; provide support/recovery/receipt without parallel auth.
- **Tests and acceptance criteria:**
  - Unverified/forged/mismatched/expired/repeat onboarding cannot access/mutate; consent audit retains purpose/version/actor/time; verified completion maps to selected offer.
- **Safety / release gate:** No live data intake through reviewed flow until identity/payment owners evidence correct verified ownership/order scoping.
- **Owner decisions / blockers:**
- Identity, privacy/legal, and product approve proof, notices, retention, and minimum fields.

### 39. `P5` — Pilot pricing and contribution economics

- **Phase:** `2-implementation`
- **Priority:** **P1**
- **Objective:** Price a bounded customer-visible pilot unit and measure collected revenue, fulfillment, refunds, and fully loaded delivery costs.
- **Dependencies:** `P1_P6`, `P3`, `PAY-09`
- **Evidence / files to inspect:**
- data/sku_catalog.json:7-59; docs/WEBSITE_PRICING_COPY.md:13-26; config/x402_pricing.yaml:10-35; src/sincor2/platform_payments.py; tests/test_sku_landings.py:9-16
- **Micro-steps:**
  - Choose approved pilot price/unit/refund; capture labor/review/provider/compute/support/CAC; separate signed/invoiced/collected/refunded/fulfilled/deferred; version/reverse prices.
- **Tests and acceptance criteria:**
  - Contract/checkout/page/order agree; contribution report accounts for all known costs and shows unknowns as unknown; margin/renewal threshold is set before pilot.
- **Safety / release gate:** No scaled/recurring economics claim until collected cash, accepted work, refunds, and costs reconcile.
- **Owner decisions / blockers:**
- CEO/Finance approve unit/refund/margin floor and revenue definitions; Product/Sales decide renewal conversion.

### 40. `ID-07` — Privacy export, erasure, retention, and legacy handling

- **Phase:** `2-implementation`
- **Priority:** **P2**
- **Objective:** Make privacy requests truthful across profiles, orders, tasks, consent, links, logs, processors, backups, active service, and retained accounting records.
- **Dependencies:** `ID-01`, `ID-02`, `ID-03`, `ID-04_PAY-08`, `RT-01_ID-05`, `RT-05`, `OPS-05`
- **Evidence / files to inspect:**
- src/sincor2/mvp_blueprints/auth.py:296-325; src/sincor2/mvp_app.py:646-663; src/sincor2/mvp_blueprints/billing.py:788-826; src/sincor2/mvp_blueprints/auth.py:274-285
- **Micro-steps:**
  - Map records/processors; replace immediate-erasure claim with verified asynchronous export/erasure state machine, retention exceptions, idempotency, downstream acknowledgements, and truthful copy.
- **Tests and acceptance criteria:**
  - Unauthorized/repeat/partial-failure/retry/legally-retained/completed request tests cover every in-scope store; synthetic staging reconciliation reaches terminal state.
- **Safety / release gate:** Do not advertise deletion completion or revise privacy promises until approved data map/retention and backup/restore rehearsal exist.
- **Owner decisions / blockers:**
- Legal/privacy approves controller map, processors, exceptions, SLAs, backups, and order/service/accounting treatment.

### 41. `OPS-01_ON-07` — Required offline CI, adversarial invariants, and regression gates

- **Phase:** `3-verification`
- **Priority:** **P0**
- **Objective:** Consolidate representative PR CI with cross-contract financial invariants and deterministic no-live-effects test policy.
- **Dependencies:** `PAY-01`, `OPS-04`, `ON-03`, `ON-04`, `ON-05`, `OB-06`
- **Evidence / files to inspect:**
- .github/workflows/ci.yml:3-6; .github/workflows/pr-ci.yml:7-69; .github/workflows/onchain-ci.yml:18-67; onchain/foundry.toml:34-39; onchain/test/invariant/SharedLiquidityVault.invariant.t.sol
- **Micro-steps:**
  - Enable owner-approved PR lint/unit/integration/Foundry jobs with explicit dependency/skip status; add path coverage, payment/runtime/outbound tests, token mock fuzz, handler invariants, and static sink checks.
- **Tests and acceptance criteria:**
  - Representative source/config/test/workflow changes schedule required job; intentional failure blocks merge; invariants cover conservation, replay, reentrancy, and terminal settlement; no job has provider/RPC/signing credentials.
- **Safety / release gate:** No operational change merges while relevant required checks are absent, unapproved-skipped, or failing.
- **Owner decisions / blockers:**
- Confirm runner/billing, required checks, duration budget, Foundry scope, invariant thresholds, and static-analysis waiver policy.

### 42. `OB-07` — Outbound regression, migration, and staged rollout gate

- **Phase:** `3-verification`
- **Priority:** **P0**
- **Objective:** Turn outbound controls into offline regression gates and preserve opt-outs/receipts through migration, rollback, and non-deliverable drills.
- **Dependencies:** `OB-01`, `OB-02`, `OB-03`, `OB-04`, `OB-05`, `OB-06`, `OPS-01_ON-07`
- **Evidence / files to inspect:**
- docs/safety/OWNER_DECISIONS_2026-10-09.md:6-17; src/sincor2/mvp_app.py:527-532; src/sincor2/scheduler.py:115-130; src/sincor2/email_sender.py:200-248; src/sincor2/webbuilder_crm.py:56-79
- **Micro-steps:**
  - Build offline suite/manifest; rehearse additive consent/provenance/outbox migration and rollback; reconcile docs; define draft-only then fake/sandbox staging and kill-switch drill.
- **Tests and acceptance criteria:**
  - Network-disabled CI covers 100% inventoried effect functions; synthetic legacy migration never opts in/unsuppresses; non-deliverable kill switch drill and documented owner/alert/rollback pass.
- **Safety / release gate:** No external send/publication is production-ready or enabled by this task.
- **Owner decisions / blockers:**
- Name approval/provider-limit/alert/migration/kill-switch owners and any later canary size/stop conditions.

### 43. `OPS-07` — Incident response and recovery rehearsal

- **Phase:** `3-verification`
- **Priority:** **P1**
- **Objective:** Give named operators tested containment, evidence, communication, recovery, and safe-resume procedures.
- **Dependencies:** `OPS-04`, `OPS-05`, `RT-06_OPS-06`
- **Evidence / files to inspect:**
- .github/workflows/deploy-base.yml:25-39; src/sincor2/task_queue.py:341-375; src/sincor2/blueprints/monitoring.py:46-77; docs/deployment/README.md
- **Micro-steps:**
  - Write runbooks for queue duplicate/outage, unready app, migration/restore, alert failure, unsafe scheduler/deploy; add auditable pause controls and rehearse with second operator.
- **Tests and acceptance criteria:**
  - Two non-production scenarios record detection/ack/contain/recovery; second operator completes runbook from clean state; critical gaps close before surface enablement.
- **Safety / release gate:** Affected workers/deployments remain disabled absent named coverage, working stop control, test alert, and rehearsal evidence.
- **Owner decisions / blockers:**
- Assign incident/on-call/communications owners, severity, targets, customer-notification authority, and stop/resume rights.

### 44. `OPS-08` — Synthetic load and fault qualification

- **Phase:** `3-verification`
- **Priority:** **P2**
- **Objective:** Measure selected topology capacity and recovery before setting scaling, concurrency, or uptime claims.
- **Dependencies:** `OPS-03`, `OPS-04`, `OPS-05`, `RT-06_OPS-06`
- **Evidence / files to inspect:**
- src/sincor2/task_queue.py:61-88,192-209,341-351; docker-compose.yml:29-36,43-73; tests/pytest/test_async_queue.py; tests/pytest/test_runtime_and_health.py
- **Micro-steps:**
  - Define synthetic workload; run bounded isolated broker/store/app faults; measure throughput, p50/p95/p99, loss/duplicate/retry, resources, recovery/backlog; set admission/alert limits.
- **Tests and acceptance criteria:**
  - At least documented baseline and stress/fault scenario show zero lost accepted/duplicate committed effects under model; owner-approved steady-state/backlog/recovery thresholds are measured.
- **Safety / release gate:** No capacity, scale, or uptime claim and no concurrency increase before reviewed synthetic evidence.
- **Owner decisions / blockers:**
- Choose workload, SLOs, target concurrency, test budget, and recovery objective.

### 45. `RT-07` — Shadow coverage and reversible runtime rollout

- **Phase:** `4-staging-release-promotion`
- **Priority:** **P1**
- **Objective:** Gate runtime rollout on complete entrypoint/sink contract tests, truthful readiness, synthetic shadow canary, kill switch, and reconciliation.
- **Dependencies:** `RT-01_ID-05`, `RT-02`, `RT-03`, `RT-04`, `RT-05`, `RT-06_OPS-06`, `OPS-01_ON-07`, `OPS-07`
- **Evidence / files to inspect:**
- src/sincor2/mvp_app.py:62-65,125-129; tests/pytest/test_shadow_boundary.py; tests/pytest/test_shadow_runtime_isolation.py; docs/ASYNC_TASK_QUEUE.md:31-45; docs/RUNTIME_STATE.md:8-12
- **Micro-steps:**
  - Enumerate routes/A2A/vertical/kernel/queue/scheduler entrypoints; require context/policy/manifest; add readiness; canary synthetic tenant with outbound traps; document pause/restart/replay.
- **Tests and acceptance criteria:**
  - All enumerated paths/sinks pass contracts, traps stay quiet, failures deny, isolation passes, readiness fails for missing component, and canary metrics are reviewed.
- **Safety / release gate:** Runtime remains shadow-only/paused; this is not live-effect approval. Missing evidence or unhealthy heartbeat is hold.
- **Owner decisions / blockers:**
- Approve synthetic canary duration/evidence threshold and confirm actual topology, flags, egress, stores, scheduler, credentials isolation, and alert routing.

### 46. `OPS-02_ON-08` — Protected immutable releases and verified onchain deployment dossier

- **Phase:** `4-staging-release-promotion`
- **Priority:** **P0**
- **Objective:** Combine branch/environment protection and artifact provenance with chain-state manifest/monitoring so repository JSON/CI claims are never treated as deployment proof.
- **Dependencies:** `CT-01`, `ON-06`, `ON-02`, `ON-03`, `ON-04`, `ON-05`, `OPS-01_ON-07`, `OPS-05`, `RT-06_OPS-06`
- **Evidence / files to inspect:**
- .github/workflows/deploy-base.yml:1-4,25-39; .github/workflows/pr-ci.yml:22-25; onchain/deployments/base-8453.json:1-34; onchain/deployments/base-sepolia-p24.json:2-20; onchain/script/07_DeployP24.s.sol:79-135
- **Micro-steps:**
  - Configure protected branches/reviewers/environments/break-glass; remove workspace private-key material; build once/promote digest; validate deployment manifest with commit, code hash, constructor, roles, treasury, receipts, finality, source verification, independent review; add read-only drift/liability monitors.
- **Tests and acceptance criteria:**
  - Non-production rules reject unapproved merge/ref; deployed digest equals tested digest; manifest rejects zero/template/wrong-chain/tampered code/role/treasury/receipt; monitors are read-only.
- **Safety / release gate:** No privileged production deployment, signing, money movement, or go decision follows from this plan. Independent operator/reviewer must verify actual target-chain and deployment state through approved procedure.
- **Owner decisions / blockers:**
- Name code/release/environment owners, signing mechanism, protection rules, evidence schema/reviewer/retention, monitoring/on-call, and whether deploy workflow stays enabled.

### 47. `P7` — Evidence-led staged product roadmap

- **Phase:** `4-staging-release-promotion`
- **Priority:** **P2**
- **Objective:** Sequence from internal walkthrough to supervised paid pilot, accepted delivery, renewal, and only then broader product/automation expansion.
- **Dependencies:** `P2`, `P3`, `P4`, `P5`, `P1_P6`, `ID-04_PAY-08`, `RT-07`, `OPS-02_ON-08`
- **Evidence / files to inspect:**
- src/sincor2/obs_skus/gates.py:5-9,347-356; tests/pytest/test_obs_sku_registry.py:121-137,304-325; docs/ops/PRODUCTION_DEPLOY_CHECKLIST.md; docs/A2A_PRODUCTION_CHECKLIST.md
- **Micro-steps:**
  - Record outcome/acceptance/rework/time/cost/revenue/refund metrics; predefine unsafe-effect, data/claim, payment/order, outcome, margin, complaint stop triggers; weekly review and stage decision.
- **Tests and acceptance criteria:**
  - Each release decision has owner, evidence, threshold, risk/rollback, date; completed pilot workflows have outcome/acceptance/cost; expansion requires target, margin floor, acceptance, and renewal.
- **Safety / release gate:** No public/production launch conclusion or expanded autonomy until actual deployment and all independent identity/payment/runtime/operations/compliance gates pass for intended path.
- **Owner decisions / blockers:**
- CEO/Product, Engineering/Operations, payment/identity owners, compliance owner, and customer sponsor approve their stated gates and stage decision.

## 5. Cross-cutting release gates

- All owner decisions must be recorded with scope, approver, expiry, and rollback/revocation path; otherwise affected capability is disabled.
- All migrations need additive plan, backup, synthetic rehearsal, reconciliation, and rollback/forward-fix; legacy data cannot be silently upgraded to proof, consent, ownership, paid state, or entitlement.
- Every production-bound access/dispatch/effect requires verified principal/tenant, exact resource ownership, durable idempotency, policy decision, and redacted evidence.
- All payment and onchain paths require canonical state, finality, unique claim, reconciliation, and finance/custody gate; no source file or test result proves funds, balances, roles, deployment, or settlement.
- All external-effect tests are fake/sandbox/non-deliverable and network/credential controlled; no task authorizes live provider calls, publication, sending, signing, deployment, custody, transfers, or trading.
- A missing deployment-specific value, health signal, source manifest entry, alert receiver, backup/restore evidence, or independent review is a hold, not an assumed pass.

### Evidence a builder must attach to each completed task

- Task ID, reviewer/owner, changed-file list, and commit(s).
- Test commands and actual results, including skipped tests and reasons; no claims based on expected outcomes.
- For migrations: schema version, synthetic fixture counts/reconciliation, restart/resume, backup/restore and rollback/forward-fix evidence.
- For effect boundaries: inventory delta, fake sink count, proof denial made zero effect calls, tenant/actor/policy/idempotency metadata, and alert receipt where applicable.
- For payment/contract work: invariant/test output, state transition and reconciliation evidence; distinguish test evidence from actual chain deployment and actual funds.
- For outward product claims: exact claim, owner, current evidence, version/date, approved wording, and deployment/customer evidence if the claim implies either.
- For staging: topology/config/egress/credential-separation evidence collected through approved procedures. Never put secret values into the report.

## 6. Decisions the builder must not invent

- Identity: customer proof/recovery, OAuth provider/linking, principal-to-tenant mapping, task visibility, and legacy ownership.
- Privacy/legal: consent/lawful basis, source-use matrix, retention/processors/backups, suppression, regulated claims, and data-rights treatment.
- Payments/finance: durable store, chains/tokens/quotes/finality, claim semantics, entitlement/refund rules, Stripe authority, ledger/accounting/reconciliation.
- Runtime/operations: hosting topology, feature profiles, queue/store, effect taxonomy/egress, evidence/monitoring/alerting, backup RPO/RTO, incident owners, CI/protection/release policy.
- Onchain/custody: live-capability approval, contract/economic/dispute rules, HSM/Safe/multisig/roles, signer limits, deployment evidence, independent review.
- Outbound/product: transactional purposes, channels, campaign/publish reviewers, quotas, pilot wedge/SOW/pricing/margin/launch thresholds.

Record each decision with: **decision ID; exact scope; chosen option; decision owner/approver; date; expiry/review trigger; evidence; affected task IDs; and rollback/revocation path.** If no decision is supplied, keep the relevant capability disabled.

## 7. Explicit non-goals until separately authorized

- Production deployment or promotion.
- Live email, cold outreach, social/WordPress publication, CRM effects, or provider/API sends.
- Public-chain broadcasts, fund movement, refund execution, escrow payout, treasury conversion, wallet/custody/key use, or live trading.
- Provider, RPC, credential, secret, customer-data, production-log, production-backup, or production-account inspection.
- Legal/compliance certification, production-readiness claim, settlement/accounting correctness claim, customer outcome claim, or capacity/uptime claim.
- Automatic remediation that changes permissions, replays ambiguous effects, resumes work, or overrides human approval.

## 8. Unknowns that must remain visible

- No workstream is listed as failed; FAILED_WORKSTREAMS is empty.
- Static source facts are limited to the exact references above. No tests/app/workers/deployments/providers/RPCs were executed or inspected in the supplied workstreams.
- Unknown runtime/deployment facts include deployed code/configuration/flags, route reachability, HTTPS/proxy setup, secret-store state, provider registrations, queue/topology/durability, database/backup state, logs/retention, alert delivery, and current health.
- Unknown commercial/legal facts include customer demand/contracts/cash/refunds, actual pricing authority, consent/provenance/legal basis, processor inventory, regulatory applicability, and retention rules.
- Unknown financial/onchain facts include verified addresses/code/roles, balances/liabilities, custody/signers, RPC quality, finality history, settlement exports, conversions, and reconciliations.
- Verification microtasks before any later promotion: authorized operational inspection of actual topology/feature flags/readiness; independent deployment/role/code evidence validation; approved provider/custody/finance/legal evidence collection; and synthetic staging rehearsal against the selected architecture.

## 9. Builder progress ledger template

| Task ID | Status (not started / in progress / blocked / done) | Owner | Dependencies cleared? | Code/PR link | Tests run and result | Acceptance evidence | Decision/rollback notes |
|---|---|---|---|---|---|---|---|
| `CT-01` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `OB-01` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `ID-01` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `RT-03` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `RT-02` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `ID-06` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `ID-02` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `ID-03` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `RT-01_ID-05` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `PAY-01` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `OPS-03` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `OPS-04` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `OPS-05` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `RT-04` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `OB-03` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `OB-02` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `ON-06` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `P1_P6` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `PAY-02` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `PAY-03` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `PAY-04` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `PAY-06` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `PAY-05` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `PAY-07` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `ID-04_PAY-08` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `PAY-09` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `RT-05` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `RT-06_OPS-06` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `OB-04` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `OB-05` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `OB-06` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `ON-02` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `ON-03` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `ON-04` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `ON-05` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `P2` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `P3` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `P4` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `P5` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `ID-07` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `OPS-01_ON-07` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `OB-07` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `OPS-07` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `OPS-08` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `RT-07` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `OPS-02_ON-08` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |
| `P7` | Not started | TBD | TBD | TBD | TBD | TBD | TBD |

## 10. Final go/no-go checklist

- Every customer session and API action requires verified principal, role and tenant/resource ownership; cross-customer access, cancellation, consent/profile mutation and admin paths fail closed.
- Every payment path rejects synthetic inputs, trusts server-side quote data, verifies attributable transfer/event evidence, enforces finality and unique claims, and grants only the purchased bounded entitlement.
- Every agent/A2A/worker/scheduler effect is present in a source-to-sink manifest and passes through one fail-closed policy/effect boundary; missing policy, context, store, queue or alert path blocks the effect.
- No planner-controlled in-process exec or broad filesystem read remains in an agent-facing runtime; any separately approved sandbox is isolated by OS/container controls, not prompts or substring filters.
- Outbound email/content/CRM/social jobs are default-off or draft-only; recipient provenance, purpose, suppression, explicit approval, quota, idempotency, revocation and alerting pass fake-provider tests.
- Payment, ledger, treasury, escrow and contract state reconcile against evidence; P24 liabilities are transfer-backed; roles and signers are separated; independent review is complete for the intended path.
- CI checks are required, reproducible and offline; deployment and migrations are protected; database/queue recovery, restore, incident, alert and kill-switch rehearsals have actual evidence.
- Product catalog, prices, units, product readiness, website copy, contracts and claims agree; customer demand, paid outcomes, margins and renewals are measured rather than inferred from code or token/agent counts.
- Every remaining unknown has an owner and an explicit hold; no report or public claim exceeds the proof actually collected.

**Go/no-go rule:** any failed P0 gate, unexplained payment variance, unknown effect sink, cross-tenant data access, missing owner approval, ineffective kill switch, or missing deployment evidence means **NO-GO for that capability**. A no-go for one capability is not a reason to restore the vulnerable fallback; keep that capability disabled and continue only independent, safe work.

## 11. Builder completion report format

At the end of each phase, return a concise evidence-based report:

1. Tasks done / blocked / deferred with IDs and reasons.  2. Commits and changed files.  3. Tests executed and exact results.  4. Security and effect-inventory changes.  5. Migration, rollback, monitoring, and alert evidence.  6. Owner decisions still required.  7. Explicit statement of what was **not** tested or proven.  8. A request for separate authorization before any external or production action.

---
Generated from the completed seven-workstream, read-only build-planning review. Scope is anchored to the commit above; if upstream changes, revalidate this plan before implementation.
