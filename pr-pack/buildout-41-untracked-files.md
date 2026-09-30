# [w41] P1: untracked files resolution

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-41-untracked-files` (172a821).

## What changed
UNTRACKED_FILES_RESOLUTION.md: resolves the deferred-file backlog — README.DRAFT.md stays founder-review deferred (local-only, never in git); MONEY_FLOW.md canonical (wave 23); TOA scripts tracked; REAL FINDING: wave 23's root foundry.lock is a stale 2-dependency subset and its root .gitmodules points at the wrong layout (feeds C1).

## Test evidence
Docs-only; git status verified clean.

## Files touched
- `docs/ops/UNTRACKED_FILES_RESOLUTION.md (69 lines)`

## Merge-order notes
Merge #39 of 41 — docs group (order flexible).

## Merge-time corrections
- C1 (recorded here): drop w23's root foundry.lock + .gitmodules at merge time.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
