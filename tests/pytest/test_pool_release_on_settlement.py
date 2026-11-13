"""Bounty-pool accounting: allocate -> settle -> spent, ghost -> released.

Regression coverage for the pool-leak money-path bug: ``allocate`` fires
on task creation, but before this fix ``submit_proof`` never consumed the
allocation and ``expire_stale_assignments`` never released it. Settled
allocations sat "allocated" forever, so ``available`` drained
monotonically toward ``InsufficientPoolFunds`` and tasks silently posted
unbacked.

The fix adds the terminal ``spend`` transition (allocated -> spent,
called from ``submit_proof`` on successful settlement) and releases the
allocation in ``expire_stale_assignments`` for ghosted assignments.
"""
from __future__ import annotations

import os
import time

# Sealed-bid windows are read at sincor2.a2a_inbound_market import time;
# 1000ms is the enforced minimum, so tests exercise the REAL
# commit -> wait -> reveal -> close -> settle path on a short clock.
os.environ["SINCOR_SEALED_COMMIT_WINDOW_MS"] = "1000"
os.environ["SINCOR_SEALED_REVEAL_WINDOW_MS"] = "1000"

import pytest  # noqa: E402

from sincor2 import activity_engine  # noqa: E402
from sincor2.a2a_bounty_pool import (  # noqa: E402
    InsufficientPoolFunds,
    PoolError,
    bounty_pool,
    reset_bounty_pool,
)

COHORT = "axm-engine-bidder-t1,axm-engine-bidder-t2,axm-engine-bidder-t3"
POOL_RESERVE = 100.0


@pytest.fixture
def pool_env(tmp_path, monkeypatch):
    from sincor2.a2a_inbound import reset_fabric
    from sincor2.onchain.stake_ledger import reset_stake_ledger

    reset_fabric()
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    activity_engine.reset_catalog_cache()
    monkeypatch.setenv("SINCOR_LAUNCH_BOUNTY_AXM", str(POOL_RESERVE))
    reset_bounty_pool(path=str(tmp_path / "pool.json"))
    bounty_pool().fund()
    monkeypatch.setenv("SINCOR_ACTIVITY_ENGINE_STATE",
                       str(tmp_path / "ae_state.json"))
    monkeypatch.setenv("SINCOR_ACTIVITY_COHORT_IDS", COHORT)
    monkeypatch.setenv("SINCOR_ACTIVITY_MAX_IN_FLIGHT", "10")
    monkeypatch.setenv("SINCOR_ACTIVITY_MAX_NEW_PER_CYCLE", "1")
    monkeypatch.setenv("SINCOR_ACTIVITY_STAKE_AXM", "10")
    yield tmp_path


def _drive_until_done(task_keys, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        activity_engine.run_cycle(post=False)
        state = activity_engine.load_state()
        if all(k not in state["in_flight"] for k in task_keys):
            return state
        time.sleep(0.2)
    raise TimeoutError(f"tasks did not finish: {task_keys}")


def _drive_until_task_state(task_key, want, timeout=40):
    from sincor2.a2a_inbound import get_fabric
    deadline = time.time() + timeout
    while time.time() < deadline:
        activity_engine.run_cycle(post=False)
        state = activity_engine.load_state()
        tsk_id = state["in_flight"][task_key]["tsk_id"]
        if get_fabric().tasks[tsk_id]["state"] == want:
            return state
        time.sleep(0.2)
    raise TimeoutError(f"task {task_key} never reached {want}")


def _allocs_for(task_id):
    pool = bounty_pool()
    return [a for a in pool._allocations.values()
            if a.get("task_id") == task_id]


# ---------------------------------------------------------------------------
# Full loop: fund -> allocate -> settle -> spent
# ---------------------------------------------------------------------------

def test_settle_spends_allocation_full_loop(pool_env):
    summary = activity_engine.run_cycle()
    assert summary["posted"]
    task_key = summary["posted"][0]
    state = activity_engine.load_state()
    tsk_id = state["in_flight"][task_key]["tsk_id"]

    allocs = _allocs_for(tsk_id)
    assert len(allocs) == 1
    assert allocs[0]["state"] == "allocated"
    bounty = float(allocs[0]["amount_axm"])

    state = _drive_until_done([task_key])
    assert state["settled_count"] == 1

    allocs = _allocs_for(tsk_id)
    assert len(allocs) == 1
    assert allocs[0]["state"] == "spent", \
        f"settled allocation must be spent, got {allocs[0]['state']}"
    assert len(allocs[0]["history"]) == 2  # allocated -> spent

    status = bounty_pool().status()
    assert abs(status["funded_axm"] - POOL_RESERVE) < 1e-9
    assert abs(status["spent_axm"] - bounty) < 1e-9
    assert abs(status["allocated_axm"]) < 1e-9
    # Exact accounting: available = funded - spent - allocated.
    assert abs(status["available_axm"]
               - (POOL_RESERVE - bounty)) < 1e-9


def test_ghost_releases_allocation_full_loop(pool_env):
    from sincor2.a2a_inbound import get_fabric
    from sincor2.a2a_inbound_market import expire_stale_assignments

    summary = activity_engine.run_cycle()
    task_key = summary["posted"][0]
    state = _drive_until_task_state(task_key, "assigned")
    tsk_id = state["in_flight"][task_key]["tsk_id"]
    bounty = float(_allocs_for(tsk_id)[0]["amount_axm"])

    # Winner ghosts: backdate the assignment past its execution deadline.
    get_fabric().tasks[tsk_id]["assigned_at"] = 1
    expired = expire_stale_assignments()
    assert any(t["task_id"] == tsk_id for t in expired)

    allocs = _allocs_for(tsk_id)
    assert len(allocs) == 1
    assert allocs[0]["state"] == "released", \
        f"ghosted allocation must be released, got {allocs[0]['state']}"

    status = bounty_pool().status()
    assert abs(status["spent_axm"]) < 1e-9  # nothing was paid out
    assert abs(status["available_axm"] - POOL_RESERVE) < 1e-9


def test_no_leak_across_many_settlements(pool_env):
    """The money test: N settled auctions must not drain `available`."""
    posted = []
    for _ in range(3):
        summary = activity_engine.run_cycle()
        assert summary["posted"]
        posted.extend(summary["posted"])
    state = _drive_until_done(posted)
    assert state["settled_count"] == 3

    pool = bounty_pool()
    status = pool.status()
    spent_total = sum(float(a.get("amount_axm") or 0)
                      for a in pool._allocations.values()
                      if a.get("state") == "spent")
    assert spent_total > 0
    assert abs(status["spent_axm"] - spent_total) < 1e-9
    assert abs(status["allocated_axm"]) < 1e-9
    assert abs(status["available_axm"]
               - (POOL_RESERVE - spent_total)) < 1e-9
    # The pool still backs new work: allocate the full available amount.
    probe = pool.allocate("tsk_probe", status["available_axm"],
                          reason="leak probe")
    assert probe["state"] == "allocated"
    pool.release(probe["allocation_id"])


# ---------------------------------------------------------------------------
# Direct pool-API behavior
# ---------------------------------------------------------------------------

def test_spend_is_terminal_and_strict(pool_env):
    pool = bounty_pool()
    alloc = pool.allocate("tsk_direct", 10.0, reason="direct")
    aid = alloc["allocation_id"]

    spent = pool.spend(aid)
    assert spent["state"] == "spent"

    with pytest.raises(PoolError):
        pool.spend(aid)  # already spent
    with pytest.raises(PoolError):
        pool.release(aid)  # spent is terminal; release is for allocated
    with pytest.raises(KeyError):
        pool.spend("alloc_nope")
    with pytest.raises(PoolError):
        pool.allocate("tsk_x", 0)  # non-positive still rejected


def test_release_unknown_raises(pool_env):
    pool = bounty_pool()
    with pytest.raises(KeyError):
        pool.release("alloc_nope")


# ---------------------------------------------------------------------------
# Adversarial: break the accounting
# ---------------------------------------------------------------------------

def test_double_submit_proof_spends_once(pool_env):
    """Re-entrant submit_proof is rejected by the task state guard; the
    allocation is spent exactly once."""
    from sincor2.a2a_inbound import get_fabric
    from sincor2.a2a_inbound_market import submit_proof

    summary = activity_engine.run_cycle()
    task_key = summary["posted"][0]
    state = _drive_until_task_state(task_key, "assigned")
    tsk_id = state["in_flight"][task_key]["tsk_id"]
    winner = get_fabric().tasks[tsk_id]["assigned_to"]
    bounty = float(_allocs_for(tsk_id)[0]["amount_axm"])

    proof = submit_proof(tsk_id, winner, "0x" + "ab" * 32)
    assert proof["status"] == "paid"

    with pytest.raises(RuntimeError):
        submit_proof(tsk_id, winner, "0x" + "cd" * 32)  # not assigned anymore

    allocs = _allocs_for(tsk_id)
    assert len(allocs) == 1 and allocs[0]["state"] == "spent"
    assert abs(bounty_pool().status()["spent_axm"] - bounty) < 1e-9


def test_failed_settlement_keeps_allocation(pool_env, monkeypatch):
    """A rejected payout (receipt not ok) must NOT spend: the adjudicator
    may still rule and pay. Allocation stays allocated."""
    from sincor2.a2a_inbound import get_fabric
    from sincor2.a2a_inbound_market import submit_proof
    import sincor2.a2a_inbound_market as mkt

    summary = activity_engine.run_cycle()
    task_key = summary["posted"][0]
    state = _drive_until_task_state(task_key, "assigned")
    tsk_id = state["in_flight"][task_key]["tsk_id"]
    winner = get_fabric().tasks[tsk_id]["assigned_to"]

    real_stage = mkt.stage_payout
    monkeypatch.setattr(
        mkt, "stage_payout",
        lambda **kw: {"ok": False, "staged_tx_digest": None})

    proof = submit_proof(tsk_id, winner, "0x" + "ef" * 32)
    assert proof["status"] == "rejected"
    assert get_fabric().tasks[tsk_id]["state"] == "failed"

    allocs = _allocs_for(tsk_id)
    assert len(allocs) == 1 and allocs[0]["state"] == "allocated"
    assert abs(bounty_pool().status()["spent_axm"]) < 1e-9


def test_concurrent_spend_single_transition(pool_env):
    """Two threads racing spend() -> exactly one wins; no double-spend."""
    import threading
    pool = bounty_pool()
    aid = pool.allocate("tsk_race", 5.0, reason="race")["allocation_id"]
    outcomes = []

    def worker():
        try:
            pool.spend(aid)
            outcomes.append("spent")
        except PoolError:
            outcomes.append("rejected")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert outcomes.count("spent") == 1
    assert outcomes.count("rejected") == 7
    assert abs(pool.status()["spent_axm"] - 5.0) < 1e-9
