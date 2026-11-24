"""W-193: BountyPool cross-instance lost updates.

Two BountyPool instances on one ledger path: the second instance was
constructed before the first instance's writes, so it holds a stale
snapshot.  Its persisting mutators (fund / allocate / release / spend)
must reload under a file lock before mutating/saving, or they erase
the first instance's allocations — the PoC shape: two stale instances
over-allocated 160 AXM against 100 funded while verify_ledger() still
returned ok=True, because the stale save rewrote the journal too.
"""
from __future__ import annotations

import pytest

from sincor2.a2a_bounty_pool import BountyPool, InsufficientPoolFunds


def _funded(tmp_path, reserve=100.0):
    path = str(tmp_path / "pool.json")
    seed = BountyPool(path=path, reserve_axm=reserve)
    seed.fund()
    return path


def _fresh(path, reserve=100.0):
    return BountyPool(path=path, reserve_axm=reserve)


def _live_ids(pool):
    return {a["allocation_id"] for a in pool.status()["allocations"]
            if a["state"] == "allocated"}


def test_allocations_from_two_stale_instances_both_survive(tmp_path):
    path = _funded(tmp_path)
    a = BountyPool(path=path, reserve_axm=100.0)
    b = BountyPool(path=path, reserve_axm=100.0)  # stale: no allocations yet

    ra = a.allocate("task-a", 60.0)
    rb = b.allocate("task-b", 30.0)  # stale save would erase task-a

    fresh = _fresh(path)
    st = fresh.status()
    assert _live_ids(fresh) == {ra["allocation_id"], rb["allocation_id"]}
    assert st["allocated_axm"] == 90.0
    assert st["available_axm"] == 10.0
    assert st["funded_axm"] == 100.0
    assert fresh.verify_ledger()["ok"] is True
    # B's own in-memory state is re-synced from the persisted state.
    assert _live_ids(b) == {ra["allocation_id"], rb["allocation_id"]}


def test_stale_instances_cannot_over_allocate_beyond_funded(tmp_path):
    path = _funded(tmp_path)
    a = BountyPool(path=path, reserve_axm=100.0)
    b = BountyPool(path=path, reserve_axm=100.0)

    a.allocate("task-a", 80.0)
    # B's stale snapshot thinks 100 is available; the fresh state has 20.
    with pytest.raises(InsufficientPoolFunds):
        b.allocate("task-b", 80.0)

    fresh = _fresh(path)
    st = fresh.status()
    assert st["allocated_axm"] == 80.0
    assert st["allocated_axm"] <= st["funded_axm"]
    assert fresh.verify_ledger()["ok"] is True


def test_stale_release_does_not_erase_other_instance_allocation(tmp_path):
    path = _funded(tmp_path)
    a = BountyPool(path=path, reserve_axm=100.0)
    b = BountyPool(path=path, reserve_axm=100.0)

    ra = a.allocate("task-a", 40.0)
    rb = b.allocate("task-b", 30.0)

    # A is stale w.r.t. B's allocation; releasing its own must not
    # erase B's allocation on save.
    a.release(ra["allocation_id"])

    fresh = _fresh(path)
    states = {al["allocation_id"]: al["state"]
              for al in fresh.status()["allocations"]}
    assert states[ra["allocation_id"]] == "released"
    assert states[rb["allocation_id"]] == "allocated"
    assert fresh.status()["allocated_axm"] == 30.0
    assert fresh.verify_ledger()["ok"] is True


def test_stale_save_does_not_resurrect_released_allocation(tmp_path):
    path = _funded(tmp_path)
    seed = BountyPool(path=path, reserve_axm=100.0)
    ra = seed.allocate("task-a", 40.0)

    p = BountyPool(path=path, reserve_axm=100.0)
    q = BountyPool(path=path, reserve_axm=100.0)  # stale: task-a still live

    p.release(ra["allocation_id"])
    rc = q.allocate("task-c", 10.0)  # stale save would resurrect task-a

    fresh = _fresh(path)
    states = {al["allocation_id"]: al["state"]
              for al in fresh.status()["allocations"]}
    assert states[ra["allocation_id"]] == "released"
    assert states[rc["allocation_id"]] == "allocated"
    assert fresh.status()["allocated_axm"] == 10.0
    assert fresh.verify_ledger()["ok"] is True


def test_stale_spend_preserves_other_instance_allocation(tmp_path):
    path = _funded(tmp_path)
    a = BountyPool(path=path, reserve_axm=100.0)
    b = BountyPool(path=path, reserve_axm=100.0)

    ra = a.allocate("task-a", 40.0)
    rb = b.allocate("task-b", 30.0)

    # A is stale w.r.t. B's allocation; spending its own must not
    # erase B's allocation on save.
    a.spend(ra["allocation_id"])

    fresh = _fresh(path)
    st = fresh.status()
    assert st["spent_axm"] == 40.0
    assert st["allocated_axm"] == 30.0
    assert _live_ids(fresh) == {rb["allocation_id"]}
    assert fresh.verify_ledger()["ok"] is True


def test_checkpoint_mirror_reflects_fresh_merged_state(tmp_path, monkeypatch):
    ckpt = tmp_path / "checkpoints.db"
    monkeypatch.setenv("SINCOR_MARKETPLACE_CHECKPOINT_PATH", str(ckpt))
    path = _funded(tmp_path)
    a = BountyPool(path=path, reserve_axm=100.0)
    b = BountyPool(path=path, reserve_axm=100.0)

    ra = a.allocate("task-a", 60.0)
    rb = b.allocate("task-b", 30.0)  # stale save mirrors its snapshot pre-fix

    from sincor2.workflow_state import (
        POOL_CHECKPOINT_NAME,
        POOL_RUN_ID,
        WorkflowStateStore,
        load_state_snapshot,
    )
    store = WorkflowStateStore.open(str(ckpt))
    try:
        state = load_state_snapshot(store, POOL_RUN_ID, POOL_CHECKPOINT_NAME)
    finally:
        store.close()
    assert state is not None
    mirrored = {aid: al["state"] for aid, al in state["allocations"].items()}
    assert mirrored[ra["allocation_id"]] == "allocated"
    assert mirrored[rb["allocation_id"]] == "allocated"

    # And a wipe + checkpoint restore therefore returns BOTH allocations.
    (tmp_path / "pool.json").unlink()
    restored = _fresh(path)
    assert restored._checkpoint_restored is True
    assert _live_ids(restored) == {ra["allocation_id"], rb["allocation_id"]}
    assert restored.verify_ledger()["ok"] is True
