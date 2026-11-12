"""Concurrency regressions for workflow-state snapshots (Builder B
rejection of e3bc2e84, Defect 3).

save_state_snapshot used to append the ``state_saved`` event and insert
the checkpoint as two separate, unlocked operations:

* two store instances racing on seq allocation died with raw
  ``sqlite3.IntegrityError: UNIQUE constraint failed: ws_events``;
* two racing first saves died with ``run already exists``;
* an interleave between the append and the checkpoint insert bound the
  checkpoint to the OTHER writer's event, so load_state_snapshot raised
  digest-mismatch and the restore path was bricked.

These tests drive the production patterns — several store instances on
one DB file, a fresh store per save, and threads sharing one instance —
and require: zero errors escaping, an intact hash chain, and a latest
checkpoint that loads and verifies.
"""
from __future__ import annotations

import threading

import pytest

from sincor2.workflow_state import (
    WorkflowStateStore,
    load_state_snapshot,
    save_state_snapshot,
)

RUN = "concurrent-run"
NAME = "snap"


def _run_threads(threads):
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert all(not t.is_alive() for t in threads), "writer thread hung"


def test_multi_instance_threaded_saves(tmp_path):
    """Two store instances on one DB file, 40 threaded saves each:
    no raw sqlite errors, chain verifies, latest snapshot loads."""
    db = str(tmp_path / "shared.db")
    stores = [WorkflowStateStore.open(db), WorkflowStateStore.open(db)]
    errors = []

    def writer(store, tag):
        try:
            for i in range(40):
                save_state_snapshot(store, RUN, NAME, {tag: i})
        except Exception as exc:  # noqa: BLE001 - collected for assertion
            errors.append(exc)

    _run_threads([
        threading.Thread(target=writer, args=(stores[0], "a")),
        threading.Thread(target=writer, args=(stores[1], "b")),
    ])
    assert errors == []
    assert stores[0].verify_chain(RUN) is True
    state = load_state_snapshot(stores[0], RUN, NAME)
    assert state is not None and len(state) == 1
    for s in stores:
        s.close()


def test_fresh_store_per_save_threaded(tmp_path):
    """The production pattern: every save opens its own store instance
    (as the pool/board mirrors do). Racing first saves on a fresh DB
    must both succeed — no 'run already exists', no UNIQUE failures."""
    db = str(tmp_path / "fresh.db")
    errors = []
    barrier = threading.Barrier(2)

    def writer(tag):
        try:
            barrier.wait(timeout=30)
            for i in range(20):
                store = WorkflowStateStore.open(db)
                try:
                    save_state_snapshot(store, RUN, NAME, {tag: i})
                finally:
                    store.close()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    _run_threads([
        threading.Thread(target=writer, args=("a",)),
        threading.Thread(target=writer, args=("b",)),
    ])
    assert errors == []
    store = WorkflowStateStore.open(db)
    try:
        assert store.verify_chain(RUN) is True
        checkpoints = store.list_checkpoints(RUN, NAME)
        # Retention keeps only the latest 3 versions (keep-latest-3,
        # PR #646); the thread-safety contract is unchanged — all 40
        # racing saves landed exactly once, so the retained tail is
        # the final three versions, each bound to its own event.
        assert len(checkpoints) == 3
        versions = [c.version for c in checkpoints]
        assert versions == sorted(versions) and len(set(versions)) == 3
        assert versions[-1] == 40
        assert all(c.event_seq == c.version for c in checkpoints)
        assert checkpoints[-1].state in ({"a": 19}, {"b": 19})
        state = load_state_snapshot(store, RUN, NAME)
        assert state == checkpoints[-1].state
    finally:
        store.close()


def test_single_shared_instance_threaded_saves(tmp_path):
    """Threads sharing ONE store instance: the append+checkpoint pair
    must stay atomic per save, so the latest checkpoint is always
    bound to its own writer's event and loads cleanly."""
    db = str(tmp_path / "single.db")
    store = WorkflowStateStore.open(db)
    errors = []

    def writer(tag):
        try:
            for i in range(40):
                save_state_snapshot(store, RUN, NAME, {tag: i})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    _run_threads([
        threading.Thread(target=writer, args=("a",)),
        threading.Thread(target=writer, args=("b",)),
    ])
    assert errors == []
    assert store.verify_chain(RUN) is True
    checkpoints = store.list_checkpoints(RUN, NAME)
    # Retention keeps only the latest 3 versions (keep-latest-3,
    # PR #646); the atomicity contract is unchanged — all 80 racing
    # saves landed exactly once, so the retained tail is the final
    # three versions, each bound to its own writer's event.
    assert len(checkpoints) == 3
    versions = [c.version for c in checkpoints]
    assert versions == sorted(versions) and len(set(versions)) == 3
    assert versions[-1] == 80
    assert all(c.event_seq == c.version for c in checkpoints)
    assert checkpoints[-1].state in ({"a": 39}, {"b": 39})
    state = load_state_snapshot(store, RUN, NAME)
    assert state == checkpoints[-1].state
    store.close()


def test_concurrent_append_event_never_leaks_sqlite_errors(tmp_path):
    """append_event itself is race-safe across instances: no raw
    sqlite error escapes and the chain stays contiguous."""
    db = str(tmp_path / "append.db")
    stores = [WorkflowStateStore.open(db), WorkflowStateStore.open(db)]
    stores[0].create_run(RUN)
    errors = []

    def writer(store):
        try:
            for _ in range(30):
                store.append_event(RUN, "probe", {})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    _run_threads([
        threading.Thread(target=writer, args=(stores[0],)),
        threading.Thread(target=writer, args=(stores[1],)),
    ])
    assert errors == []
    assert stores[0].verify_chain(RUN) is True
    seqs = [e["seq"] for e in stores[0].events(RUN)]
    assert seqs == list(range(1, 62))  # genesis + 60 appends
    for s in stores:
        s.close()
