# [w25] P4: fork-simulation harness for DeFi products

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-25-fork-sim-harness` (094ab10).

## What changed
Fork-simulation harness running DeFi product sims against live Base mainnet; read-only, public RPC URL (no keys). 9 honest findings recorded.

## Test evidence
17/17 fork sims green on live Base mainnet.

## Files touched
- `fork-sim harness (NEW)`
- `tests/pytest/test_fork_sim_harness.py`
- `data/defi_product_arm/proof_ledger.json (pure appends)`

## Merge-order notes
Merge #22 of 41 — ledger chain third (pure appends; sits before w26).

## Merge-time corrections
- C6: entry-ID concatenation; JSON-validate.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
