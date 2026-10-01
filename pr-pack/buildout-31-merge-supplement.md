# [w31] Docs: merge plan supplement — fork-sim + audit branches

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-31-merge-supplement` (d0b6294).

## What changed
MERGE_PLAN_SUPPLEMENT.md: extends the merge order to 27 branches (w25 fork-sim harness, w28 audit artifacts) with the full five-branch ledger entry-ID chain.

## Test evidence
Docs-only; no test changes.

## Files touched
- `MERGE_PLAN_SUPPLEMENT.md (94 lines)`

## Merge-order notes
Merge #34 of 41 — docs group (order flexible).

## Merge-time corrections
None.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
