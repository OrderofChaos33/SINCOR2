# [w22] P5: honest money-path copy

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-22-money-path-copy` (b7361f5).

## What changed
Honest money-path copy on Axiom/privacy/settlement surfaces: no fabricated metrics, no live-on-Base implications, positive utility framing. Live render check on /axiom + /privacy.

## Test evidence
139/139 green.

## Files touched
- `templates/axiom.html`
- `templates/privacy.html`
- `src/sincor2/settlement.py`
- `src/sincor2/x402_payments.py`
- `src/sincor2/mvp_blueprints/billing.py`
- `src/sincor2/a2a_inbound.py`

## Merge-order notes
Merge #26 of 41 — AFTER w02; on templates/axiom.html conflicts take w22's side and verify no 80%/Uniswap-V4/live-on-Base strings remain (w22's test asserts this).

## Merge-time corrections
None.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
