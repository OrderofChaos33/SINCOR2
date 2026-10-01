# [w09] P3: A2A heartbeat and side-channel authentication

> Human-gated PR — do not merge autonomously. Base: `main` (fd96801). Source branch: `xioix/buildout-09-heartbeat-auth` (65da954).

## What changed
Authenticates agent heartbeats and side-channel messages (liveness runner) with a shared token. Adversarial self-review done pre-commit.

## Test evidence
16/16 new + 167/167 neighbors green.

## Files touched
- `src/sincor2/a2a_inbound_ext.py`
- `src/sincor2/a2a_sdk.py`
- `liveness/runner.py`
- `+ tests`

## Merge-order notes
Merge #10 of 41 — after w07 (disjoint hunks, same files).

## Merge-time corrections
- DEPLOY: set AGENT_HEARTBEAT_TOKEN in Railway before any deploy of the merged tree.

## Human-gate checklist
- [ ] PR CI (`pr-ci.yml`) green on the branch head
- [ ] Merge in the numbered order above (or confirm order-independence for docs waves)
- [ ] Apply the listed merge-time corrections during the merge, not after
- [ ] Re-run the wave's neighbor suites after merging
- [ ] No deployment, broadcast, money movement, or production-auth change results from merging alone
