# Overlapping Supervision Pattern (D5)

**Owner decision 2026-10-09:** Agent financial operations run under head
orchestration with overlapping supervision.

## Pattern

```
                    ┌─────────────┐
                    │  Founder /  │
                    │  Operator   │
                    └──────┬──────┘
                           │ policy + limits
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │Supervisor│ │Supervisor│ │Supervisor│  ← overlapping:
        │    A     │ │    B     │ │    C     │    no single supervisor
        └────┬─────┘ └────┬─────┘ └────┬─────┘    is a point of failure
             │  watch     │  watch     │  watch   or collusion
             └────────────┼────────────┘
                          ▼
                   ┌──────────────┐
                   │Head Orchestrator│ ← single execution point
                   │    Agent       │    for financial ops
                   └──────┬───────┘
                          │ proposed effects
                          ▼
                   ┌──────────────┐
                   │Shadow Boundary│ ← proposal-only in this process
                   │ (WP0 contract)│
                   └──────────────┘
```

## Rules

1. **Single execution point.** All agent-originated financial operations
   (payment.transfer, trade.swap, contract.call) flow through ONE head
   orchestrator agent. No agent may invoke a value-moving handler directly.

2. **Overlapping supervision.** N ≥ 2 supervisors watch the head
   orchestrator. Each supervisor independently evaluates every proposed
   financial operation against policy (limits, allowlists, risk tiers).

3. **Quorum approval.** A value-moving operation executes only with
   supervisor quorum (default: majority of seated supervisors, minimum 2).
   Single-supervisor approval is NEVER sufficient. Supervisor revocation
   is immediate and does not require quorum (fail-closed on distrust).

4. **Per-action and daily limits in code.** Limits are enforced by the
   dispatch gate (this package), not by supervisor goodwill:
   - per-action amount caps per effect type
   - daily aggregate caps per originator
   - violation → deny + alert (no silent truncation)

5. **No self-supervision.** The head orchestrator cannot approve its own
   operations. A supervisor cannot approve an operation it proposed.

6. **Audit.** Every proposal, supervisor vote, quorum decision, and
   execution/denial is recorded as an immutable PolicyDecision (WP0
   contract) with the supervisor identities attached.

## Enforcement in code (this package)

- `capability_manifest.check_dispatch(kind, originator)`: value-moving
  task kinds require `originator="operator"`. The head orchestrator
  dispatches with operator originator; anything else is rejected.
- `dispatch_gate.gated_run_job`: the manifest check runs before ANY
  handler invocation, on every backend (eager, Celery, thread pool).
- Unknown task kinds fail closed (no manifest = no execution).

## What this does NOT do

- It does not implement the supervisor voting protocol itself (that is
  a WP4 concern, alongside the approval UX).
- It does not set the actual limit values (founder sets per-action/daily
  caps before any financial capability is enabled).
- Value-moving capabilities remain DISABLED until the WP4 owner decisions
  (limits, approval UX, quorum size) are finalized. The scaffold here
  ensures enablement is a policy flip, not a rebuild.
