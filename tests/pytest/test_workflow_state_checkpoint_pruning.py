"""Checkpoint retention: old checkpoint versions must be pruned.

Production incident (2026-11-11): every marketplace save INSERTed a new
ws_checkpoints row carrying the ENTIRE board/pool state JSON and nothing
ever deleted old versions, so /data/marketplace_checkpoints.db grew
without bound and filled the 5 GB Railway volume — every production
write then failed with OSError [Errno 28] (No space left on device).

The retention contract pinned here:

* only the latest ``CHECKPOINT_VERSIONS_KEPT`` (3) versions per
  (run_id, name) survive — on BOTH write paths (the atomic
  ``insert_snapshot`` used by save_state_snapshot, and the generic
  ``insert_checkpoint`` used by create_checkpoint);
* pruning never removes the latest version, and the verified restore
  path (``load_state_snapshot`` + ``verify_chain``) is unaffected —
  events are NEVER pruned, the hash chain stays complete;
* ``WorkflowStateStore.prune_checkpoints()`` trims a database that was
  already bloated by the pre-fix code and reports how many rows it
  deleted.
"""

import hashlib
import json
import os
import sqlite3

import pytest

from sincor2.workflow_state import (
    CHECKPOINT_VERSIONS_KEPT,
    CheckpointNotFound,
    WorkflowStateStore,
    load_state_snapshot,
    save_state_snapshot,
)

RUN = "prune-run"
NAME = "snap"


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "checkpoints.db")


@pytest.fixture
def store(db_path):
    s = WorkflowStateStore.open(db_path)
    yield s
    s.close()


def _raw_checkpoint_versions(db_path, run_id=RUN, name=NAME):
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT version FROM ws_checkpoints"
            " WHERE run_id = ? AND name = ? ORDER BY version ASC",
            (run_id, name),
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# Retention on the atomic snapshot path (save_state_snapshot)
# ---------------------------------------------------------------------------


def test_snapshot_retention_keeps_only_latest_three(store, db_path):
    for i in range(1, 11):
        save_state_snapshot(store, RUN, NAME, {"n": i})
    listed = store.list_checkpoints(RUN, NAME)
    assert len(listed) == CHECKPOINT_VERSIONS_KEPT == 3
    assert [c.version for c in listed] == [8, 9, 10]
    # The raw table agrees — the rows are gone, not just filtered.
    assert _raw_checkpoint_versions(db_path) == [8, 9, 10]


def test_latest_snapshot_loads_and_chain_verifies_after_retention(store):
    for i in range(1, 11):
        save_state_snapshot(store, RUN, NAME, {"n": i})
    assert load_state_snapshot(store, RUN, NAME) == {"n": 10}
    # Events are never pruned: the full hash chain still verifies.
    assert store.verify_chain(RUN) is True
    assert len(store.events(RUN)) == 10


def test_retention_never_drops_below_keep_on_few_saves(store):
    save_state_snapshot(store, RUN, NAME, {"n": 1})
    save_state_snapshot(store, RUN, NAME, {"n": 2})
    assert [c.version for c in store.list_checkpoints(RUN, NAME)] == [1, 2]
    assert load_state_snapshot(store, RUN, NAME) == {"n": 2}


# ---------------------------------------------------------------------------
# Retention on the generic checkpoint path (create_checkpoint)
# ---------------------------------------------------------------------------


def test_create_checkpoint_path_also_prunes(store):
    store.create_run(RUN)
    for i in range(1, 11):
        store.create_checkpoint(RUN, NAME, {"n": i})
    listed = store.list_checkpoints(RUN, NAME)
    assert [c.version for c in listed] == [8, 9, 10]
    assert store.load_checkpoint(RUN, NAME).state == {"n": 10}
    # Pruned versions are really gone.
    with pytest.raises(CheckpointNotFound):
        store.load_checkpoint(RUN, NAME, version=7)


# ---------------------------------------------------------------------------
# Mixed names / runs prune independently
# ---------------------------------------------------------------------------


def test_mixed_names_prune_independently(store):
    for i in range(1, 11):
        save_state_snapshot(store, RUN, "board", {"n": i})
        save_state_snapshot(store, RUN, "pool-ledger", {"n": 100 + i})
    board = store.list_checkpoints(RUN, "board")
    pool = store.list_checkpoints(RUN, "pool-ledger")
    assert [c.version for c in board] == [8, 9, 10]
    assert [c.version for c in pool] == [8, 9, 10]
    assert load_state_snapshot(store, RUN, "board") == {"n": 10}
    assert load_state_snapshot(store, RUN, "pool-ledger") == {"n": 110}


# ---------------------------------------------------------------------------
# Growth bound: retained state bytes stay flat as saves accumulate
# ---------------------------------------------------------------------------


def test_checkpoint_state_bytes_stay_bounded(tmp_path):
    big = {"blob": "x" * 50_000}
    serialized = len(json.dumps(big, sort_keys=True))

    db_path = str(tmp_path / "growth.db")
    store = WorkflowStateStore.open(db_path)
    for i in range(40):
        save_state_snapshot(store, RUN, NAME, {**big, "n": i})
    store.close()

    conn = sqlite3.connect(db_path)
    try:
        row_count, state_bytes = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(length(state)), 0)"
            " FROM ws_checkpoints WHERE run_id = ? AND name = ?",
            (RUN, NAME),
        ).fetchone()
    finally:
        conn.close()
    assert row_count == 3
    # Only the 3 retained states' bytes remain — not 40 copies.
    assert state_bytes <= 3 * (serialized + 64)

    # File size also stays flat: freed pages are reused by later
    # inserts (the mechanism that stops production growth). A control
    # DB with only 3 saves of the same state is the yardstick.
    control_path = str(tmp_path / "control.db")
    control = WorkflowStateStore.open(control_path)
    for i in range(3):
        save_state_snapshot(control, RUN, NAME, {**big, "n": i})
    control.close()
    growth_size = os.path.getsize(db_path)
    control_size = os.path.getsize(control_path)
    assert growth_size < control_size * 2
    assert growth_size < serialized * 10  # nowhere near 40 copies (~2 MB)


# ---------------------------------------------------------------------------
# Maintenance: prune_checkpoints() trims an already-bloated legacy DB
# ---------------------------------------------------------------------------


def _legacy_bloat(store, db_path, run_id=RUN, name=NAME, versions=10):
    """Reproduce a pre-fix database: full-state checkpoint rows written
    with NO pruning (the old insert path), each bound to its own
    state_saved event exactly as the old atomic insert wrote them."""
    store.create_run(run_id)
    # Append all binding events first (via the store), then write the
    # checkpoint rows with one raw transaction — interleaving a raw
    # write transaction with store writes would deadlock on the file
    # lock, and the old code's rows were written one committed
    # transaction per save anyway.
    rows = []
    for i in range(1, versions + 1):
        state = {"n": i}
        digest = hashlib.sha256(
            json.dumps(state, sort_keys=True,
                       separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        event = store.append_event(
            run_id, "state_saved",
            {"name": name, "state_sha256": digest})
        rows.append((run_id, name, i, json.dumps(state, sort_keys=True),
                     event["seq"], event["created_at"]))
    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(
            "INSERT INTO ws_checkpoints (run_id, name, version,"
            " state, event_seq, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def test_prune_checkpoints_trims_legacy_bloat(store, db_path):
    _legacy_bloat(store, db_path)
    assert _raw_checkpoint_versions(db_path) == list(range(1, 11))

    deleted = store.prune_checkpoints()

    assert deleted == 7
    assert _raw_checkpoint_versions(db_path) == [8, 9, 10]
    # The verified restore path still loads the latest state and the
    # event chain is untouched.
    assert load_state_snapshot(store, RUN, NAME) == {"n": 10}
    assert store.verify_chain(RUN) is True


def test_prune_checkpoints_respects_keep_argument(store, db_path):
    _legacy_bloat(store, db_path)
    deleted = store.prune_checkpoints(keep=5)
    assert deleted == 5
    assert _raw_checkpoint_versions(db_path) == [6, 7, 8, 9, 10]
    assert load_state_snapshot(store, RUN, NAME) == {"n": 10}


def test_prune_checkpoints_rejects_keep_below_one(store):
    with pytest.raises(ValueError):
        store.prune_checkpoints(keep=0)


def test_prune_checkpoints_noop_when_backend_lacks_support():
    class BareBackend:
        pass

    store = WorkflowStateStore(backend=BareBackend())
    assert store.prune_checkpoints() == 0
