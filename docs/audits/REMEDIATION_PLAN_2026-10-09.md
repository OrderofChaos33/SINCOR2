# SINCOR2 Audit Remediation — Master Plan

**Source audit:** `~/workspace/user/files/sincor2_business_code_audit_2026-10-09_0_pfrm.md`
**Audit basis:** clean upstream `main` at `e980ada` · **Cutoff:** 2026-10-09
**Plan status:** P1 fixes in flight (`xioix/audit-p1-fixes`); this doc covers everything else.
**Rule:** every item gets a fix branch + regression tests + adversarial self-review. No item closed without evidence.

---

## Phase 0 — Containment (24–72h, immediate)

If any listed route is deployed and internet-accessible, disable first, fix second.

| # | Action | Owner |
|---|--------|-------|
| 0.1 | Verify which P1 routes are exposed in production (founder verifies safely, preserves logs) | Founder |
| 0.2 | Disable identifier-only customer login until Batch A lands | Founder/ops |
| 0.3 | Disable unauthenticated `/api/orders/<email>` and `/api/cancel-subscription` until Batch A lands | Founder/ops |
| 0.4 | Confirm outreach/content/trading schedulers are OFF in every deployed env; missing config must fail closed | Founder/ops |
| 0.5 | Restrict treasury metrics endpoint pending P2 fix | Founder/ops |

## Phase 1 — P1 fixes (in flight)

Branch: `xioix/audit-p1-fixes` · Coordinator: 3 batches (A: identity ×5, B: payments ×3, C: runtime ×2+1)

| Audit P1 | Batch | Fix summary |
|----------|-------|-------------|
| Identifier-only customer login | A | Require customer password verification; no session on identifier alone |
| Unauthenticated order disclosure | A | JWT required, scoped to own email |
| Unauthenticated subscription cancellation | A | Authenticated owner match required |
| Unverified OAuth email | A | Provider-verified claim required |
| Public onboarding upsert | A | Verified email challenge before any profile write; consent with proof |
| 0xSIMULATED payment bypass | B | Removed from production; test-only injectable bypass |
| x402 entitlement reuse | B | Expiry enforced, single-use consumption, persisted |
| tx-hash reuse across challenges | B | UNIQUE constraint + atomic claim; replay test |
| Scheduler side-effect defaults | C | All send/publish jobs default OFF; explicit opt-in + kill switch |
| agency_kernel exec + broad reads | C | exec removed/gated; _ALLOWED_ROOTS narrowed; no repo root, no /tmp |
| Shadow not universal | — | Tracked separately (Phase 3.2); NOT in P1 batches |

**Exit criteria:** all new regression tests green; adversarial self-review per batch; founder merges.

## Phase 2 — P2 fixes (next dispatch)

Branch: `xioix/audit-p2-fixes` (from e980ada)

| # | Audit P2 | Fix |
|---|----------|-----|
| 2.1 | Task result APIs may be public (`mvp_app.py:125-129`, `task_queue.py:104-115,354-378`) | Authenticated tenant ownership on list/poll; minimize diagnostics in responses |
| 2.2 | Treasury metrics unauthenticated (`mvp_blueprints/__init__.py:86-94`, `monitoring.py:100-142`) | Auth + allowlisted DTOs; founder decides if any of it is intentionally public |
| 2.3 | Admin form login unthrottled (`pages.py:124-154` vs `auth.py:26-27`) | Consistent throttling/backoff on every credential verifier |
| 2.4 | Credential diagnostic prints admin password (`auth_system.py:203-228`) | Remove secret printing; synthetic sentinel only |
| 2.5 | Receipt finality not enforced (`platform_payments.py:592-641,781-856`, `billing.py:76-126`) | Orders stay pending until chain finality depth; reorg remediation defined |
| 2.6 | OFAC screening not on payment path (`billing.py:76-90`, `platform_payments.py:781-913`) | Screen recovered sender on verified transfer; fail closed where legally required; reconcile GUARDRAIL_CATALOG claims with code |
| 2.7 | Vertical policy fallback fail-open (`vertical_dispatch.py:244-249`) | Fail closed on enforcement exceptions; idempotency before any retry |
| 2.8 | Queue config / job state not shared (`task_queue.py:64-88`, `async_tasks.py:34-42`) | Durable shared job storage; backend health/readiness gate; retriable error instead of inline fallback |
| 2.9 | Legacy Stripe event idempotency (`platform_payments.py:101-103`, `billing.py:265-348`) | Persist event IDs with uniqueness; idempotent fulfillment before live webhooks |
| 2.10 | Cookie Secure tied to deployment marker (`mvp_app.py:105,150-163,809-821`) | Tie to effective production/HTTPS config |
| 2.11 | Paid vault auth doesn't check paid status (`billing.py:603-607`) | Confirm canonical paid/active before serving materials; test pending/failed/refunded |

**Exit criteria:** same as Phase 1.

## Phase 3 — Structural (after P1+P2)

These are architecture, not patches. Each gets its own design doc + branch + review.

| # | Item | Plan |
|---|------|------|
| 3.1 | **Durable payment state machine** | One state machine: `created → verified → finalized → uniquely claimed → fulfilled → reconciled`. Covers idempotency, disputes, refunds, reorg handling, accounting evidence. Replaces ad-hoc verification scattered across billing/platform_payments/x402. |
| 3.2 | **Universal shadow effect boundary** | Every effect-capable adapter (email, content, CRM, chain-write, file/process exec, schedulers) routes through one typed, policy-checked intent gateway; missing boundary fails closed. Startup sink inventory; recurring heartbeat; durable alert receipts; CI rule failing on new unwrapped adapters. |
| 3.3 | **Queue durability + effect idempotency** | Shared durable job store; idempotency keys on every effect; retry taxonomy; poison-queue handling; recovery runbook. (Builds on 2.8.) |
| 3.4 | **Polyclaw stub** | Decision: complete the adapter with full controls (dry-run, approval, spend bounds, kill switch, key isolation) OR remove scheduler wiring until complete. No stub with live wiring. Founder decision required. |
| 3.5 | **P24 contracts** | Treat as scaffolding. Before any money: role authorization, transfer-backed accounting, conservation/replay tests, independent review, verified deployment. Deferred to post-launch v2 per TOA decision. |
| 3.6 | **Treasury conversion** | Stays disarmed. Arming requires: simulation, policy approval, on-chain receipt verification. Founder-gated. |

## Phase 4 — Evidence (parallel with Phase 3)

| # | Item | Plan |
|---|------|------|
| 4.1 | **Claim register** | Every public claim ("production-grade", "43 live skills", "24/7", "zero-human", regulated capabilities) mapped to: tested code, deployment proof, customer evidence, owner. Downgrade to "prototype / sandbox / under validation" unless proven. README, product pages, SKU catalog reconciled. |
| 4.2 | **Commercial evidence room** | Verified baseline: cash/runway, signed revenue, collected cash, ARR/MRR, margins by SKU, retention, liabilities, token positions. Separate cash received / on-chain receipt / finalized / fulfilled / realized conversion in every report. No valuation work until this exists. |
| 4.3 | **Sink inventory manifest** | Machine-readable list of every external-effect sink + its boundary coverage. CI enforces it. |

## Phase 5 — Strategic (founder decisions)

The audit's business recommendations need founder calls, not builder work:

1. **First wedge:** one buyer persona, one vertical, one workflow, one integration, one measurable outcome, 90 days. (Audit hypothesis: compliance evidence ops or lead-to-CRM prep — both need customer interviews first.)
2. **Polyclaw:** keep or remove (see 3.4).
3. **Treasury metrics publicity:** intentional public data or not (see 2.2).
4. **Autonomy boundary:** should any outbound/trading/treasury action ever run without human approval? Effect-by-effect limits, not a global switch.
5. **Operating company vs token ecosystem vs regulated activity** boundary.

## Sequencing

```
Phase 0 (containment) ──→ Phase 1 (P1, in flight) ──→ Phase 2 (P2, next)
                                                        │
Phase 3 (structural) ←──────────────────────────────────┘
Phase 4 (evidence) runs parallel with Phase 3
Phase 5 (founder decisions) unblocks 3.4, 2.2, and wedge work
```

## What "done" looks like

- Zero P1/P2 findings reproducible against main.
- Payment state machine + universal effect boundary + claim register all in main.
- Every public claim traceable to evidence.
- Adversarial test suite covering: cross-account access, replay, parallel duplicate payment, reorg/finality, scheduler no-op by default, boundary bypass, data leakage, kill-switch — green in CI.
- Founder has answered the 5 strategic questions.

No phase is closed on vibes. Evidence or it didn't happen.
