# [w23] P1: doc index + canonical paths + dormant-secret cleanup

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-23-p1-cleanup` (4391476).

## What changed
Doc index (10 files), CANONICAL_PATHS.md, quarantine of the old inline payment verifier, and removal of a dormant DEMO_SECRET. Nothing deleted — quarantine only.

## Test evidence
87/87 green.

## Files touched
- `docs index (10 files)`
- `CANONICAL_PATHS.md`
- `src/sincor2/a2a_inbound*.py`
- `sinc_payment_verifier.py (quarantined)`
- `.gitmodules`
- `foundry.lock`

## Merge-order notes
Merge #27 of 41 — mount() interplay with w17: idempotency guard first, handler registration inside first-mount (§4.3).

## Merge-time corrections
- C1: at merge time DROP w23's root foundry.lock and .gitmodules (stale two-dependency subset / wrong layout — wave 41 finding). Canonical: onchain/foundry.lock.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
