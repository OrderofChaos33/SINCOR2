# [w48 (replaces w16)] P3: rate-limit coverage + SSE auth/throttle (+ create-auth test compat)

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-48-w16-testcompat` (3209dc9 (on f93fd8c)).

## What changed
Rate-limit rewrite with a RateLimitStore Protocol seam, SSE stream auth and throttling (slot-leak found and fixed during development). Includes the w06 create-auth test-compat fix (1 test). Supersedes the original w16 branch — merge THIS branch, not the original.

## Test evidence
Streaming 4/4 + w16 suites 56/56 + neighbors 37/37 green.

## Files touched
- `src/sincor2/a2a_rate_limits.py (rewrite)`
- `src/sincor2/a2a_inbound_market.py`
- `src/sincor2/wardrobe.py`
- `+ tests`

## Merge-order notes
Merge #18 of 41 — in w16's slot, AFTER w08: re-add w08's issuance tier (policy + endpoint mapping + per-agent keying) after merging (§3.3).

## Merge-time corrections
- C4: port w15's 3 enforcer hunks as a SharedStateRateLimitStore adapter implementing w48's Protocol (§4.2) — do NOT take either side wholesale.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
