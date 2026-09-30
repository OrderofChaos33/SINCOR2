# [w20] P4: record DeFi invariant tests in proof ledger

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-20-defi-invariant-ledger` (de8c66b).

## What changed
Records DeFi invariant-test evidence in the append-only proof ledger via record_invariant_evidence.py (+19 invariant_test entries). 7 products covered.

## Test evidence
55/55 invariant+fuzz green; 36/36 neighbors green.

## Files touched
- `NEW record_invariant_evidence.py`
- `data/defi_product_arm/proof_ledger.json (+19 entries)`

## Merge-order notes
Merge #20 of 41 — ledger chain opens (w20 → w21 → w25 → w26 → w28).

## Merge-time corrections
- C6: merge proof_ledger.json by ENTRY-ID CONCATENATION, never text-merge; JSON-validate after every edit.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
