# [w34] P1: remove inert AGENT_BURN_AUTO dead path

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-34-burn-cleanup` (056cd11).

## What changed
Removes the dead AGENT_BURN_AUTO / _attempt_auto_burn path after confirming zero live callers. 36 lines removed, no behavior change.

## Test evidence
14/14 green.

## Files touched
- `src/sincor2/agent_billing.py (36 deletions)`

## Merge-order notes
Merge #30 of 41 — late code wave; disjoint.

## Merge-time corrections
None.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
