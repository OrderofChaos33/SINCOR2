# [w30] P3: shared durable-state adoption + settlement-idempotency residual

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-30-shared-store-adopt` (dc1d324).

## What changed
Shared durable-state adoption guide plus the settlement-idempotency residual: issuance rate tier, wall clock, fail-closed 503, scoped clears, and settle idempotency preserved through the adapter.

## Test evidence
32/32 adapter behavior checks vs real SharedState.

## Files touched
- `docs/architecture/SHARED_STATE_ADOPTION.md (+ adapter code per C4)`

## Merge-order notes
Merge #28 of 41 — merge-time wiring wave.

## Merge-time corrections
- C4: implement the SharedStateRateLimitStore adapter at merge time; preserve issuance rate tier, wall clock, fail-closed 503, scoped clears, settle idempotency.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
