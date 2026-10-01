# [w04] P1 housekeeping: correct mislabeled canonical paths, fix broken import

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/p1-mislabeled-files` (6f012f1).

## What changed
Fixes mislabeled canonical module paths and one broken import in the contract-net / order-fulfillment code so imports resolve to the real modules.

## Test evidence
34 contract-net/inbound + 50 neighbors green; 3 pre-existing failures verified on fd96801.

## Files touched
- `src/sincor2/contract_net.py`
- `src/sincor2/order_fulfillment.py`
- `src/sincor2/marketplace/contract_net/engine.py`

## Merge-order notes
Merge #2 of 41 — P1 housekeeping block (after w01).

## Merge-time corrections
None.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
