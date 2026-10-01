# [w02] P1 housekeeping: remove fabricated public claims (CertiK score, stale launch date, live-on-Base pricing)

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/p1-fabricated-claims` (11ab5d5).

## What changed
Removes fabricated/unverifiable public claims from templates and outreach copy: CertiK score assertions, stale launch date, and live-on-Base pricing language. Positive utility framing only.

## Test evidence
14 passed; 2 pre-existing hygiene failures verified pre-existing on fd96801.

## Files touched
- `templates/sitemap.html`
- `templates/whitepaper.html`
- `+ 8 more templates/outreach files`

## Merge-order notes
Merge #4 of 41 — w22 (money-path copy) merges AFTER w02; on templates/axiom.html conflicts take w22's side.

## Merge-time corrections
None.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
