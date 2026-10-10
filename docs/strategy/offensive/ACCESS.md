# Offensive Playbook — Access Control

**Enforcement:** `src/sincor2/offensive_playbook/gate.py` (`PlaybookGate`)
**Tests:** `tests/pytest/test_offplay_gate.py` (9/9 passing)

## What is gated

Reading the framework docs (`00-plan.md`, `00-doctrine.md`, vector docs) is
open to anyone with repo access — they contain doctrine and method, no
targets, no timing, no non-public information.

**Deploying a play is gated.** A play is any offensive action taken under
this program. No play executes without recorded approval.

## Approval paths (either one)

1. **Founder/manager path:** one EIP-191 signature from the founder address
   (`OFFPLAY_FOUNDER_ADDRESS`). Unconditional approve.

2. **Multi-sig agent path:** 3-of-4 signatures from the designated approver
   agents:
   - `E-toa-44` (Temporal Optimization Agent)
   - `E-critic-46` (Critic)
   - `E-strategist-45` (Strategist)
   - `E-treasury-exec-47` (Treasury Executive)

   Each approver signs a domain-separated message over the play hash
   (play_id + action + target_scope + created_at). Signatures are low-s
   enforced — malleability twins are rejected. One approval per agent per
   play. Approval records are hash-chained; tampering breaks the chain and
   voids the approval.

## What the gate guarantees

- No single agent can approve its own play (quorum requires 3 distinct
  approvers; overlapping supervision).
- A signature for play A cannot be replayed as approval for play B (the
  play hash binds action + scope + timestamp).
- The founder can always act alone; the agents can act without the founder
  only together.
- Every approval is auditable: who signed, what exactly, when, in what
  chain order.

## What the gate does NOT do

- It does not hide the docs from repo readers. Git has no per-directory
  read permissions. True secrecy needs a private repo (founder decision).
- It does not execute plays. Execution wiring is separate and stays
  founder-gated.
- Approver signing keys are founder-controlled and registered out-of-band.
  They never appear in chat, code, or docs.

## Changing this policy

Quorum size, approver set, and founder address change only by founder
decision, recorded as a new play approval itself (the gate governs its own
reconfiguration).
