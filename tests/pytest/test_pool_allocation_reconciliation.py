"""Bounty-pool allocation reconciliation: the stranded-allocation sweeper.

Regression coverage for the 2026-11-13 accounting defect: the task
lifecycle's pool calls (release on expiry, spend on settle, release
on delete) are fail-safe try/except BY DESIGN, so one evening window
of failures (Nov 12, deploys/restarts) stranded 16 allocations /
26.0 AXM in state "allocated" against tasks that were already
expired, settled, or deleted.  No sweeper existed: pool status counts
every "allocated" record with no join against task liveness, so
available_axm stayed understated and the settled task's stranded
allocation was a double-count hazard.

These tests reproduce the stranded cohort by driving the REAL
lifecycle transitions with the pool transition sabotaged (the exact
failure the fail-safe design swallows), then prove reconciliation
repairs it through the pool's own release/spend paths — exactly,
idempotently, and without touching live allocations.
"""
from __future__ import annotations

import json
import os
import time

# Sealed-bid windows are read at sincor2.a2a_inbound_market import time;
# 1000ms is the enforced minimum, so the settlement test exercises the
# REAL commit -> wait -> reveal -> close -> settle path on a short
# clock (same convention as test_pool_release_on_settlement.py).
os.environ["SINCOR_SEALED_COMMIT_WINDOW_MS"] = "1000"
os.environ["SINCOR_SEALED_REVEAL_WINDOW_MS"] = "1000"

import pytest  # noqa: E402

from sincor2 import activity_engine  # noqa: E402
from sincor2.a2a_bounty_pool import BountyPool, bounty_pool, reset_bounty_pool  # noqa: E402
from sincor2.a2a_pool_reconciliation import reconcile_allocations  # noqa: E402

COHORT = "axm-engine-bidder-t1,axm-engine-bidder-t2,axm-engine-bidder-t3"
POOL_RESERVE = 100.0
POSTER = "recon-poster"


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


def _register_internal_poster():
    """An internal-origin agent record: pool-eligible via the engine
    exemption in poster_pool_eligible (origin is server-stamped at
    registration; here the fixture plays the server)."""
    from sincor2.a2a_inbound import get_fabric

    fabric = get_fabric()
    with fabric.lock:
        fabric.agents[POSTER] = {"agent_id": POSTER, "origin": "internal"}


def _create_backed_task(bounty=3.0):
    from sincor2.a2a_inbound_market import create_task

    snap = create_task("recon-skill", bounty_axm=bounty, poster_id=POSTER)
    assert snap["pool_backed"] is True
    assert snap["pool_allocation_id"]
    return snap


def _alloc(task_id):
    pool = bounty_pool()
    allocs = [a for a in pool._allocations.values()
              if a.get("task_id") == task_id]
    assert len(allocs) == 1
    return allocs[0]


def _conservation_holds(status):
    return abs(status["funded_axm"]
               - (status["spent_axm"] + status["allocated_axm"]
                  + status["available_axm"])) < 1e-9


# ---------------------------------------------------------------------------
# Full loop: backed task -> auction expires with release failing ->
# stranded -> reconciliation releases -> available restored, idempotent
# ---------------------------------------------------------------------------

def test_expired_task_stranded_allocation_reconciled(pool_env, monkeypatch):
    from sincor2.a2a_inbound import get_fabric
    from sincor2.a2a_inbound_market import close_auction

    _register_internal_poster()
    snap = _create_backed_task(bounty=3.0)
    task_id = snap["task_id"]
    assert _alloc(task_id)["state"] == "allocated"
    assert abs(bounty_pool().status()["available_axm"]
               - (POOL_RESERVE - 3.0)) < 1e-9

    # Auction window elapses with zero bids (a state the task
    # legitimately reaches when its first-bid window has been set).
    fabric = get_fabric()
    with fabric.lock:
        fabric.tasks[task_id]["auction_closes_at"] = 1

    # The Nov-12 window: the lifecycle's inline release raises and the
    # fail-safe wrapper swallows it.  The task still expires.
    orig_release = BountyPool.release

    def _boom(self, allocation_id):
        raise RuntimeError("simulated pool failure (Nov-12 window)")

    monkeypatch.setattr(BountyPool, "release", _boom)
    closed = close_auction(task_id)
    assert closed["state"] == "expired"
    monkeypatch.setattr(BountyPool, "release", orig_release)

    # Stranded: task terminal, allocation still "allocated",
    # available still understated by the full bounty.
    assert _alloc(task_id)["state"] == "allocated"
    status = bounty_pool().status()
    assert abs(status["allocated_axm"] - 3.0) < 1e-9
    assert abs(status["available_axm"] - (POOL_RESERVE - 3.0)) < 1e-9

    # Dry run classifies but writes nothing.
    report = reconcile_allocations()
    assert report["dry_run"] is True
    rows = [r for r in report["actions"] if r["task_id"] == task_id]
    assert len(rows) == 1
    assert rows[0]["classification"] == "release"
    assert rows[0]["task_state"] == "expired"
    assert abs(report["totals_axm"]["release"] - 3.0) < 1e-9
    assert _alloc(task_id)["state"] == "allocated"

    # Apply: released through the pool's own path, available restored.
    report = reconcile_allocations(apply=True)
    rows = [r for r in report["actions"] if r["task_id"] == task_id]
    assert rows[0]["applied"] is True and rows[0]["result"] == "released"
    assert _alloc(task_id)["state"] == "released"
    status = bounty_pool().status()
    assert abs(status["allocated_axm"]) < 1e-9
    assert abs(status["available_axm"] - POOL_RESERVE) < 1e-9
    assert _conservation_holds(status)

    # Idempotent: a second sweep finds nothing to do.
    report = reconcile_allocations(apply=True)
    assert report["scanned_allocations"] == 0
    assert all(v == 0 for v in report["totals_axm"].values())
    assert abs(bounty_pool().status()["available_axm"] - POOL_RESERVE) < 1e-9


# ---------------------------------------------------------------------------
# Full loop (activity engine): settle completes, spend fails ->
# stranded settled allocation -> reconciliation spends, no double count
# ---------------------------------------------------------------------------

def _drive_until_done(task_keys, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        activity_engine.run_cycle(post=False)
        state = activity_engine.load_state()
        if all(k not in state["in_flight"] for k in task_keys):
            return state
        time.sleep(0.2)
    raise TimeoutError(f"tasks did not finish: {task_keys}")


def test_settled_task_stranded_allocation_reconciled(pool_env, monkeypatch):
    orig_spend = BountyPool.spend

    def _boom(self, allocation_id):
        raise RuntimeError("simulated pool failure (Nov-12 window)")

    monkeypatch.setattr(BountyPool, "spend", _boom)
    summary = activity_engine.run_cycle()
    assert summary["posted"]
    task_key = summary["posted"][0]
    state = activity_engine.load_state()
    task_id = state["in_flight"][task_key]["tsk_id"]
    alloc = _alloc(task_id)
    bounty = float(alloc["amount_axm"])

    state = _drive_until_done([task_key])
    assert state["settled_count"] == 1
    monkeypatch.setattr(BountyPool, "spend", orig_spend)

    # Settlement completed and recorded its payout, but the spend was
    # swallowed: the allocation is stranded in "allocated" — the
    # double-count hazard from the defect report.
    from sincor2.a2a_inbound import get_fabric
    task = get_fabric().tasks[task_id]
    assert task["state"] == "settled"
    assert task["payout_axm"] is not None
    assert _alloc(task_id)["state"] == "allocated"
    status = bounty_pool().status()
    assert abs(status["spent_axm"]) < 1e-9
    assert abs(status["allocated_axm"] - bounty) < 1e-9

    # Reconcile: spend through the pool's own settle transition.
    report = reconcile_allocations(apply=True)
    rows = [r for r in report["actions"] if r["task_id"] == task_id]
    assert len(rows) == 1
    assert rows[0]["classification"] == "spend"
    assert rows[0]["result"] == "spent"
    assert _alloc(task_id)["state"] == "spent"

    status = bounty_pool().status()
    assert abs(status["spent_axm"] - bounty) < 1e-9
    assert abs(status["allocated_axm"]) < 1e-9
    assert _conservation_holds(status)
    assert bounty_pool().verify_ledger()["ok"] is True

    # Idempotent, and the paid bounty is never counted twice.
    report = reconcile_allocations(apply=True)
    assert report["scanned_allocations"] == 0
    assert abs(bounty_pool().status()["spent_axm"] - bounty) < 1e-9


# ---------------------------------------------------------------------------
# Deleted task: record gone, allocation stranded -> release
# ---------------------------------------------------------------------------

def test_deleted_task_stranded_allocation_reconciled(pool_env):
    from sincor2.a2a_inbound import get_fabric

    _register_internal_poster()
    snap = _create_backed_task(bounty=2.5)
    task_id = snap["task_id"]

    # The admin-delete end state when its inline release failed: the
    # row is gone, the allocation remains.
    fabric = get_fabric()
    with fabric.lock:
        fabric.tasks.pop(task_id, None)

    report = reconcile_allocations(apply=True)
    rows = [r for r in report["actions"] if r["task_id"] == task_id]
    assert len(rows) == 1
    assert rows[0]["classification"] == "release"
    assert rows[0]["task_state"] is None
    assert _alloc(task_id)["state"] == "released"
    assert abs(bounty_pool().status()["available_axm"] - POOL_RESERVE) < 1e-9


# ---------------------------------------------------------------------------
# Guards: live allocations untouched; settled-without-payout is
# flagged for manual review and NEVER auto-spent
# ---------------------------------------------------------------------------

def test_live_and_unproven_settled_allocations_untouched(pool_env):
    from sincor2.a2a_inbound import get_fabric

    _register_internal_poster()
    live = _create_backed_task(bounty=4.0)
    suspect = _create_backed_task(bounty=2.0)

    # A "settled" task with no payout evidence: the settle paths stamp
    # payout_axm in the same lock block as the state flip, so this
    # state is one the code never produces — flag, never spend.
    fabric = get_fabric()
    with fabric.lock:
        fabric.tasks[suspect["task_id"]]["state"] = "settled"
        fabric.tasks[suspect["task_id"]]["payout_axm"] = None
        fabric.tasks[suspect["task_id"]].pop("settled_at", None)

    report = reconcile_allocations(apply=True)
    by_task = {r["task_id"]: r for r in report["actions"]}
    assert by_task[live["task_id"]]["classification"] == "live"
    assert by_task[live["task_id"]]["applied"] is False
    assert by_task[suspect["task_id"]]["classification"] == "manual_review"
    assert by_task[suspect["task_id"]]["applied"] is False
    assert abs(report["totals_axm"]["manual_review"] - 2.0) < 1e-9

    assert _alloc(live["task_id"])["state"] == "allocated"
    assert _alloc(suspect["task_id"])["state"] == "allocated"
    status = bounty_pool().status()
    assert abs(status["allocated_axm"] - 6.0) < 1e-9
    assert abs(status["spent_axm"]) < 1e-9
    assert _conservation_holds(status)


# ---------------------------------------------------------------------------
# Periodic hook: expire_stale_assignments' cadence runs the sweep
# ---------------------------------------------------------------------------

def test_sweeper_cadence_reconciles(pool_env):
    from sincor2.a2a_inbound import get_fabric
    from sincor2.a2a_inbound_market import expire_stale_assignments

    _register_internal_poster()
    snap = _create_backed_task(bounty=1.5)
    task_id = snap["task_id"]
    # Task already terminal (its stranded allocation predates the
    # sweep); no assignment is stale, so expire_stale_assignments has
    # nothing of its own to do — the reconciliation tail must still run.
    fabric = get_fabric()
    with fabric.lock:
        fabric.tasks[task_id]["state"] = "expired"
        fabric.tasks[task_id]["expired_reason"] = "auction_timeout"

    assert expire_stale_assignments() == []
    assert _alloc(task_id)["state"] == "released"
    assert abs(bounty_pool().status()["available_axm"] - POOL_RESERVE) < 1e-9


# ---------------------------------------------------------------------------
# Operator CLI: dry-run prints the classification, --apply repairs the
# on-disk ledger (the maintenance-window route from the CLI docstring)
# ---------------------------------------------------------------------------

def test_operator_cli_dry_run_and_apply(tmp_path, monkeypatch, capsys):
    from sincor2.reconcile_pool_allocations import main as cli_main

    pool_path = str(tmp_path / "pool.json")
    tasks_path = tmp_path / "tasks.json"
    monkeypatch.setenv("SINCOR_LAUNCH_BOUNTY_AXM", str(POOL_RESERVE))
    pool = BountyPool(path=pool_path)
    pool.fund()
    alloc = pool.allocate("tsk_deadbeef01", 2.0, reason="task bounty backing")
    tasks_path.write_text(json.dumps({"tasks": [
        {"task_id": "tsk_deadbeef01", "state": "expired",
         "expired_reason": "auction_timeout", "bounty_axm": 2.0},
        {"task_id": "tsk_live000001", "state": "open", "bounty_axm": 1.0},
    ]}), encoding="utf-8")

    rc = cli_main(["--pool-path", pool_path, "--tasks-path", str(tasks_path)])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["dry_run"] is True
    rows = [r for r in report["actions"]
            if r["allocation_id"] == alloc["allocation_id"]]
    assert len(rows) == 1 and rows[0]["classification"] == "release"
    assert abs(report["totals_axm"]["release"] - 2.0) < 1e-9
    # Dry-run wrote nothing.
    assert BountyPool(path=pool_path).status()["allocation_count"] == 1

    rc = cli_main(["--apply", "--pool-path", pool_path,
                   "--tasks-path", str(tasks_path)])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["dry_run"] is False
    status = BountyPool(path=pool_path).status()
    assert status["allocation_count"] == 0
    assert abs(status["available_axm"] - POOL_RESERVE) < 1e-9
    assert _conservation_holds(status)
