# [w27] P5: stake/slash contract design + dry-run wiring (undeployed)

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-27-stake-slash-design` (f595ac6).

## What changed
Stake/slash contract design with dry-run wiring. Low-S malleability bug caught during development. Contracts UNDEPLOYED — no broadcast without explicit authorization.

## Test evidence
12/12 new + 85 neighbors green.

## Files touched
- `design docs`
- `tests/pytest/test_stake_slash_bridge.py`
- `+ 5 more`

## Merge-order notes
Merge #32 of 41 — docs/design group (order flexible).

## Merge-time corrections
- No broadcast, no stake funding, no pool allocation without explicit founder authorization.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
