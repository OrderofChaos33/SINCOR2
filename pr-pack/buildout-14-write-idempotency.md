# [w14] P3: write idempotency keys on money-adjacent routes

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-14-write-idempotency` (ee4ed10).

## What changed
Idempotency keys on money-adjacent write routes: a retried request executes exactly once (16-thread race test proves single execution).

## Test evidence
24/24 new + 52 neighbors green.

## Files touched
- `NEW src/sincor2/a2a_idempotency.py`
- `src/sincor2/a2a_inbound_market.py`
- `src/sincor2/a2a_integration.py`
- `tests/pytest/test_a2a_idempotency.py`

## Merge-order notes
Merge #14 of 41 — after w10; quota try_consume (w47) must happen INSIDE the idempotent execution, not before the idempotency check (else retries double-charge quota).

## Merge-time corrections
None.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
