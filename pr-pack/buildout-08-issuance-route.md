# [w08] P2: agent-triggered P24 issuance route + SDK method

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-08-issuance-route` (277a102).

## What changed
Adds the agent-triggered P24 issuance route (POST /v1/a2a/socialfi/issue), the `issuance` rate-limit tier (5/hr, 20/day), and the SDK method. Contracts undeployed; route is wired but issuance cannot fire onchain yet.

## Test evidence
10/10 new wiring + 50/50 neighbors green.

## Files touched
- `src/sincor2/a2a_inbound_market.py`
- `src/sincor2/a2a_rate_limits.py`
- `src/sincor2/a2a_sdk.py`
- `tests/pytest/test_defi_p24_wiring.py`

## Merge-order notes
Merge #6 of 41 — MUST merge BEFORE w16/w48: w48's a2a_rate_limits.py rewrite does not contain the issuance tier; after merging w48, re-add `"issuance": [Window(5, 3600), Window(20, 86400)]`, the `POST /v1/a2a/socialfi/issue` endpoint mapping, and the per-agent keying branch.

## Merge-time corrections
- C5: w38's P24 pre-screen vs this route — keep both or unify; `register` remains the authority.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
