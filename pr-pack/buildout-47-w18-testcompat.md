# [w47 (replaces w18)] P3: free quota keyed on verified identity (+ create-auth test compat)

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-47-w18-testcompat` (e54aa26 (on 37cad5e)).

## What changed
SECURITY: free quota is keyed on verified identity (ECDSA) — a critical ECDSA flaw was caught and fixed during development. Includes the w06 create-auth test-compat fix (8 tests). Supersedes the original w18 branch — merge THIS branch, not the original.

## Test evidence
21/21 on both trees + 59 neighbors green.

## Files touched
- `src/sincor2/a2a_integration.py`
- `src/sincor2/a2a_sdk.py`
- `+ tests`

## Merge-order notes
Merge #15 of 41 — in w18's slot, back-to-back with w45 so the identity-helper dedup happens in one pass.

## Merge-time corrections
- C3: deduplicate the EIP-191 identity helpers (§4.1) — single _keccak256, one recover function, w45's identity_message_for_send/quote names, w47's QuotaStore Protocol, w45's _reputation_key namespacing.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
