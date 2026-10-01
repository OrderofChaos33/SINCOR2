# [w45 (replaces w24)] P3: reputation keyed on verified identity (+ leaderboard INT64 overflow fix)

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-45-w24-fixes` (d641d0e (on b215d18)).

## What changed
Reputation is keyed on verified identity (priority-spoof caught pre-commit). REAL BUG FIX: ReputationLedger.leaderboard() summed axm_paid_wei as sqlite INTEGER — the route 500s once cumulative settlements exceed ~9.2 AXM; now SUM(CAST(axm_paid_wei AS REAL)) with regression tests. Includes the w06 create-auth test-compat fix (3 tests). Supersedes the original w24 branch — merge THIS branch, not the original.

## Test evidence
21/21 (19 existing + 2 new overflow regression tests) + 86 neighbors green.

## Files touched
- `src/sincor2/a2a_inbound_market.py`
- `src/sincor2/a2a_integration.py`
- `tests/pytest/test_a2a_reputation_identity.py`

## Merge-order notes
Merge #16 of 41 — in w24's slot, back-to-back with w47 (dedup in one pass).

## Merge-time corrections
- C3: identity-helper dedup with w47/w32 (§4.1) — see w47's PR.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
