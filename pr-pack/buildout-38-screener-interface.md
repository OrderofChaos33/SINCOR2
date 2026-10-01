# [w38] P2: P24 content-policy screener interface (fail-closed)

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-38-screener-interface` (153a126).

## What changed
Pluggable P24 content screener: ContentScreener, DeferredScreener, DenyListScreener. Registry default is deferred/fail-closed; OnboardingAgent preserves the standing deny-list unless explicitly configured. No deployment or key handling.

## Test evidence
18/18 new + 25/25 P24 neighbors green.

## Files touched
- `screener modules (NEW)`
- `tests/pytest/test_p24_screener.py`

## Merge-order notes
Merge #31 of 41 — late code wave.

## Merge-time corrections
- C5: w38 pre-screen + w08 route — keep both or unify; `register` is authority.
- Founder decisions open: Sepolia onchain screener identity, screener key custody, production P24_SCREENER posture, third-party moderation.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
