# [w37] Design: payment-transaction replay prevention options

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-37-tx-replay-design` (4e4513a).

## What changed
PAYMENT_TX_REPLAY_DESIGN.md: three options (spent-transaction ledger, sender-wallet binding, combined) with recommendation: combined, phased — Phase A spent ledger first, Phase B sender binding after identity-helper dedup and relayer allowlist. DESIGN ONLY — not implemented.

## Test evidence
Docs-only; no test changes.

## Files touched
- `docs/security/PAYMENT_TX_REPLAY_DESIGN.md (170 lines)`

## Merge-order notes
Merge #37 of 41 — docs group (order flexible).

## Merge-time corrections
- C8: do NOT pre-decide A/B/C at merge time. Phase B must not ship before the EIP-191 helper dedup (C3) and the founder-pinned relayer/forwarder allowlist. Founder decision open: A (combined phased, recommended) / B (ledger only) / C (binding only), plus fail-closed vs fail-open store-outage policy.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
