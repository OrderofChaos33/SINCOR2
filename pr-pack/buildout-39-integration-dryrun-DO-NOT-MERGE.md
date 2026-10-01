# [w39 — DO NOT MERGE/PUSH] [DO NOT MERGE] Integration dry-run — scratch only

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-39-integration-dryrun` (8b1faee).

## What changed
Single squashed scratch commit merging all 37 branches available at wave time. Final: 42 failed / 2203 passed (24 pre-existing on base; 1 genuine breakage fixed in scratch = C10; 17 test-compat items fixed by w45–w48; 3 pollution-only). SCRATCH ONLY — never push, merge, or deploy. Its findings are recorded in INTEGRATION_REPORT.md and as corrections/fix waves.

## Test evidence
42 failed / 2203 passed / 1 skipped (scratch).

## Files touched
- `INTEGRATION_REPORT.md (worktree root; not for merge)`
- `130 files in scratch tree`

## Merge-order notes
DO NOT MERGE OR PUSH — scratch verification only.

## Merge-time corrections
None.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
