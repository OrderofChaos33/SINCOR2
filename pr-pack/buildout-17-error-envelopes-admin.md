# [w17] P3: JSON error envelopes + admin credential unification

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-17-error-envelopes-admin` (90f4af8).

## What changed
Uniform JSON error envelopes across A2A routes and unification of admin credentials (single admin credential replaces the bounty-pool admin key).

## Test evidence
15/15 new + 211 neighbors green; 7 pre-existing failures on base.

## Files touched
- `NEW src/sincor2/a2a_errors.py`
- `bounty_pool / inbound_ext / inbound_market / a2a_integration / recovery / sponsored_stake`
- `+ tests`

## Merge-order notes
Merge #17 of 41 — after the A2A core settles; mount() interplay with w23: idempotency guard first, error-handler registration inside the first-mount branch (§4.3).

## Merge-time corrections
- DEPLOY: ADMIN_PASSWORD replaces SINCOR_BOUNTY_POOL_ADMIN_KEY in Railway before deploy.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
