# [w11] P2: P24 issuance agent skill wired to /v1/a2a/socialfi/issue

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-11-issuance-skill` (d23e2eb).

## What changed
Agent skill module for P24 issuance, wired to the w08 route. Skill-side validation and parameter building; signing stays caller-side.

## Test evidence
15/15 new + 70/70 neighbors green.

## Files touched
- `src/sincor2/a2a_integration.py`
- `NEW src/sincor2/defi/p24/skill.py`
- `tests/pytest/test_defi_p24_skill.py`

## Merge-order notes
Merge #7 of 41 — after w08; import-block overlap with w14 at ~(74,74) is trivial (keep both).

## Merge-time corrections
None.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
