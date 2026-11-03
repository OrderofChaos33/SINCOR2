# Workflow State: Cross-Agent Checkpointing & Distributed State

`src/sincor2/workflow_state.py` — durable state for long multi-step
agent workflows that span archetypes and workers.

## Why

On 2026-11-02 the production marketplace board + pool state did not
survive a redeploy. State held only in process memory is lost on worker
drops, crashes, and restarts, and a half-finished workflow cannot be
resumed by another worker without risking duplicated side effects.
This layer makes workflow state persistent, fault-tolerant, and safe
to hand off.

## Concepts

| Piece | Guarantee |
|---|---|
| **Event log** | Append-only per run. Each event carries `seq`, `prev_hash`, `event_hash` (sha256 over canonical JSON — same style as the escalation audit chain). `verify_chain()` detects tampering, deletion, and reordering. |
| **Checkpoints** | Named snapshots of the run's JSON state dict. Versions are monotonic per (run, name). `expected_version` gives optimistic concurrency: a stale writer gets `VersionConflict`, never a silent overwrite. Each checkpoint records the event `seq` it covers. |
| **Resume** | `resume()` = latest checkpoint state + replay of later events through a reducer (default: `state_patch` events shallow-merge). Callable from a different worker/process than the writer. |
| **Leases** | One worker owns a run at a time. `claim_lease` / `heartbeat_lease` / `release_lease` with TTL expiry. A dropped worker's run is reclaimable **only after** its lease expires — premature reclaim raises `LeaseHeld`. |
| **Idempotent steps** | `execute_step(run, step, key, fn)` runs `fn` at most once per (run, key); the recorded JSON result is returned on re-execution. Failed steps record nothing and may be retried. This is what makes resume-after-crash safe. |

Typical handoff: worker A claims the lease, applies patches, executes
steps, checkpoints — then dies. Worker B waits out (or observes) the
lease expiry, claims, calls `resume()`, and continues; steps A already
completed replay from their records instead of re-running.

## Backend interface

Storage is behind `StateBackend` (abstract). The store logic uses only
storage primitives, so the bundled `SQLiteStateBackend` (stdlib
`sqlite3`, single-host) can be swapped for Postgres/Redis in
multi-host deployments without changing call sites. A backend must
provide:

- run rows: `init_run`, `run_exists`
- events: `last_event`, `insert_event`, `list_events(after_seq)`
- checkpoints: `latest_checkpoint`, `get_checkpoint`,
  `list_checkpoints`, `insert_checkpoint` — must enforce uniqueness of
  (run, name, version)
- leases: `get_lease`, `put_lease`, `delete_lease` (owner-checked)
- steps: `get_step`, `insert_step` — must be insert-if-absent

The two uniqueness requirements are what keep optimistic concurrency
and idempotency correct under racing writers; the SQLite backend gets
them from primary keys.

## Integration path (future, not in this pass)

1. **Task/board store** (`a2a_task_store`): mirror task mutations as
   `state_patch` events under one run per task; checkpoint on status
   transitions so a redeploy replays minutes, not history.
2. **Bounty pool** (`a2a_bounty_pool`): same treatment per pool epoch —
   allocations/spends as events, periodic balance checkpoints, chain
   verification as a conservation cross-check.
3. **Activity engine**: long-running jobs claim a lease per run; on
   restart the new process reclaims expired leases and resumes instead
   of dropping in-flight work.

No production store is rewired yet; this module is the layer they will
plug into.

## Tests

`tests/pytest/test_workflow_state.py` — checkpoint → simulated worker
drop → resume by a fresh store instance; idempotent step re-execution
(invocation counting); lease expiry reclaim + premature-reclaim
refusal; optimistic-concurrency conflict; chain verification +
tamper/deletion detection; event replay reconstruction.
