# [w42] Docs: merge-plan corrections ledger (C1–C9)

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-42-merge-corrections` (846b3fe).

## What changed
MERGE_CORRECTIONS.md: the 9 merge-time corrections ledger (C1 drop w23 root lockfile/gitmodules; C2 docs index; C3 EIP-191 dedup; C4 shared-state adapters; C5 screening reconcile; C6 ledger entry-ID merge; C7 extended order; C8 replay-design gating; C9 retire inline PaymentVerifier). The integrator's checklist — read before merging anything.

## Test evidence
Docs-only; no test changes.

## Files touched
- `docs/ops/MERGE_CORRECTIONS.md (153 lines)`

## Merge-order notes
Merge #40 of 41 — docs group; merge BEFORE starting the code-wave merges (it is the instruction sheet).

## Merge-time corrections
- C10 (recorded in driver-state, post-dates this doc): w12 ZeroCreator → w05 embedded ABI position.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
