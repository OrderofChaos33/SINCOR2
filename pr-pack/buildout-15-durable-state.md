# [w15] P3: durable shared state for A2A fabric (Redis + local fallback)

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-15-durable-state` (fdfb33e).

## What changed
Durable shared state for the A2A fabric: Redis backend with a local fallback, SharedState with TTL/scoped clears and fail-closed behavior. 5 self-review bugs fixed pre-commit.

## Test evidence
26/26 new + 58 neighbors green.

## Files touched
- `NEW src/sincor2/a2a_shared_state.py`
- `src/sincor2/a2a_rate_limits.py (3 hunks)`
- `src/sincor2/mvp_app.py`
- `.env.example`
- `tests/pytest/test_a2a_shared_state.py`

## Merge-order notes
Merge #19 of 41 — after w48; port the 3 enforcer hunks onto w48's rewritten module (§4.2).

## Merge-time corrections
- C4: merge-time wiring — build SharedStateRateLimitStore adapter; verify with w15's and w48's suites. DEPLOY: A2A_STATE_STORE / REDIS_URL.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
