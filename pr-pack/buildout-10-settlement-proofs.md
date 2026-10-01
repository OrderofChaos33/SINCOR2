# [w10] P3: real settlement receipts — adjudicator-signed proofs

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-10-settlement-proofs` (df6367a).

## What changed
Real settlement receipts: adjudicator-signed proofs produced at settle time and verifiable by the poster. Adversarial self-review done pre-commit.

## Test evidence
19/19 new + 60/60 neighbors green.

## Files touched
- `src/sincor2/a2a_integration.py`
- `src/sincor2/payment_verifier.py`
- `NEW src/sincor2/settlement_proofs.py`
- `onchain/stake_ledger.py`
- `tests/pytest/test_settlement_proofs.py`

## Merge-order notes
Merge #13 of 41 — after w46: hand-merge the adjudicator-signature block around the reconciled amount; the idempotent settle path (w14) must wrap the proof-verified settle, not the reverse.

## Merge-time corrections
- C9: retire the old inline PaymentVerifier at merge time (canonical payment_verifier.py wins).

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
