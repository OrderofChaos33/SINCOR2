# [w19] P5: fail-closed treasury policy + fee-event listener (executor disarmed)

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-19-axm-fee-listener` (cff2e2d).

## What changed
Fail-closed treasury policy and onchain fee-event listener. The fee conversion executor is built but stays DISARMED — arming is a founder call (item 32). 3 red tests during development were genuine findings, fixed.

## Test evidence
75/75 green.

## Files touched
- `NEW onchain/fee_event_listener.py`
- `src/sincor2/treasury_policy.py (fail-closed)`
- `src/sincor2/fee_conversion_executor.py (DISARMED)`
- `src/sincor2/sadas_orchestrator.py`
- `+ tests`

## Merge-order notes
Merge #25 of 41 — money path; disjoint from the ledger chain.

## Merge-time corrections
- Founder call (item 32): executor arming — do NOT arm at merge time.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
