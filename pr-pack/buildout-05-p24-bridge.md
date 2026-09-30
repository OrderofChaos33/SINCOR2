# [w05] P2: P24 issuance bridge — exact calldata, eth_call dry-run, caller-supplied signing

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-05-p24-bridge` (fbf9a6d).

## What changed
Python bridge for the P24 creator-token factory: builds exact contract calldata, dry-runs via eth_call before signing, and delegates signing to a caller-supplied account — the module never imports eth_account and never sees keys. Contracts undeployed; no broadcast.

## Test evidence
12/12 new bridge + 25/25 neighbors green. No-eth_account-import rule enforced by test.

## Files touched
- `NEW src/sincor2/defi/p24/bridge.py`
- `tests/pytest/test_defi_p24_bridge.py`

## Merge-order notes
Merge #5 of 41 — P2 block opens; new-file-only, no conflicts by construction.

## Merge-time corrections
- C10: when w12 lands, add error ZeroCreator() to the embedded ABI JSON in bridge.py at the exact compiled position (verified in the w39 scratch run).

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
