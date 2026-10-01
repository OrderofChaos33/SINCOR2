# [w06] P3: A2A caller ownership — cancel/read restricted, server-bound poster identity

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-06-caller-ownership` (0d48f37).

## What changed
SECURITY: strict EIP-191 create-auth on task-create (message/send). Cancels and reads are restricted to the owning caller; poster identity is server-bound. The w45/w46/w47/w48 fix waves updated earlier tests to sign — no security relaxation.

## Test evidence
35/35 new + 311 neighbors green (7 pre-existing failures on base).

## Files touched
- `src/sincor2/a2a_inbound_market.py`
- `src/sincor2/a2a_integration.py`
- `src/sincor2/a2a_sdk.py`
- `+ tests`

## Merge-order notes
Merge #11 of 41 — after w09; interleaved hunks with w14/w17/w47/w45 are resolvable by keeping both blocks in merge order.

## Merge-time corrections
None.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
