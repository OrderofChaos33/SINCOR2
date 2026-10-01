# [w46 (replaces w13)] P3: payment amounts read from chain, never from caller claims (+ create-auth test compat)

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-46-w13-testcompat` (c4b672d (on d300450)).

## What changed
MONEY PATH: payment amounts are read from chain state, never trusted from caller claims; fixes a free-quota OverflowError found during development. Includes the w06 create-auth test-compat fix (5 tests learn to sign). Supersedes the original w13 branch — merge THIS branch, not the original.

## Test evidence
20/20 reconciliation + 51/51 neighbors green.

## Files touched
- `src/sincor2/a2a_integration.py`
- `src/sincor2/payment_verifier.py`
- `tests/pytest/test_payment_amount_reconciliation.py`

## Merge-order notes
Merge #12 of 41 — in w13's slot, before w10 (settle-proof wiring vs payment reconciliation).

## Merge-time corrections
- C9: at merge time, retire the old inline PaymentVerifier in favor of canonical payment_verifier.py.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
