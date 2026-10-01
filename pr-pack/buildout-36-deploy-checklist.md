# [w36] Docs: production deploy checklist

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-36-deploy-checklist` (c5fe180).

## What changed
PRODUCTION_DEPLOY_CHECKLIST.md: 43 checks + 16-row environment-variable table; ceremonies ordered (P0 merges → Sepolia auction → P24 → stake/slash); money/key actions marked founder-executed only.

## Test evidence
Docs-only; no test changes.

## Files touched
- `docs/ops/PRODUCTION_DEPLOY_CHECKLIST.md (192 lines)`

## Merge-order notes
Merge #36 of 41 — docs group (order flexible). Stage the env/config checklist BEFORE any deploy of the merged tree.

## Merge-time corrections
None.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
