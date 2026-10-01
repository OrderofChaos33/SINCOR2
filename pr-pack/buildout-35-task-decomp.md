# [w35] Planning: 1000-task swarm decomposition

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-35-task-decomp` (a3c4d01).

## What changed
TASK_DECOMPOSITION.json + summary: exactly 1,000 tasks T0001–T1000 (199 done / 565 pending / 236 blocked at wave time; phases P1–P5 + merge/push/verify/launch). Planning artifact for the swarm.

## Test evidence
Planning wave; counts reflect wave-time state.

## Files touched
- `TASK_DECOMPOSITION.json`
- `TASK_DECOMPOSITION_SUMMARY.md`
- `+ generators`

## Merge-order notes
Merge #35 of 41 — docs/planning group (order flexible).

## Merge-time corrections
None.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
