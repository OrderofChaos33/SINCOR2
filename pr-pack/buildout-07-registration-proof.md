# [w07] P3: A2A registration identity proof — signed re-registration

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-07-registration-proof` (5e73fe2).

## What changed
Signed re-registration: re-registering an agent_id requires a signature proving control of the bound wallet (EIP-191). Unsigned re-registration returns 403. Adversarial self-review done pre-commit.

## Test evidence
9/9 new + 181/181 neighbors green.

## Files touched
- `src/sincor2/a2a_inbound.py`
- `src/sincor2/a2a_inbound_ext.py`
- `src/sincor2/a2a_sdk.py`
- `tests/pytest/test_a2a_registration_proof.py`

## Merge-order notes
Merge #9 of 41 — A2A identity chain opens (w07 → w09 → w06 → w47 → w45).

## Merge-time corrections
- FOUNDER DECISION (blocked): w07 strict vs w32 unsigned grandfathered grace — keep strict (unsigned re-registration 403) or allow w32's grace. The w39 scratch kept w07 strict.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
