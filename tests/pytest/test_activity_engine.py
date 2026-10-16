"""AXM Activity Engine — gasless auction loop end to end."""
from __future__ import annotations

import json
import os
import time

# Sealed-bid windows are read at sincor2.a2a_inbound_market import time;
# 1000ms is the enforced minimum, so tests exercise the REAL
# commit -> wait -> reveal -> close -> settle path on a short clock.
os.environ["SINCOR_SEALED_COMMIT_WINDOW_MS"] = "1000"
os.environ["SINCOR_SEALED_REVEAL_WINDOW_MS"] = "1000"

import pytest  # noqa: E402

from sincor2 import activity_engine  # noqa: E402

COHORT = "axm-engine-bidder-t1,axm-engine-bidder-t2,axm-engine-bidder-t3"


@pytest.fixture
def engine_env(tmp_path, monkeypatch):
    from sincor2.a2a_inbound import reset_fabric
    from sincor2.onchain.stake_ledger import reset_stake_ledger
    from sincor2 import treasury_inflow

    reset_fabric()
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    activity_engine.reset_catalog_cache()
    # Sealed-bid windows are frozen at sincor2.a2a_inbound_market import
    # time.  The module-level env override above only takes effect when
    # this file is imported before any module that pulls in the market
    # module; when another suite (e.g. test_w89_wash_guard) is collected
    # first, the 5-minute default sticks and the cycle-driving tests below
    # hang past their 40s timeouts.  Pin the globals per-test so the
    # suite is green regardless of collection order.
    import sincor2.a2a_inbound_market as _mkt
    monkeypatch.setattr(_mkt, "COMMIT_WINDOW_MS", 1000)
    monkeypatch.setattr(_mkt, "REVEAL_WINDOW_MS", 1000)
    monkeypatch.setenv("SINCOR_ACTIVITY_ENGINE_STATE",
                       str(tmp_path / "ae_state.json"))
    monkeypatch.setenv("SINCOR_ACTIVITY_COHORT_IDS", COHORT)
    monkeypatch.setenv("SINCOR_ACTIVITY_MAX_IN_FLIGHT", "10")
    monkeypatch.setenv("SINCOR_ACTIVITY_MAX_NEW_PER_CYCLE", "1")
    monkeypatch.setenv("SINCOR_ACTIVITY_STAKE_AXM", "10")
    monkeypatch.setattr(treasury_inflow, "_LEDGER_PATH",
                        tmp_path / "treasury_inflow.jsonl")
    yield tmp_path


def _drive_until_done(task_keys, timeout=40):
    """Run non-posting cycles until every task_key leaves in_flight."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        activity_engine.run_cycle(post=False)
        state = activity_engine.load_state()
        if all(k not in state["in_flight"] for k in task_keys):
            return state
        time.sleep(0.2)
    raise TimeoutError(f"tasks did not finish: {task_keys}")


def _fee_entries(tmp_path):
    path = tmp_path / "treasury_inflow.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_catalog_loads_1000_unique_tasks(engine_env):
    catalog = activity_engine.load_catalog()
    assert len(catalog) == 1000
    keys = [t["task_key"] for t in catalog]
    assert len(set(keys)) == 1000
    first = catalog[0]
    assert first["skill"] and first["bounty_axm"] > 0


def test_full_cycle_commit_reveal_close_settle_fee(engine_env):
    tmp_path = engine_env
    from sincor2.a2a_inbound import get_fabric
    from sincor2.onchain.stake_ledger import stake_ledger

    summary = activity_engine.run_cycle()
    assert summary["posted"], "first cycle must post a task"
    task_key = summary["posted"][0]
    state = activity_engine.load_state()
    assert state["next_index"] == 1  # durable cursor advanced exactly once
    tsk_id = state["in_flight"][task_key]["tsk_id"]

    # Idempotent commits: a second cycle inside the commit window must not
    # double-commit (it legitimately posts a second auction — pipelining).
    summary2 = activity_engine.run_cycle()
    assert summary2["posted"], "pipelining must post while capacity remains"
    task_key2 = summary2["posted"][0]
    fabric = get_fabric()
    commits = [k for k in fabric.commits if k.startswith(tsk_id + "\x00")]
    assert len(commits) == 3

    state = _drive_until_done([task_key, task_key2])
    assert state["settled_count"] == 2
    assert state["in_flight"] == {}

    task = fabric.tasks[tsk_id]
    assert task["state"] == "settled"
    winner = task["assigned_to"]
    assert winner in COHORT.split(",")
    payout = float(task["payout_axm"])
    assert payout > 0

    # 5% treasury fee recorded on the existing ledger rail with the
    # metrics-visible source.
    fees = [e for e in _fee_entries(tmp_path)
            if e.get("source") == "a2a_settlement"
            and task_key in e.get("note", "")]
    assert len(fees) == 1
    assert fees[0]["asset"] == "AXM"
    assert fees[0]["projected"] is False
    assert abs(fees[0]["amount"] - round(payout * 0.05, 6)) < 1e-9
    # State accumulates fees across every settled auction (two settled here).
    all_fees = [e for e in _fee_entries(tmp_path)
                if e.get("source") == "a2a_settlement"
                and "activity-engine" in e.get("note", "")]
    assert abs(state["fees_axm"] - sum(e["amount"] for e in all_fees)) < 1e-9
    assert len(all_fees) == 2

    # Stake fully released for every bidder (no locked residue).
    for agent_id in COHORT.split(","):
        bal = stake_ledger().balance_of(agent_id)
        assert bal["locked_wei"] == "0", agent_id
        assert bal["available_wei"] == str(10 * 10**18), agent_id

    # Winner honestly earned reputation via the protocol path.
    assert float(fabric.agents[winner]["reputation"]) > 0


def test_loop_repeats_across_cycles_no_repeat_until_exhausted(engine_env):
    from sincor2.a2a_inbound import get_fabric

    posted_keys = []
    for _ in range(3):
        summary = activity_engine.run_cycle(post=False)
        # post manually one at a time to keep the test clock tight
        state = activity_engine.load_state()
        catalog = activity_engine.load_catalog()
        key = catalog[state["next_index"]]["task_key"]
        activity_engine.post_task(catalog[state["next_index"]], state)
        activity_engine.save_state(state)
        posted_keys.append(key)
    assert len(set(posted_keys)) == 3  # round-robin, no repeats

    state = _drive_until_done(posted_keys)
    assert state["settled_count"] == 3
    assert state["next_index"] == 3
    assert state["fees_axm"] > 0

    fabric = get_fabric()
    seed_keys = [t.get("seed_key") for t in fabric.tasks.values()]
    for key in posted_keys:
        assert seed_keys.count(f"activity-engine:{key}") == 1


def test_restart_resume_does_not_duplicate(engine_env):
    """Simulate a process restart mid-commit-window: the engine must resume
    the same auction (same task_key, one fabric task) and never skip or
    double-count the cursor."""
    from sincor2.a2a_inbound import get_fabric, reset_fabric
    from sincor2.onchain.stake_ledger import reset_stake_ledger

    summary = activity_engine.run_cycle()
    task_key = summary["posted"][0]
    state = activity_engine.load_state()
    assert state["next_index"] == 1
    assert len(state["in_flight"]) == 1

    # --- simulated restart: fresh process, same state file on disk ---
    reset_fabric()
    reset_stake_ledger(path=str(engine_env / "stake2.json"))
    activity_engine.reset_catalog_cache()

    summary2 = activity_engine.run_cycle(post=False)
    state2 = activity_engine.load_state()
    # Same task_key resumed (re-posted after the fabric wipe); cursor did
    # NOT consume a new catalog slot for the repost.
    assert list(state2["in_flight"].keys()) == [task_key]
    assert state2["next_index"] == 1
    assert state2["settled_count"] == 0

    fabric = get_fabric()
    matching = [t for t in fabric.tasks.values()
                if t.get("seed_key") == f"activity-engine:{task_key}"]
    assert len(matching) == 1  # exactly one live task for the key

    state2 = _drive_until_done([task_key])
    assert state2["settled_count"] == 1
    assert state2["next_index"] == 1  # cursor advanced exactly once total


def test_seed_key_dedupe_no_double_post(engine_env):
    """Posting the same catalog task twice converges on one live task."""
    catalog = activity_engine.load_catalog()
    state = activity_engine.load_state()
    rec1 = activity_engine.post_task(catalog[0], state)
    rec2 = activity_engine.post_task(catalog[0], state)
    assert rec2["dedupe_hit"] is True
    assert rec1["tsk_id"] == rec2["tsk_id"]

    from sincor2.a2a_inbound import get_fabric
    fabric = get_fabric()
    live = [t for t in fabric.tasks.values()
            if t.get("seed_key") == "activity-engine:" + catalog[0]["task_key"]
            and t.get("state") in ("open", "auction")]
    assert len(live) == 1


def test_scheduler_registers_activity_engine_cycle(engine_env):
    from apscheduler.schedulers.background import BackgroundScheduler
    from sincor2.scheduler import _register_jobs

    sched = BackgroundScheduler(timezone="America/Chicago")
    try:
        registered = _register_jobs(sched)
    finally:
        if sched.running:
            sched.shutdown(wait=False)
    assert "activity_engine_cycle" in registered
    ids = {job.id for job in sched.get_jobs()}
    assert "activity_engine_cycle" in ids


def test_metrics_labels_gasless(engine_env):
    m = activity_engine.metrics()
    assert m["engine"] == "axm-activity-engine"
    assert m["mode"] == "gasless-ledger-only"
    assert m["catalog_size"] == 1000
    assert "no on-chain" in m["note"]
