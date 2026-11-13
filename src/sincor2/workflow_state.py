"""Durable workflow state: event sourcing, checkpoints, leases, idempotency.

Problem this solves
-------------------
Long multi-step agent workflows span archetypes and workers. On
2026-11-02 the production marketplace board + pool state did not survive
a redeploy: state that lives only in one process's memory is lost on
worker drops, crashes, and restarts, and a half-finished workflow cannot
be safely resumed by another worker without risking duplicated side
effects.

This module is the durable state layer those workflows plug into:

* **Event-sourced runs** — every state change is an append-only event in
  a per-run log. The log is hash-chained (``prev_hash`` / ``event_hash``,
  sha256 over canonical JSON) in the same style as the escalation audit
  chain in :mod:`sincor2.escalation`, with a ``verify_chain`` integrity
  check that detects tampering.
* **Checkpoints** — named, versioned snapshots of a run's JSON state.
  Versions are monotonic per (run, name). Writes accept an
  ``expected_version`` for optimistic concurrency: a stale writer gets
  :class:`VersionConflict`, never a silent overwrite. Only the latest
  ``CHECKPOINT_VERSIONS_KEPT`` versions per (run, name) are retained —
  each row is a full state copy, so unpruned history grows without
  bound; :meth:`WorkflowStateStore.prune_checkpoints` trims databases
  written before retention existed.
* **Resume** — :meth:`WorkflowStateStore.resume` loads the latest
  checkpoint and replays the events recorded after it through a reducer,
  so a *different* worker can pick a run up exactly where the previous
  one stopped.
* **Worker leases** — a run is claimed by one worker at a time. The
  lease carries an expiry; a dropped worker's run can be reclaimed only
  *after* the lease expires (or is explicitly released), never before.
* **Idempotency keys** — step executions are recorded under a
  caller-supplied idempotency key. Re-executing a completed step returns
  the recorded result instead of re-running the side effect, which is
  what makes resume-after-crash safe.

Backend interface
-----------------
Storage sits behind :class:`StateBackend`. The store logic (hash chain,
version checks, lease rules, replay) only uses the backend's storage
primitives, so a Postgres or Redis backend can replace the bundled
:class:`SQLiteStateBackend` (stdlib ``sqlite3``) without touching call
sites. A backend must provide: run rows, event append/read, checkpoint
insert/read, lease get/put/delete, and step get/insert-if-absent — see
the abstract methods below. One correctness note for future backends:
``insert_checkpoint`` must enforce uniqueness of (run, name, version)
and ``insert_step`` must be insert-if-absent, so the store's optimistic
concurrency and idempotency guarantees hold under racing writers (the
SQLite backend gets both from primary keys).

Integration path (later, deliberately NOT done here)
----------------------------------------------------
The board store (``a2a_task_store``), the bounty pool
(``a2a_bounty_pool``), and the activity engine can adopt this layer
incrementally: (1) mirror each store mutation as a ``state_patch`` event
under a run id per task/pool epoch; (2) take periodic checkpoints so
resume is O(events since checkpoint) instead of a full replay; (3) move
worker ownership of long-running engine jobs onto leases so a redeploy
hands runs to the next process instead of dropping them. No production
store is rewired in this pass.
"""

from __future__ import annotations

import abc
import hashlib
import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

__all__ = [
    "CHECKPOINT_VERSIONS_KEPT",
    "GENESIS_PREV_HASH",
    "BOARD_CHECKPOINT_NAME",
    "BOARD_RUN_ID",
    "Checkpoint",
    "Lease",
    "LeaseHeld",
    "LeaseNotHeld",
    "MARKETPLACE_CHECKPOINT_PATH_ENV",
    "POOL_CHECKPOINT_NAME",
    "POOL_RUN_ID",
    "ResumeResult",
    "RunNotFound",
    "CheckpointNotFound",
    "SQLiteStateBackend",
    "StateBackend",
    "VersionConflict",
    "WorkflowStateError",
    "WorkflowStateStore",
    "default_reducer",
    "load_state_snapshot",
    "load_state_snapshot_checkpoint",
    "marketplace_checkpoint_path",
    "save_state_snapshot",
]

#: prev_hash of the first (genesis) event in a run's log: 64 zero hex chars.
GENESIS_PREV_HASH = "0" * 64

#: Bounded retries for cross-connection write races (event seq and
#: checkpoint version allocation). Each attempt re-reads under a fresh
#: transaction; busy_timeout makes contenders queue rather than error,
#: so a handful of attempts suffices in practice — the bound exists so
#: a pathological lock-holder surfaces as WorkflowStateError instead of
#: an infinite loop or a raw sqlite error.
_SNAPSHOT_MAX_ATTEMPTS = 100

#: Checkpoint retention: only the latest this-many versions per
#: (run_id, name) are kept. Every checkpoint row carries a FULL copy of
#: the state JSON, and the marketplace stores snapshot on every save —
#: without pruning, the checkpoint DB grows by one full state per save
#: forever (2026-11-11: marketplace_checkpoints.db filled the 5 GB
#: production volume and every write began failing with Errno 28).
#: Events are NOT pruned: for snapshot runs they carry only small
#: digest payloads, and the hash chain must stay verifiable end-to-end.
CHECKPOINT_VERSIONS_KEPT = 3

_EVENT_PATCH = "state_patch"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class WorkflowStateError(Exception):
    """Base class for workflow-state errors."""


class RunNotFound(WorkflowStateError):
    """The referenced run does not exist."""


class CheckpointNotFound(WorkflowStateError):
    """The referenced checkpoint does not exist."""


class VersionConflict(WorkflowStateError):
    """Optimistic-concurrency check failed: the writer's expected
    version does not match the stored latest version."""


class LeaseHeld(WorkflowStateError):
    """A live (unexpired) lease is held by another worker."""


class LeaseNotHeld(WorkflowStateError):
    """The caller does not hold a live lease for the run."""


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Checkpoint:
    run_id: str
    name: str
    version: int
    state: dict
    #: seq of the last event included in (preceding) this checkpoint.
    event_seq: int
    created_at: float


@dataclass(frozen=True)
class Lease:
    run_id: str
    worker_id: str
    expires_at: float


@dataclass
class ResumeResult:
    state: dict
    checkpoint: Optional[Checkpoint]
    events_replayed: int


# ---------------------------------------------------------------------------
# Hash chain (same canonical style as sincor2.escalation)
# ---------------------------------------------------------------------------


def _canonical_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _event_hash(event_without_hash: dict) -> str:
    return hashlib.sha256(_canonical_bytes(event_without_hash)).hexdigest()


def default_reducer(state: dict, event: dict) -> dict:
    """Default replay reducer: ``state_patch`` events shallow-merge their
    ``patch`` payload into the state; every other event type is ignored
    (it is audit/control data, not state)."""
    if event.get("event_type") == _EVENT_PATCH:
        patch = event.get("payload", {}).get("patch", {})
        if isinstance(patch, dict):
            state.update(patch)
    return state


# ---------------------------------------------------------------------------
# Backend interface
# ---------------------------------------------------------------------------


class StateBackend(abc.ABC):
    """Storage primitives for the workflow-state store.

    Implementations must guarantee: checkpoint (run, name, version)
    uniqueness, and step insert-if-absent semantics.
    """

    @abc.abstractmethod
    def init_run(self, run_id: str, workflow: Optional[str],
                 metadata_json: str, created_at: float) -> None:
        """Insert a run row; raise WorkflowStateError if it exists."""

    @abc.abstractmethod
    def run_exists(self, run_id: str) -> bool:
        ...

    @abc.abstractmethod
    def last_event(self, run_id: str) -> Optional[dict]:
        """Highest-seq event dict for the run, or None."""

    @abc.abstractmethod
    def insert_event(self, event: dict) -> None:
        ...

    @abc.abstractmethod
    def list_events(self, run_id: str, after_seq: int = 0) -> list:
        """All events with seq > after_seq, in seq order."""

    @abc.abstractmethod
    def latest_checkpoint(self, run_id: str,
                          name: Optional[str] = None) -> Optional[dict]:
        """Latest checkpoint row (highest version for ``name``; most
        recently written overall when ``name`` is None)."""

    @abc.abstractmethod
    def get_checkpoint(self, run_id: str, name: str,
                       version: int) -> Optional[dict]:
        ...

    @abc.abstractmethod
    def list_checkpoints(self, run_id: str,
                         name: Optional[str] = None) -> list:
        ...

    @abc.abstractmethod
    def insert_checkpoint(self, checkpoint: dict) -> None:
        """Insert a checkpoint row; (run, name, version) must be unique."""

    @abc.abstractmethod
    def get_lease(self, run_id: str) -> Optional[dict]:
        ...

    @abc.abstractmethod
    def put_lease(self, run_id: str, worker_id: str, expires_at: float,
                  updated_at: float) -> None:
        ...

    @abc.abstractmethod
    def delete_lease(self, run_id: str, worker_id: str) -> bool:
        """Delete the lease only if owned by ``worker_id``."""

    @abc.abstractmethod
    def get_step(self, run_id: str, idempotency_key: str) -> Optional[dict]:
        ...

    @abc.abstractmethod
    def insert_step(self, run_id: str, idempotency_key: str,
                    step_name: str, result_json: str,
                    created_at: float) -> bool:
        """Insert-if-absent; True if this call inserted the row."""

    def close(self) -> None:  # pragma: no cover - trivial default
        pass


_SCHEMA = """
CREATE TABLE IF NOT EXISTS ws_runs (
    run_id     TEXT PRIMARY KEY,
    workflow   TEXT,
    metadata   TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ws_events (
    run_id     TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload    TEXT NOT NULL,
    prev_hash  TEXT NOT NULL,
    event_hash TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (run_id, seq)
);
CREATE TABLE IF NOT EXISTS ws_checkpoints (
    run_id     TEXT NOT NULL,
    name       TEXT NOT NULL,
    version    INTEGER NOT NULL,
    state      TEXT NOT NULL,
    event_seq  INTEGER NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (run_id, name, version)
);
CREATE TABLE IF NOT EXISTS ws_leases (
    run_id     TEXT PRIMARY KEY,
    worker_id  TEXT NOT NULL,
    expires_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ws_steps (
    run_id          TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    step_name       TEXT NOT NULL,
    result          TEXT NOT NULL,
    created_at      REAL NOT NULL,
    PRIMARY KEY (run_id, idempotency_key)
);
"""


def _event_from_row(row: sqlite3.Row) -> dict:
    return {
        "run_id": row["run_id"],
        "seq": row["seq"],
        "event_type": row["event_type"],
        "payload": json.loads(row["payload"]),
        "prev_hash": row["prev_hash"],
        "event_hash": row["event_hash"],
        "created_at": row["created_at"],
    }


def _checkpoint_from_row(row: sqlite3.Row) -> dict:
    return {
        "run_id": row["run_id"],
        "name": row["name"],
        "version": row["version"],
        "state": json.loads(row["state"]),
        "event_seq": row["event_seq"],
        "created_at": row["created_at"],
    }


class SQLiteStateBackend(StateBackend):
    """SQLite-backed storage (stdlib only). One connection guarded by a
    lock; suitable for a single host's workers. ``path=":memory:"`` is
    handy for tests, a file path for real durability."""

    def __init__(self, path: str = ":memory:"):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        # Cross-connection writers (production opens a fresh store per
        # save) must wait for each other's write transactions instead of
        # failing instantly with "database is locked"; the store-level
        # retry loops handle the remaining uniqueness races.
        self._conn.execute("PRAGMA busy_timeout = 30000")
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)

    # -- runs ---------------------------------------------------------
    def init_run(self, run_id, workflow, metadata_json, created_at):
        with self._lock:
            try:
                with self._conn:
                    self._conn.execute(
                        "INSERT INTO ws_runs (run_id, workflow, metadata,"
                        " created_at) VALUES (?, ?, ?, ?)",
                        (run_id, workflow, metadata_json, created_at),
                    )
            except sqlite3.IntegrityError as exc:
                raise WorkflowStateError(
                    f"run already exists: {run_id!r}") from exc

    def run_exists(self, run_id):
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM ws_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            return row is not None

    # -- events -------------------------------------------------------
    def last_event(self, run_id):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM ws_events WHERE run_id = ?"
                " ORDER BY seq DESC LIMIT 1", (run_id,)
            ).fetchone()
            return _event_from_row(row) if row else None

    def insert_event(self, event):
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO ws_events (run_id, seq, event_type, payload,"
                " prev_hash, event_hash, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (event["run_id"], event["seq"], event["event_type"],
                 json.dumps(event["payload"], sort_keys=True),
                 event["prev_hash"], event["event_hash"],
                 event["created_at"]),
            )

    def list_events(self, run_id, after_seq=0):
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM ws_events WHERE run_id = ? AND seq > ?"
                " ORDER BY seq ASC", (run_id, after_seq)
            ).fetchall()
            return [_event_from_row(r) for r in rows]

    # -- checkpoints --------------------------------------------------
    def latest_checkpoint(self, run_id, name=None):
        with self._lock:
            if name is not None:
                row = self._conn.execute(
                    "SELECT * FROM ws_checkpoints WHERE run_id = ?"
                    " AND name = ? ORDER BY version DESC LIMIT 1",
                    (run_id, name),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT * FROM ws_checkpoints WHERE run_id = ?"
                    " ORDER BY created_at DESC, version DESC LIMIT 1",
                    (run_id,),
                ).fetchone()
            return _checkpoint_from_row(row) if row else None

    def get_checkpoint(self, run_id, name, version):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM ws_checkpoints WHERE run_id = ?"
                " AND name = ? AND version = ?",
                (run_id, name, version),
            ).fetchone()
            return _checkpoint_from_row(row) if row else None

    def list_checkpoints(self, run_id, name=None):
        with self._lock:
            if name is not None:
                rows = self._conn.execute(
                    "SELECT * FROM ws_checkpoints WHERE run_id = ?"
                    " AND name = ? ORDER BY version ASC", (run_id, name)
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM ws_checkpoints WHERE run_id = ?"
                    " ORDER BY name ASC, version ASC", (run_id,)
                ).fetchall()
            return [_checkpoint_from_row(r) for r in rows]

    # -- checkpoint retention -----------------------------------------
    def _delete_old_checkpoints(self, run_id, name, keep):
        """Delete all but the latest ``keep`` versions of (run_id, name).

        Caller must hold ``self._lock``; the DELETE runs on this
        connection inside the caller's transaction, so an insert and
        its prune commit or roll back together. The threshold is the
        keep-th highest version: rows strictly below it go. When
        ``keep`` or fewer versions exist the subquery yields NULL and
        nothing is deleted — the latest version is never removed.
        """
        self._conn.execute(
            "DELETE FROM ws_checkpoints"
            " WHERE run_id = ? AND name = ? AND version < ("
            " SELECT version FROM ws_checkpoints"
            " WHERE run_id = ? AND name = ?"
            " ORDER BY version DESC LIMIT 1 OFFSET ?)",
            (run_id, name, run_id, name, keep - 1))

    def prune_checkpoints(self, keep=CHECKPOINT_VERSIONS_KEPT):
        """Delete every checkpoint version beyond the latest ``keep``
        per (run_id, name), across ALL runs and names; returns the
        number of rows deleted. Events are never touched, so every
        run's hash chain stays verifiable. Maintenance for databases
        bloated before insert-time retention existed."""
        if keep < 1:
            raise ValueError("keep must be >= 1")
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM ws_checkpoints WHERE version < ("
                " SELECT c2.version FROM ws_checkpoints AS c2"
                " WHERE c2.run_id = ws_checkpoints.run_id"
                " AND c2.name = ws_checkpoints.name"
                " ORDER BY c2.version DESC LIMIT 1 OFFSET ?)",
                (keep - 1,))
            return cur.rowcount

    def insert_checkpoint(self, checkpoint):
        with self._lock:
            try:
                with self._conn:
                    self._conn.execute(
                        "INSERT INTO ws_checkpoints (run_id, name,"
                        " version, state, event_seq, created_at)"
                        " VALUES (?, ?, ?, ?, ?, ?)",
                        (checkpoint["run_id"], checkpoint["name"],
                         checkpoint["version"],
                         json.dumps(checkpoint["state"], sort_keys=True),
                         checkpoint["event_seq"], checkpoint["created_at"]),
                    )
                    self._delete_old_checkpoints(
                        checkpoint["run_id"], checkpoint["name"],
                        CHECKPOINT_VERSIONS_KEPT)
            except sqlite3.IntegrityError as exc:
                raise VersionConflict(
                    f"checkpoint version already exists: "
                    f"{checkpoint['run_id']!r}/{checkpoint['name']!r}"
                    f" v{checkpoint['version']}") from exc

    # -- atomic snapshots ----------------------------------------------
    def insert_snapshot(self, run_id, workflow, metadata_json, name,
                        state, created_at):
        """Atomically persist one state snapshot: ensure the run row
        exists, append the ``state_saved`` event, and insert the
        checkpoint bound to THAT event's seq — all in a single write
        transaction, so no interleaving (same instance, another
        instance, or a crash between the two writes) can bind the
        checkpoint to another writer's event or lose the pairing.

        Cross-connection races on the (run, seq) / (run, name, version)
        keys surface as IntegrityError after the busy-timeout wait; the
        whole transaction is rolled back by the connection context
        manager and retried with fresh reads. Raw sqlite errors never
        escape: exhaustion raises WorkflowStateError.

        Returns (event, checkpoint_row) in the shapes the store uses.
        """
        payload = {"name": name, "state_sha256": _state_digest(state)}
        last_error = None
        with self._lock:
            for attempt in range(_SNAPSHOT_MAX_ATTEMPTS):
                try:
                    with self._conn:
                        self._conn.execute(
                            "INSERT OR IGNORE INTO ws_runs (run_id,"
                            " workflow, metadata, created_at)"
                            " VALUES (?, ?, ?, ?)",
                            (run_id, workflow, metadata_json, created_at))
                        row = self._conn.execute(
                            "SELECT * FROM ws_events WHERE run_id = ?"
                            " ORDER BY seq DESC LIMIT 1", (run_id,)
                        ).fetchone()
                        last = _event_from_row(row) if row else None
                        seq = (last["seq"] + 1) if last else 1
                        event = {
                            "run_id": run_id,
                            "seq": seq,
                            "event_type": "state_saved",
                            "payload": payload,
                            "prev_hash": (last["event_hash"] if last
                                          else GENESIS_PREV_HASH),
                            "created_at": created_at,
                        }
                        event["event_hash"] = _event_hash(event)
                        self._conn.execute(
                            "INSERT INTO ws_events (run_id, seq,"
                            " event_type, payload, prev_hash, event_hash,"
                            " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (event["run_id"], event["seq"],
                             event["event_type"],
                             json.dumps(event["payload"], sort_keys=True),
                             event["prev_hash"], event["event_hash"],
                             event["created_at"]))
                        crow = self._conn.execute(
                            "SELECT * FROM ws_checkpoints WHERE run_id = ?"
                            " AND name = ? ORDER BY version DESC LIMIT 1",
                            (run_id, name)).fetchone()
                        version = (crow["version"] + 1) if crow else 1
                        checkpoint = {
                            "run_id": run_id,
                            "name": name,
                            "version": version,
                            "state": dict(state),
                            "event_seq": seq,
                            "created_at": created_at,
                        }
                        self._conn.execute(
                            "INSERT INTO ws_checkpoints (run_id, name,"
                            " version, state, event_seq, created_at)"
                            " VALUES (?, ?, ?, ?, ?, ?)",
                            (run_id, name, version,
                             json.dumps(state, sort_keys=True),
                             seq, created_at))
                        # Same transaction: prune old versions so the
                        # insert + prune commit or roll back as a unit.
                        self._delete_old_checkpoints(
                            run_id, name, CHECKPOINT_VERSIONS_KEPT)
                    return event, checkpoint
                except (sqlite3.IntegrityError,
                        sqlite3.OperationalError) as exc:
                    last_error = exc
                    time.sleep(min(0.05, 0.002 * (attempt + 1)))
        raise WorkflowStateError(
            f"snapshot insert for run {run_id!r} name {name!r} kept "
            f"losing write races ({last_error}); nothing was written")

    # -- leases -------------------------------------------------------
    def get_lease(self, run_id):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM ws_leases WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                return None
            return {"run_id": row["run_id"], "worker_id": row["worker_id"],
                    "expires_at": row["expires_at"]}

    def put_lease(self, run_id, worker_id, expires_at, updated_at):
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO ws_leases (run_id, worker_id, expires_at,"
                " updated_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(run_id) DO UPDATE SET"
                " worker_id = excluded.worker_id,"
                " expires_at = excluded.expires_at,"
                " updated_at = excluded.updated_at",
                (run_id, worker_id, expires_at, updated_at),
            )

    def delete_lease(self, run_id, worker_id):
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM ws_leases WHERE run_id = ? AND worker_id = ?",
                (run_id, worker_id),
            )
            return cur.rowcount > 0

    # -- steps --------------------------------------------------------
    def get_step(self, run_id, idempotency_key):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM ws_steps WHERE run_id = ?"
                " AND idempotency_key = ?", (run_id, idempotency_key)
            ).fetchone()
            if row is None:
                return None
            return {"run_id": row["run_id"],
                    "idempotency_key": row["idempotency_key"],
                    "step_name": row["step_name"],
                    "result": json.loads(row["result"]),
                    "created_at": row["created_at"]}

    def insert_step(self, run_id, idempotency_key, step_name, result_json,
                    created_at):
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO ws_steps (run_id, idempotency_key,"
                " step_name, result, created_at) VALUES (?, ?, ?, ?, ?)",
                (run_id, idempotency_key, step_name, result_json,
                 created_at),
            )
            return cur.rowcount > 0

    def close(self):
        with self._lock:
            self._conn.close()


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class WorkflowStateStore:
    """Durable, resumable state for one or many workflow runs.

    ``clock`` is injectable so lease expiry is testable without sleeping.
    """

    def __init__(self, backend: Optional[StateBackend] = None,
                 clock: Callable[[], float] = time.time):
        self._backend = backend if backend is not None \
            else SQLiteStateBackend()
        self._clock = clock
        self._lock = threading.RLock()

    @classmethod
    def open(cls, path: str, clock: Callable[[], float] = time.time):
        """Open a store backed by a SQLite file at ``path``."""
        return cls(SQLiteStateBackend(path), clock=clock)

    def close(self) -> None:
        self._backend.close()

    # -- runs ---------------------------------------------------------
    def create_run(self, run_id: str, workflow: Optional[str] = None,
                   metadata: Optional[dict] = None) -> dict:
        metadata = dict(metadata or {})
        self._backend.init_run(run_id, workflow,
                               json.dumps(metadata, sort_keys=True),
                               self._clock())
        return self.append_event(run_id, "run_created",
                                 {"workflow": workflow, "metadata": metadata})

    def _require_run(self, run_id: str) -> None:
        if not self._backend.run_exists(run_id):
            raise RunNotFound(f"unknown run: {run_id!r}")

    # -- events -------------------------------------------------------
    def append_event(self, run_id: str, event_type: str,
                     payload: Optional[dict] = None) -> dict:
        """Append a hash-chained event to the run's log.

        Seq allocation is read-last-then-insert; with several store
        instances on one backend file another writer can claim the seq
        first. That surfaces as a uniqueness error from the backend and
        is retried with a fresh read (the chain stays intact: the retry
        chains onto the winner's event). Raw backend errors never
        escape — exhaustion raises :class:`WorkflowStateError`.
        """
        payload = dict(payload or {})
        with self._lock:
            self._require_run(run_id)
            last_error = None
            for attempt in range(_SNAPSHOT_MAX_ATTEMPTS):
                last = self._backend.last_event(run_id)
                seq = (last["seq"] + 1) if last else 1
                prev_hash = last["event_hash"] if last else GENESIS_PREV_HASH
                event = {
                    "run_id": run_id,
                    "seq": seq,
                    "event_type": event_type,
                    "payload": payload,
                    "prev_hash": prev_hash,
                    "created_at": self._clock(),
                }
                event["event_hash"] = _event_hash(event)
                try:
                    self._backend.insert_event(event)
                    return event
                except (sqlite3.IntegrityError,
                        sqlite3.OperationalError) as exc:
                    last_error = exc
                    time.sleep(min(0.05, 0.002 * (attempt + 1)))
            raise WorkflowStateError(
                f"append_event for run {run_id!r} kept losing write "
                f"races ({last_error}); nothing was written")

    def events(self, run_id: str, after_seq: int = 0) -> list:
        self._require_run(run_id)
        return self._backend.list_events(run_id, after_seq=after_seq)

    def apply_patch(self, run_id: str, patch: dict) -> dict:
        """Append a ``state_patch`` event (shallow-merged by the default
        reducer on replay)."""
        if not isinstance(patch, dict):
            raise ValueError("patch must be a dict")
        return self.append_event(run_id, _EVENT_PATCH, {"patch": patch})

    def verify_chain(self, run_id: str) -> bool:
        """Recompute the run's hash chain; False on any tampering,
        reordering, or gap."""
        self._require_run(run_id)
        prev_hash = GENESIS_PREV_HASH
        expected_seq = 1
        for event in self._backend.list_events(run_id):
            if event["seq"] != expected_seq:
                return False
            if event["prev_hash"] != prev_hash:
                return False
            content = {k: event[k] for k in
                       ("run_id", "seq", "event_type", "payload",
                        "prev_hash", "created_at")}
            if _event_hash(content) != event["event_hash"]:
                return False
            prev_hash = event["event_hash"]
            expected_seq += 1
        return True

    # -- checkpoints --------------------------------------------------
    def create_checkpoint(self, run_id: str, name: str, state: dict,
                          expected_version: Optional[int] = None,
                          event_seq: Optional[int] = None
                          ) -> Checkpoint:
        """Snapshot ``state`` under ``name``. Version is latest+1 for the
        (run, name) pair. If ``expected_version`` is given it must equal
        the current latest version (0 when none exists), else
        :class:`VersionConflict` is raised and nothing is written.

        ``event_seq`` pins the checkpoint to a specific event (the one
        its own writer appended). When omitted, the checkpoint binds to
        whatever the run's last event is at insert time — fine for a
        single writer, wrong under concurrency, where another writer's
        event can land in between (snapshot writers must pass it; see
        :meth:`save_snapshot`)."""
        if not isinstance(state, dict):
            raise ValueError("checkpoint state must be a dict")
        try:
            json.dumps(state)  # must be JSON-serializable
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "checkpoint state must be JSON-serializable") from exc
        with self._lock:
            self._require_run(run_id)
            latest = self._backend.latest_checkpoint(run_id, name)
            current = latest["version"] if latest else 0
            if expected_version is not None and expected_version != current:
                raise VersionConflict(
                    f"checkpoint {name!r} for run {run_id!r}: expected "
                    f"version {expected_version}, found {current}")
            if event_seq is not None:
                bound_seq = event_seq
            else:
                last = self._backend.last_event(run_id)
                bound_seq = last["seq"] if last else 0
            row = {
                "run_id": run_id,
                "name": name,
                "version": current + 1,
                "state": dict(state),
                "event_seq": bound_seq,
                "created_at": self._clock(),
            }
            self._backend.insert_checkpoint(row)
            self.append_event(run_id, "checkpoint_created",
                              {"name": name, "version": row["version"],
                               "event_seq": row["event_seq"]})
            return Checkpoint(**row)

    def list_checkpoints(self, run_id: str,
                         name: Optional[str] = None) -> list:
        self._require_run(run_id)
        return [Checkpoint(**row) for row in
                self._backend.list_checkpoints(run_id, name)]

    def prune_checkpoints(self, keep: int = CHECKPOINT_VERSIONS_KEPT
                          ) -> int:
        """Trim stored checkpoints to the latest ``keep`` versions per
        (run, name) across ALL runs; returns the number of rows
        deleted. Maintenance for databases bloated before insert-time
        retention existed — new writes prune themselves. Events are
        never pruned, so every run's hash chain stays verifiable and
        the verified restore path is unaffected. Backends without a
        prune primitive (cf. the optional ``insert_snapshot``) make
        this a no-op returning 0."""
        if keep < 1:
            raise ValueError("keep must be >= 1")
        pruner = getattr(self._backend, "prune_checkpoints", None)
        if pruner is None:
            return 0
        return pruner(keep)

    def load_checkpoint(self, run_id: str, name: Optional[str] = None,
                        version: Optional[int] = None) -> Checkpoint:
        self._require_run(run_id)
        if version is not None:
            if name is None:
                raise ValueError("name is required when version is given")
            row = self._backend.get_checkpoint(run_id, name, version)
        else:
            row = self._backend.latest_checkpoint(run_id, name)
        if row is None:
            raise CheckpointNotFound(
                f"no checkpoint for run {run_id!r} name={name!r}"
                f" version={version!r}")
        return Checkpoint(**row)

    # -- resume -------------------------------------------------------
    def resume(self, run_id: str, name: Optional[str] = None,
               reducer: Callable[[dict, dict], dict] = default_reducer
               ) -> ResumeResult:
        """Reconstruct run state: latest checkpoint (optionally the
        latest for ``name``) plus replay of every later event through
        ``reducer``. Safe to call from a different worker/process than
        the one that wrote the state."""
        self._require_run(run_id)
        try:
            checkpoint = self.load_checkpoint(run_id, name)
            state = dict(checkpoint.state)
            after_seq = checkpoint.event_seq
        except CheckpointNotFound:
            checkpoint = None
            state = {}
            after_seq = 0
        replayed = self._backend.list_events(run_id, after_seq=after_seq)
        for event in replayed:
            state = reducer(state, event)
        return ResumeResult(state=state, checkpoint=checkpoint,
                            events_replayed=len(replayed))

    # -- leases -------------------------------------------------------
    def claim_lease(self, run_id: str, worker_id: str,
                    ttl_seconds: float = 30.0) -> Lease:
        """Claim (or renew, if already ours) the run's lease. Raises
        :class:`LeaseHeld` while another worker's lease is unexpired —
        a dropped worker's run is reclaimable only after expiry."""
        with self._lock:
            self._require_run(run_id)
            now = self._clock()
            existing = self._backend.get_lease(run_id)
            if (existing and existing["worker_id"] != worker_id
                    and existing["expires_at"] > now):
                raise LeaseHeld(
                    f"run {run_id!r} is leased by "
                    f"{existing['worker_id']!r} until "
                    f"{existing['expires_at']}")
            expires = now + ttl_seconds
            self._backend.put_lease(run_id, worker_id, expires, now)
            return Lease(run_id=run_id, worker_id=worker_id,
                         expires_at=expires)

    def heartbeat_lease(self, run_id: str, worker_id: str,
                        ttl_seconds: float = 30.0) -> Lease:
        """Extend our lease. Raises :class:`LeaseNotHeld` if we do not
        hold a live lease (never held, released, or already expired)."""
        with self._lock:
            self._require_run(run_id)
            now = self._clock()
            existing = self._backend.get_lease(run_id)
            if (not existing or existing["worker_id"] != worker_id
                    or existing["expires_at"] <= now):
                raise LeaseNotHeld(
                    f"worker {worker_id!r} holds no live lease on "
                    f"run {run_id!r}")
            expires = now + ttl_seconds
            self._backend.put_lease(run_id, worker_id, expires, now)
            return Lease(run_id=run_id, worker_id=worker_id,
                         expires_at=expires)

    def release_lease(self, run_id: str, worker_id: str) -> bool:
        """Release our lease; True if a lease of ours was removed.
        Releasing makes the run immediately claimable by anyone."""
        self._require_run(run_id)
        return self._backend.delete_lease(run_id, worker_id)

    def current_lease(self, run_id: str) -> Optional[Lease]:
        self._require_run(run_id)
        row = self._backend.get_lease(run_id)
        if row is None:
            return None
        return Lease(**row)

    # -- idempotent steps ----------------------------------------------
    def execute_step(self, run_id: str, step_name: str,
                     idempotency_key: str,
                     fn: Callable[[], Any]) -> Any:
        """Run ``fn`` at most once per (run, idempotency_key).

        First call: ``fn()`` runs, its JSON-serializable result is
        recorded, and a ``step_completed`` event is appended. Any later
        call with the same key returns the recorded result WITHOUT
        calling ``fn`` again — so resuming a crashed run never repeats a
        completed step's side effect. If ``fn`` raises, nothing is
        recorded and the step may be retried.
        """
        with self._lock:
            self._require_run(run_id)
            existing = self._backend.get_step(run_id, idempotency_key)
            if existing is not None:
                return existing["result"]
            result = fn()
            result_json = json.dumps(result, sort_keys=True)
            inserted = self._backend.insert_step(
                run_id, idempotency_key, step_name, result_json,
                self._clock())
            if not inserted:  # lost a race: another writer recorded it
                recorded = self._backend.get_step(run_id, idempotency_key)
                return recorded["result"]
            self.append_event(run_id, "step_completed",
                              {"step_name": step_name,
                               "idempotency_key": idempotency_key})
            return result

    def step_result(self, run_id: str,
                    idempotency_key: str) -> Optional[Any]:
        """Recorded result for a step, or None if it never completed."""
        self._require_run(run_id)
        row = self._backend.get_step(run_id, idempotency_key)
        return row["result"] if row else None

    def run_exists(self, run_id: str) -> bool:
        """True when a run with this id has been created."""
        return self._backend.run_exists(run_id)

    # -- whole-state snapshots ------------------------------------------
    def save_snapshot(self, run_id: str, name: str, state: dict
                      ) -> Checkpoint:
        """Atomically checkpoint ``state`` under (run_id, name), bound
        to the hash chain: the ``state_saved`` event and the checkpoint
        that references it are written as one unit, and the checkpoint
        is bound to THIS save's event seq — never to whatever event
        another writer appended in between.

        Safe under concurrent writers, including several store
        instances sharing one backend file and racing first saves on a
        fresh database. When the backend offers an atomic
        ``insert_snapshot`` primitive it is used; otherwise the
        append + checkpoint pair is retried as a unit on version
        conflicts, with the checkpoint pinned to its own event.
        """
        if not isinstance(state, dict):
            raise ValueError("snapshot state must be a dict")
        try:
            json.dumps(state)  # must be JSON-serializable
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "snapshot state must be JSON-serializable") from exc
        inserter = getattr(self._backend, "insert_snapshot", None)
        if inserter is not None:
            _event, row = inserter(
                run_id, "marketplace-state",
                json.dumps({"checkpoint_name": name}, sort_keys=True),
                name, dict(state), self._clock())
            return Checkpoint(**row)
        # Generic-backend fallback: ensure the run (tolerating a racing
        # creator), then append + checkpoint with retry. Each attempt
        # appends a fresh event and pins the checkpoint to it, so a
        # version conflict never leaves a mis-bound checkpoint.
        with self._lock:
            if not self.run_exists(run_id):
                try:
                    self.create_run(
                        run_id, workflow="marketplace-state",
                        metadata={"checkpoint_name": name})
                except WorkflowStateError:
                    if not self.run_exists(run_id):
                        raise
            for _attempt in range(_SNAPSHOT_MAX_ATTEMPTS):
                event = self.append_event(
                    run_id, "state_saved",
                    {"name": name, "state_sha256": _state_digest(state)})
                try:
                    return self.create_checkpoint(
                        run_id, name, state, event_seq=event["seq"])
                except VersionConflict:
                    continue
            raise WorkflowStateError(
                f"snapshot for run {run_id!r} name {name!r} kept losing "
                "version races; no checkpoint was written")


# ---------------------------------------------------------------------------
# Whole-state snapshots for the marketplace stores (board + bounty pool)
# ---------------------------------------------------------------------------
#
# The marketplace board (sincor2.a2a_inbound) and the bounty pool
# (sincor2.a2a_bounty_pool) keep JSON ledger files as their warm-boot
# source. Those files are single points of failure: a redeploy that wipes
# the data dir, or a torn write, loses the board and the pool ledger even
# though every mutation was "saved". The helpers below mirror each save
# into a WorkflowStateStore as a checkpoint snapshot bound to the run's
# hash-chained event log, so a store whose JSON file is missing on boot
# can restore its exact last-saved state from the checkpoint — and a
# corrupt or tampered checkpoint fails verification loudly instead of
# silently booting empty.

#: Env var overriding the checkpoint DB location for BOTH marketplace
#: stores (tests point it at a temp dir; production can point it at the
#: persistent volume independent of the legacy JSON paths).
MARKETPLACE_CHECKPOINT_PATH_ENV = "SINCOR_MARKETPLACE_CHECKPOINT_PATH"
MARKETPLACE_CHECKPOINT_FILENAME = "marketplace_checkpoints.db"

#: Run ids / checkpoint names for the two marketplace stores.
BOARD_RUN_ID = "marketplace-board"
BOARD_CHECKPOINT_NAME = "board"
POOL_RUN_ID = "bounty-pool"
POOL_CHECKPOINT_NAME = "pool-ledger"


def marketplace_checkpoint_path(anchor_path: Any = None) -> str:
    """Resolve the shared marketplace checkpoint DB path.

    ``SINCOR_MARKETPLACE_CHECKPOINT_PATH`` wins. Otherwise the DB sits
    next to ``anchor_path`` (the store's own JSON ledger file), falling
    back to the persistent data dir.
    """
    override = os.environ.get(MARKETPLACE_CHECKPOINT_PATH_ENV, "").strip()
    if override:
        return override
    if anchor_path:
        return str(Path(anchor_path).parent / MARKETPLACE_CHECKPOINT_FILENAME)
    try:
        from sincor2.data_paths import data_dir
        return str(data_dir() / MARKETPLACE_CHECKPOINT_FILENAME)
    except Exception:
        return MARKETPLACE_CHECKPOINT_FILENAME


def _state_digest(state: dict) -> str:
    return hashlib.sha256(_canonical_bytes(state)).hexdigest()


def save_state_snapshot(store: WorkflowStateStore, run_id: str,
                        name: str, state: dict) -> Checkpoint:
    """Checkpoint ``state`` under (run_id, name), bound to the hash chain.

    A ``state_saved`` event carrying the state's sha256 digest and the
    checkpoint that references it are written atomically (see
    :meth:`WorkflowStateStore.save_snapshot`), so
    :func:`load_state_snapshot` can prove the checkpoint it loads is the
    one the chained log vouched for — even under concurrent writers.
    """
    return store.save_snapshot(run_id, name, state)


def load_state_snapshot_checkpoint(store: WorkflowStateStore, run_id: str,
                                   name: str) -> Optional[Checkpoint]:
    """Verified latest snapshot checkpoint for (run_id, name).

    Same verification as :func:`load_state_snapshot` (hash chain intact,
    checkpoint bound to its own ``state_saved`` event by digest), but
    returns the :class:`Checkpoint` so callers that must reason about
    freshness can use its monotonic ``version``. Returns None when no
    snapshot exists; raises :class:`WorkflowStateError` on any
    verification failure — boot paths must fail closed.
    """
    if not store.run_exists(run_id):
        return None
    if not store.verify_chain(run_id):
        raise WorkflowStateError(
            f"checkpoint event chain failed verification for run {run_id!r}")
    try:
        checkpoint = store.load_checkpoint(run_id, name)
    except CheckpointNotFound:
        return None
    binding = None
    for event in store.events(run_id):
        if event["seq"] == checkpoint.event_seq:
            binding = event
            break
    if (binding is None or binding.get("event_type") != "state_saved"
            or binding.get("payload", {}).get("name") != name
            or binding.get("payload", {}).get("state_sha256")
            != _state_digest(checkpoint.state)):
        raise WorkflowStateError(
            f"checkpoint {name!r} for run {run_id!r} is not bound to the "
            "verified event chain (state digest mismatch)")
    return checkpoint


def load_state_snapshot(store: WorkflowStateStore, run_id: str,
                        name: str) -> Optional[dict]:
    """Load the latest snapshot state for (run_id, name).

    Returns None when no snapshot exists. Raises
    :class:`WorkflowStateError` when the event chain fails verification
    or the checkpoint is not the state the chain vouched for —
    callers on a boot path must fail closed, never boot empty over a
    tampered checkpoint.
    """
    checkpoint = load_state_snapshot_checkpoint(store, run_id, name)
    return checkpoint.state if checkpoint is not None else None
