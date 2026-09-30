# [w32] P3: first-registration squatting control

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-32-first-registration` (182fc41).

## What changed
First-registration squatting control: new registrations default to identity 'unverified'; SINCOR_REGISTRATION_PROOF_REQUIRED=1 rejects unsigned registrations (enabling in production is an auth change — founder call); protected names and the sincor- prefix are reserved; transfers require current-owner signature, no admin override. EIP-191 helpers overlap w47/w45 and are deduplicated at merge (C3).

## Test evidence
20/20 new + 101 neighbors green.

## Files touched
- `tests/pytest/test_registration_identity.py`
- `+ 3 source files`

## Merge-order notes
Merge #29 of 41 — late code wave.

## Merge-time corrections
- C3: deduplicate EIP-191 helpers with w47/w45 (single shared helper set).
- FOUNDER DECISION (blocked): w07 strict vs w32 unsigned grandfathered grace.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
