# [w40] Docs: index refresh #2 (waves 32–38 docs)

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-40-docs-index-2` (3f61190 (+ 9dfd75a)).

## What changed
Second docs-index refresh: indexes 5 new docs; 110 relative links verified. SUPERSEDES w33's docs/README.md — merge THIS, not w33.

## Test evidence
Docs-only; 110 links resolve, policy scan clean.

## Files touched
- `docs/README.md`
- `docs/architecture/README.md`

## Merge-order notes
Merge #38 of 41 — docs group; take w40's version over w33's.

## Merge-time corrections
- C2: take wave 40's docs/README.md (supersedes wave 33's). Do NOT merge w33.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
