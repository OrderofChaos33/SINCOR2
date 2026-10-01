# [w12] P2: P24 wiring verification — full-chain tests + Foundry access-control tests

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-12-p24-wiring-tests` (4f87f06).

## What changed
Full-chain P24 wiring tests plus Foundry access-control tests. Adds the `ZeroCreator` guard to CreatorTokenFactory.sol (CONTRACT CHANGE — the Sepolia deployment must use this source). Fixed a real zero-creator bug during development.

## Test evidence
15/15 new wiring + 25/25 neighbors green.

## Files touched
- `onchain/src/p24/CreatorTokenFactory.sol (ZeroCreator guard — CONTRACT CHANGE)`
- `onchain/test/P24AccessControl.t.sol`
- `tests/pytest/test_defi_p24_wiring_full.py`

## Merge-order notes
Merge #8 of 41 — after w11; contract change noted for the Sepolia deploy ceremony.

## Merge-time corrections
- C10: at merge time, add error ZeroCreator() to w05's embedded ABI JSON in src/sincor2/defi/p24/bridge.py at the exact compiled position (verified in w39 scratch).

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
