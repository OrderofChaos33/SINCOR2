"""Workflow state layer: event sourcing, checkpoints, leases, idempotency.

These tests simulate the failure this layer exists for: a worker checks
out mid-workflow (crash / redeploy) and a *different* worker — here, a
brand-new store instance over the same database file — must resume the
run with state intact and without repeating completed side effects.
"""

import sqlite3

import pytest

from sincor2.workflow_state import (
    CheckpointNotFound,
    LeaseHeld,
    LeaseNotHeld,
    RunNotFound,
    SQLiteStateBackend,
    VersionConflict,
    WorkflowStateError,
    WorkflowStateStore,
)


class FakeClock:
    def __init__(self, start=1_000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "workflow_state.db")


@pytest.fixture
def store(db_path, clock):
    s = WorkflowStateStore.open(db_path, clock=clock)
    yield s
    s.close()


def reopen(db_path, clock):
    """A different worker: a fresh store over the same database file."""
    return WorkflowStateStore.open(db_path, clock=clock)


# ---------------------------------------------------------------------------
# Runs and event log
# ---------------------------------------------------------------------------


def test_create_run_appends_genesis_event(store):
    event = store.create_run("run-1", workflow="onboard")
    assert event["seq"] == 1
    assert event["event_type"] == "run_created"
    assert event["prev_hash"] == "0" * 64
    assert store.verify_chain("run-1") is True


def test_duplicate_run_rejected(store):
    store.create_run("run-1")
    with pytest.raises(WorkflowStateError):
        store.create_run("run-1")


def test_unknown_run_raises(store):
    with pytest.raises(RunNotFound):
        store.append_event("nope", "x")
    with pytest.raises(RunNotFound):
        store.verify_chain("nope")


def test_events_are_hash_chained_in_order(store):
    store.create_run("run-1")
    store.apply_patch("run-1", {"a": 1})
    store.apply_patch("run-1", {"b": 2})
    events = store.events("run-1")
    assert [e["seq"] for e in events] == [1, 2, 3]
    assert events[1]["prev_hash"] == events[0]["event_hash"]
    assert events[2]["prev_hash"] == events[1]["event_hash"]
    assert store.verify_chain("run-1") is True


# ---------------------------------------------------------------------------
# Checkpoints + resume across a worker drop
# ---------------------------------------------------------------------------


def test_checkpoint_drop_resume_state_intact(store, db_path, clock):
    store.create_run("run-1", workflow="pipeline")
    store.apply_patch("run-1", {"stage": "screened", "count": 3})
    store.apply_patch("run-1", {"count": 4})
    cp = store.create_checkpoint(
        "run-1", "after-screen",
        {"stage": "screened", "count": 4, "items": ["a", "b"]})
    assert cp.version == 1

    # Worker 1 "drops" here: no cleanup, store abandoned.
    worker2 = reopen(db_path, clock)
    try:
        resumed = worker2.resume("run-1")
        assert resumed.checkpoint is not None
        assert resumed.checkpoint.name == "after-screen"
        assert resumed.state == {
            "stage": "screened", "count": 4, "items": ["a", "b"]}
    finally:
        worker2.close()


def test_resume_replays_events_after_checkpoint(store):
    store.create_run("run-1")
    store.apply_patch("run-1", {"a": 1})
    store.create_checkpoint("run-1", "cp", {"a": 1, "base": True})
    store.apply_patch("run-1", {"b": 2})
    store.apply_patch("run-1", {"a": 10})
    resumed = store.resume("run-1")
    assert resumed.state == {"a": 10, "base": True, "b": 2}
    # Only the two patches after the checkpoint replay (plus the
    # checkpoint_created control event, which the reducer ignores).
    assert resumed.events_replayed == 3


def test_replay_without_checkpoint_reconstructs_state(store):
    store.create_run("run-1")
    store.apply_patch("run-1", {"x": 1, "y": 2})
    store.apply_patch("run-1", {"y": 3})
    store.append_event("run-1", "note", {"text": "audit only"})
    resumed = store.resume("run-1")
    assert resumed.checkpoint is None
    assert resumed.state == {"x": 1, "y": 3}


def test_checkpoint_versions_monotonic_and_listed(store):
    store.create_run("run-1")
    v1 = store.create_checkpoint("run-1", "cp", {"n": 1})
    v2 = store.create_checkpoint("run-1", "cp", {"n": 2})
    other = store.create_checkpoint("run-1", "other", {"n": 9})
    assert (v1.version, v2.version, other.version) == (1, 2, 1)
    listed = store.list_checkpoints("run-1", "cp")
    assert [c.version for c in listed] == [1, 2]
    assert store.load_checkpoint("run-1", "cp").state == {"n": 2}
    assert store.load_checkpoint("run-1", "cp", version=1).state == {"n": 1}


def test_load_missing_checkpoint_raises(store):
    store.create_run("run-1")
    with pytest.raises(CheckpointNotFound):
        store.load_checkpoint("run-1", "nope")


def test_optimistic_concurrency_conflict_raises(store):
    store.create_run("run-1")
    store.create_checkpoint("run-1", "cp", {"n": 1}, expected_version=0)
    # A second writer still holding the stale expected version 0 must
    # not silently overwrite version 1.
    with pytest.raises(VersionConflict):
        store.create_checkpoint("run-1", "cp", {"n": 99},
                                expected_version=0)
    assert store.load_checkpoint("run-1", "cp").state == {"n": 1}
    # The up-to-date writer proceeds.
    cp = store.create_checkpoint("run-1", "cp", {"n": 2},
                                 expected_version=1)
    assert cp.version == 2


def test_checkpoint_state_must_be_json_dict(store):
    store.create_run("run-1")
    with pytest.raises(ValueError):
        store.create_checkpoint("run-1", "cp", {"bad": object()})


# ---------------------------------------------------------------------------
# Idempotent steps
# ---------------------------------------------------------------------------


def test_step_reexecution_does_not_rerun_side_effect(store):
    store.create_run("run-1")
    calls = {"n": 0}

    def side_effect():
        calls["n"] += 1
        return {"tx": "0xabc", "amount": 5}

    first = store.execute_step("run-1", "pay", "pay-1", side_effect)
    second = store.execute_step("run-1", "pay", "pay-1", side_effect)
    assert first == second == {"tx": "0xabc", "amount": 5}
    assert calls["n"] == 1  # the side effect ran exactly once

    # A different idempotency key is a different step: it runs.
    store.execute_step("run-1", "pay", "pay-2", side_effect)
    assert calls["n"] == 2
    assert store.step_result("run-1", "pay-1") == {"tx": "0xabc",
                                                   "amount": 5}
    assert store.step_result("run-1", "never-ran") is None


def test_failed_step_is_not_recorded_and_can_retry(store):
    store.create_run("run-1")
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return "ok"

    with pytest.raises(RuntimeError):
        store.execute_step("run-1", "risky", "k", flaky)
    assert store.step_result("run-1", "k") is None
    assert store.execute_step("run-1", "risky", "k", flaky) == "ok"
    assert calls["n"] == 2


def test_step_result_survives_worker_drop(store, db_path, clock):
    store.create_run("run-1")
    store.execute_step("run-1", "screen", "s-1", lambda: {"pass": True})
    worker2 = reopen(db_path, clock)
    try:
        calls = {"n": 0}

        def should_not_run():
            calls["n"] += 1
            return {"pass": False}

        result = worker2.execute_step("run-1", "screen", "s-1",
                                      should_not_run)
        assert result == {"pass": True}
        assert calls["n"] == 0
    finally:
        worker2.close()


# ---------------------------------------------------------------------------
# Leases
# ---------------------------------------------------------------------------


def test_lease_blocks_other_worker_until_expiry(store, clock):
    store.create_run("run-1")
    lease = store.claim_lease("run-1", "worker-a", ttl_seconds=30)
    assert lease.worker_id == "worker-a"

    # Premature reclaim is refused while the lease is live.
    with pytest.raises(LeaseHeld):
        store.claim_lease("run-1", "worker-b", ttl_seconds=30)
    clock.advance(29)
    with pytest.raises(LeaseHeld):
        store.claim_lease("run-1", "worker-b", ttl_seconds=30)

    # After expiry the dropped worker's run can be reclaimed.
    clock.advance(2)
    reclaimed = store.claim_lease("run-1", "worker-b", ttl_seconds=30)
    assert reclaimed.worker_id == "worker-b"
    assert store.current_lease("run-1").worker_id == "worker-b"


def test_lease_heartbeat_extends_and_release_frees(store, clock):
    store.create_run("run-1")
    store.claim_lease("run-1", "worker-a", ttl_seconds=30)
    clock.advance(25)
    store.heartbeat_lease("run-1", "worker-a", ttl_seconds=30)
    clock.advance(25)  # past the original expiry, inside the extension
    with pytest.raises(LeaseHeld):
        store.claim_lease("run-1", "worker-b", ttl_seconds=30)

    # Only the owner can heartbeat.
    with pytest.raises(LeaseNotHeld):
        store.heartbeat_lease("run-1", "worker-b", ttl_seconds=30)

    # Release makes the run immediately claimable.
    assert store.release_lease("run-1", "worker-a") is True
    assert store.release_lease("run-1", "worker-a") is False
    lease = store.claim_lease("run-1", "worker-b", ttl_seconds=30)
    assert lease.worker_id == "worker-b"


def test_expired_lease_cannot_heartbeat(store, clock):
    store.create_run("run-1")
    store.claim_lease("run-1", "worker-a", ttl_seconds=10)
    clock.advance(11)
    with pytest.raises(LeaseNotHeld):
        store.heartbeat_lease("run-1", "worker-a", ttl_seconds=10)


def test_full_drop_and_reclaim_resume(store, db_path, clock):
    """The headline scenario: worker A leases a run, checkpoints, and
    dies without releasing. Worker B may not touch the run until the
    lease expires; then it reclaims and resumes with state intact and
    completed steps not re-executed."""
    store.create_run("run-1", workflow="multi-archetype")
    store.claim_lease("run-1", "worker-a", ttl_seconds=60)
    store.apply_patch("run-1", {"archetype": "screen", "done": True})
    calls = {"n": 0}

    def side_effect():
        calls["n"] += 1
        return "screened"

    store.execute_step("run-1", "screen", "screen-1", side_effect)
    store.create_checkpoint("run-1", "handoff",
                            {"archetype": "screen", "done": True})
    # worker-a dies here: no release, no heartbeat.

    worker2 = reopen(db_path, clock)
    try:
        with pytest.raises(LeaseHeld):
            worker2.claim_lease("run-1", "worker-b", ttl_seconds=60)
        clock.advance(61)
        worker2.claim_lease("run-1", "worker-b", ttl_seconds=60)
        resumed = worker2.resume("run-1")
        assert resumed.state == {"archetype": "screen", "done": True}
        # The completed step replays from its record, not its effect.
        assert worker2.execute_step("run-1", "screen", "screen-1",
                                    side_effect) == "screened"
        assert calls["n"] == 1
        assert worker2.verify_chain("run-1") is True
    finally:
        worker2.close()


# ---------------------------------------------------------------------------
# Integrity: tamper detection
# ---------------------------------------------------------------------------


def test_tampered_event_fails_verification(store, db_path):
    store.create_run("run-1")
    store.apply_patch("run-1", {"balance": 100})
    assert store.verify_chain("run-1") is True

    # Rewrite history directly in the database: claim a bigger balance.
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE ws_events SET payload = ? WHERE run_id = 'run-1'"
        " AND seq = 2",
        ('{"patch": {"balance": 1000000}}',),
    )
    conn.commit()
    conn.close()

    assert store.verify_chain("run-1") is False


def test_deleted_event_fails_verification(store, db_path):
    store.create_run("run-1")
    store.apply_patch("run-1", {"a": 1})
    store.apply_patch("run-1", {"b": 2})
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM ws_events WHERE run_id = 'run-1' AND seq = 2")
    conn.commit()
    conn.close()
    assert store.verify_chain("run-1") is False


# ---------------------------------------------------------------------------
# Backend pluggability
# ---------------------------------------------------------------------------


def test_store_works_over_explicit_sqlite_backend(tmp_path, clock):
    backend = SQLiteStateBackend(str(tmp_path / "explicit.db"))
    store = WorkflowStateStore(backend, clock=clock)
    store.create_run("run-1")
    store.apply_patch("run-1", {"ok": True})
    assert store.resume("run-1").state == {"ok": True}
    store.close()
