# [w01] P1 housekeeping: correct stale fee-policy and agent-count strings

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/p1-housekeeping-stale-strings` (811404c).

## What changed
Corrects stale strings to the locked policy: 5% platform fee to treasury (no burn), fleet = 48 agents. Touches the fee-policy string in `a2a_integration.py` and the agent-count string in `agent_billing.py`.

## Test evidence
46 smoke/schema + 129 A2A neighbors green; 2 pre-existing hygiene failures verified pre-existing on fd96801.

## Files touched
- `src/sincor2/a2a_integration.py`
- `src/sincor2/agent_billing.py`

## Merge-order notes
Merge #1 of 41 — P1 housekeeping goes first (smallest, mostly disjoint).

## Merge-time corrections
None.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
