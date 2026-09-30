# [w03] P1 housekeeping: retire/deprecate burn-stats route, end burn narrative

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/p1-burn-stats-retire` (0255ae9).

## What changed
Retires the burn-stats route and ends the deflationary-burn narrative in billing surfaces, consistent with the locked 5%-fee-no-burn policy.

## Test evidence
50 passed; 7 pre-existing failures verified on fd96801.

## Files touched
- `src/sincor2/agent_billing.py`
- `src/sincor2/mvp_blueprints/billing.py`

## Merge-order notes
Merge #3 of 41 — w01 and w03 both edited agent_billing.py:52 with a byte-identical change; git auto-resolves.

## Merge-time corrections
None.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
