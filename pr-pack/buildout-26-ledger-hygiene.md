# [w26] P4: proof-ledger stale-SKU hygiene

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-26-ledger-hygiene` (8e00a4a).

## What changed
Marks 3 stale proof-ledger entries superseded (by entry-ID lookup, never text-diff) and adds the DeFi ledger SKU canon doc. gates._is_superseded excludes superseded entries from pass checks.

## Test evidence
112/112 green.

## Files touched
- `src/sincor2/defi/gates.py (_is_superseded)`
- `data/defi_product_arm/proof_ledger.json (3 supersede markings + 2 notes)`
- `NEW docs/DEFI_LEDGER_SKU_CANON.md`

## Merge-order notes
Merge #23 of 41 — MUST come after w20/w21/w25 so the supersede markings land on entries that exist.

## Merge-time corrections
- C6: apply supersede markings by entry-ID lookup (ev_11b1b596f771, ev_e27e4f64ef46, ev_0adf75900649); JSON-validate; verify via gates._passing_entries.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
