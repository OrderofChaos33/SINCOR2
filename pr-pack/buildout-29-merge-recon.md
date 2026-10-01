# [w29] Docs: merge reconciliation plan for build-out branches

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-29-merge-recon` (d9b47d2).

## What changed
MERGE_PLAN.md: the 25-branch merge order with hunk-level conflict map, resolution notes, and the deployment-config checklist. Planning doc — this PR pack supersedes/extends it.

## Test evidence
Docs-only; no test changes.

## Files touched
- `MERGE_PLAN.md (274 lines)`

## Merge-order notes
Merge #33 of 41 — docs group (order flexible).

## Merge-time corrections
None.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
