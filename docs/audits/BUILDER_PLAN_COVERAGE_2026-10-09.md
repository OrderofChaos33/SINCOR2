# Builder Plan Coverage Map — 2026-10-09

**Source plan:** `docs/audits/BUILDER_PLAN_2026-10-09.md` (47 micro-tasks, friend's plan)
**This map:** what our P1/P2 fix branches already cover vs what remains.

## Already covered by xioix/audit-p1-fixes (108 tests green)

| Plan task | Status | Our coverage |
|-----------|--------|--------------|
| OB-01 (outbound schedulers default-off) | ✅ DONE | P1 Batch C: triple-gate, defaults OFF |
| ID-01 (proof-based customer auth) | ✅ DONE | P1 Batch A: identifier-only login disabled (403) |
| RT-03 (remove planner exec / broad reads) | ✅ DONE | P1 Batch C: exec founder-gated, reads sandboxed |
| ID-02 (verified OAuth) | ✅ DONE | P1 Batch A: verified email required |
| ID-03 (onboarding consent) | ✅ DONE | P1 Batch A: email OTP + consent receipts |
| PAY-02 (remove synthetic verifier) | ✅ DONE | P1 Batch B: 0xSIMULATED removed from prod |
| PAY-04 (atomic global claims) | ✅ DONE | P1 Batch B: UNIQUE(tx_hash) + atomic claim |
| PAY-05 (bounded x402 entitlement) | ✅ DONE | P1 Batch B: single-use + expiry + persisted |

## Partially covered (P2 branch in flight)

| Plan task | Status | Gap |
|-----------|--------|-----|
| RT-02 (fail-closed dispatch) | 🟡 PARTIAL | P2 Batch C does vertical_dispatch; queue/inline fallback still open |
| ID-06 (redact secrets, cookies) | 🟡 PARTIAL | P2 covers secret printing + cookie Secure; log allowlist not done |
| ID-04_PAY-08 (entitlement-gated access) | 🟡 PARTIAL | P1 did orders/cancel; vault + pre-account capabilities open |

## Not yet covered — next wave priority

| Plan task | Phase | Why it matters |
|-----------|-------|----------------|
| PAY-01 (canonical durable payment state) | 1 | THE foundation — one payment/order/claim contract for everything |
| RT-04 (typed effect gateway + sink manifest) | 1 | Universal boundary; CI blocks unwrapped sinks |
| PAY-03 (finality + reorg-aware settlement) | 2 | No fulfillment before finality; reorg remediation |
| RT-01_ID-05 (tenant context + API authz matrix) | 1 | Every API gets verified principal/tenant/owner |
| P1_P6 (claim register) | 1 | Every public claim mapped to evidence or downgraded |
| CT-01 (no-live-onchain typed policy) | 0 | Formalize the disarmed stance as a typed policy |

## Deferred (needs founder decisions first)

- ON-02 through ON-06 (onchain): P24 is post-launch v2 per TOA; keep disarmed
- P2–P5 (wedge selection, pricing): founder picks the wedge
- Phase 3–4 (verification, staging): after implementation lands
- OB-02–OB-06 (campaign machinery): only if outbound is ever enabled
