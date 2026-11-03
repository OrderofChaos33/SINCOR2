"""Checkpointed marketplace persistence: board + bounty pool survive
restarts/redeploys through the workflow state layer.

The 2026-11-02 redeploy reset the board (1,748 -> 699 tasks) and
re-seeded the pool ledger: the JSON persistence files were wiped with
the data dir. The stores now mirror every save into the workflow
checkpoint store (sincor2.workflow_state) as a hash-bound snapshot, and
a boot that finds its JSON file missing restores the exact last-saved
state from the verified checkpoint instead.

Covered here:
* pool conservation (funded = allocated + available + spent, exact)
  across a simulated restart whose JSON ledger file was deleted;
* the full money loop — post -> allocate -> bid -> RESTART -> close ->
  settle (pool spend + settlement record) -> second post -> release —
  ending in exact conservation;
* board tasks in open/auction/settled/expired states surviving the same
  restart with statuses and amounts intact, including the in-flight
  bid (never persisted by the legacy JSON file at all);
* fail-closed boot: a corrupt checkpoint DB raises instead of booting
  empty over checkpointed state; a corrupt tasks JSON raises too;
* the checkpoint root is env-configurable (tests run entirely in tmp).
"""
from __future__ import annotations

import json
import os
import tempfile
import time

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from sincor2 import kya_registry as kya
from sincor2.a2a_bounty_pool import BountyPool, PoolError, reset_bounty_pool
from sincor2.a2a_identity import register_message as _new_registration_message
from sincor2.a2a_inbound import (
    _load_tasks,
    _now_ms,
    _save_agents,
    _save_tasks,
    get_fabric,
    reset_fabric,
)
from sincor2.a2a_inbound_ext import register_agent_record
from sincor2.a2a_inbound_market import close_auction, create_task, place_bid, submit_proof
from sincor2.onchain.stake_ledger import reset_stake_ledger


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch):
    """Hermetic KYA + fabric + ledger singletons per test (mirrors
    test_w92_pool_backing_persisted.py)."""
    import sincor2.a2a_bounty_pool as pool_mod
    import sincor2.onchain.stake_ledger as ledger_mod

    kya_dir = tempfile.mkdtemp(prefix="ckpt_kya_")
    store_fd, store_path = tempfile.mkstemp(prefix="ckpt_kya_", suffix=".json")
    os.close(store_fd)
    if os.path.exists(store_path):
        os.unlink(store_path)
    monkeypatch.setenv("KYA_STORE_PATH", store_path)
    monkeypatch.setenv("SINCOR_DATA_DIR", kya_dir)
    old_pool, old_ledger = pool_mod._POOL, ledger_mod._LEDGER
    pool_mod._POOL = None
    ledger_mod._LEDGER = None
    kya.reset()
    reset_fabric()
    yield
    kya.reset()
    reset_fabric()
    pool_mod._POOL, ledger_mod._LEDGER = old_pool, old_ledger


@pytest.fixture
def persist_env(tmp_path, monkeypatch):
    """All marketplace persistence roots redirected to one tmp dir."""
    env = {
        "tasks": tmp_path / "a2a_tasks.json",
        "pool": tmp_path / "pool.json",
        "checkpoints": tmp_path / "checkpoints.db",
        "stake": tmp_path / "stake.json",
    }
    monkeypatch.setenv("SINCOR_A2A_TASKS_PATH", str(env["tasks"]))
    monkeypatch.setenv("SINCOR_BOUNTY_POOL_PATH", str(env["pool"]))
    monkeypatch.setenv("SINCOR_MARKETPLACE_CHECKPOINT_PATH",
                       str(env["checkpoints"]))
    reset_stake_ledger(path=str(env["stake"]))
    return env


def _register(agent_id: str, key, capability="lead-enrichment"):
    body = {
        "agent_id": agent_id,
        "name": agent_id,
        "description": "checkpoint persistence probe agent",
        "version": "0.1.0",
        "capability_tags": [capability],
        "skills": [{"id": capability, "name": capability}],
        "rpc_callback": "https://probe.example/rpc",
        "wallet": key.address,
        "chain_id": 8453,
        "registration_wallet": key.address,
        "registration_ts": _now_ms(),
    }
    ts = body["registration_ts"]
    message = _new_registration_message(agent_id, ts)
    sig = key.sign_message(encode_defunct(text=message)).signature
    body["registration_signature"] = "0x" + bytes(sig).hex()
    return register_agent_record(body)


def _restart_board():
    """Simulate a process restart: drop the fabric singleton so the
    next get_fabric() rebuilds from durable state only."""
    import sincor2.a2a_inbound as inbound

    inbound._FABRIC = None
    return inbound.get_fabric()


def _assert_conserved(pool: BountyPool):
    st = pool.status()
    total = st["allocated_axm"] + st["available_axm"] + st["spent_axm"]
    assert abs(st["funded_axm"] - total) < 1e-9, st
    v = pool.verify_ledger()
    assert v["ok"], v
    return st


# ---------------------------------------------------------------------------
# Pool: checkpoint restore + conservation
# ---------------------------------------------------------------------------

def test_pool_restored_from_checkpoint_when_ledger_file_lost(persist_env):
    pool = reset_bounty_pool(path=str(persist_env["pool"]), reserve_axm=100.0)
    pool.fund()
    a = pool.allocate("task-a", 40.0)
    pool.spend(a["allocation_id"])
    b = pool.allocate("task-b", 25.0)
    _assert_conserved(pool)

    # The redeploy wipe: the JSON ledger is gone, the checkpoint DB is not.
    persist_env["pool"].unlink()
    assert not persist_env["pool"].exists()

    pool2 = BountyPool(path=str(persist_env["pool"]), reserve_axm=100.0)
    assert pool2._checkpoint_restored is True
    st = _assert_conserved(pool2)
    assert st["funded_axm"] == 100.0
    assert st["spent_axm"] == 40.0
    assert st["allocated_axm"] == 25.0
    assert st["available_axm"] == 35.0
    states = {al["task_id"]: al["state"] for al in st["allocations"]}
    assert states == {"task-a": "spent", "task-b": "allocated"}
    # The JSON ledger is rewritten from the restored state.
    assert persist_env["pool"].is_file()
    # The restored allocation is still live and releasable.
    pool2.release(b["allocation_id"])
    st = _assert_conserved(pool2)
    assert st["allocated_axm"] == 0.0
    assert st["available_axm"] == 60.0


def test_pool_corrupt_checkpoint_fails_closed(persist_env):
    pool = reset_bounty_pool(path=str(persist_env["pool"]), reserve_axm=100.0)
    pool.fund()
    pool.allocate("task-a", 10.0)
    assert persist_env["checkpoints"].is_file()

    persist_env["pool"].unlink()
    persist_env["checkpoints"].write_bytes(b"this is not a sqlite database")
    with pytest.raises(PoolError, match="checkpoint"):
        BountyPool(path=str(persist_env["pool"]), reserve_axm=100.0)


def test_checkpoint_db_lands_at_env_configured_path(persist_env):
    pool = reset_bounty_pool(path=str(persist_env["pool"]), reserve_axm=50.0)
    pool.fund()
    assert persist_env["checkpoints"].is_file()


# ---------------------------------------------------------------------------
# Board: states survive via checkpoint; corrupt persistence fails closed
# ---------------------------------------------------------------------------

def test_board_states_survive_restart_from_checkpoint(persist_env):
    reset_bounty_pool(path=str(persist_env["pool"]), reserve_axm=100.0).fund()
    fabric = get_fabric()
    t_open = create_task("lead-enrichment", bounty_axm=2.0)
    t_settled = create_task("lead-enrichment", bounty_axm=3.0)
    t_expired = create_task("lead-enrichment", bounty_axm=4.0)
    with fabric.lock:
        fabric.tasks[t_settled["task_id"]]["state"] = "settled"
        fabric.tasks[t_settled["task_id"]]["payout_axm"] = 2.5
        fabric.tasks[t_expired["task_id"]]["state"] = "expired"
    _save_tasks(fabric)

    # Wipe the legacy JSON board file: the checkpoint is the only source.
    persist_env["tasks"].unlink()
    fabric2 = _restart_board()
    open_t = fabric2.tasks[t_open["task_id"]]
    assert open_t["state"] == "open" and open_t["bounty_axm"] == 2.0
    settled_t = fabric2.tasks[t_settled["task_id"]]
    assert settled_t["state"] == "settled"
    assert settled_t["payout_axm"] == 2.5
    expired_t = fabric2.tasks[t_expired["task_id"]]
    assert expired_t["state"] == "expired" and expired_t["bounty_axm"] == 4.0


def test_board_corrupt_checkpoint_fails_closed(persist_env):
    reset_bounty_pool(path=str(persist_env["pool"]), reserve_axm=100.0).fund()
    create_task("lead-enrichment", bounty_axm=2.0)
    assert persist_env["checkpoints"].is_file()

    persist_env["tasks"].unlink()
    persist_env["checkpoints"].write_bytes(b"garbage, not sqlite")
    with pytest.raises(RuntimeError, match="checkpoint"):
        _restart_board()


def test_board_corrupt_tasks_file_fails_closed(persist_env):
    persist_env["tasks"].write_text("{not json", encoding="utf-8")
    fabric = reset_fabric()
    with pytest.raises(RuntimeError, match="corrupt"):
        _load_tasks(fabric)


# ---------------------------------------------------------------------------
# Full loop (the "wired" gate): post -> allocate -> bid -> RESTART ->
# close -> settle (fee-bearing settlement record; pool spend) -> post ->
# release, ending in exact conservation.
# ---------------------------------------------------------------------------

def test_full_loop_with_restart_mid_auction_conserves_pool(persist_env):
    poster_key, bidder_key = Account.create(), Account.create()
    _register("persist-poster", poster_key)
    _register("persist-bidder", bidder_key)
    fabric = get_fabric()
    with fabric.lock:
        fabric.agents["persist-poster"]["origin"] = "internal"
        fabric.agents["persist-bidder"]["reputation"] = 1.0
        fabric.agents["persist-bidder"]["last_heartbeat"] = _now_ms()
    # Persist the stamps (registration does the same in production), so
    # the post-restart fabric reloads the poster with its standing.
    _save_agents(fabric)

    pool = reset_bounty_pool(path=str(persist_env["pool"]), reserve_axm=100.0)
    pool.fund()

    # Post + allocate.
    task = create_task("lead-enrichment", bounty_axm=10.0,
                       poster_id="persist-poster")
    assert task["pool_backed"] is True
    task_id = task["task_id"]
    st = _assert_conserved(pool)
    assert st["allocated_axm"] == 10.0 and st["available_axm"] == 90.0

    # Bid (task moves to auction; the bid lives only in the checkpoint —
    # the legacy JSON board file never persisted bids).
    bid = place_bid(task_id, "persist-bidder", 9.0, 60)
    assert get_fabric().tasks[task_id]["state"] == "auction"

    # RESTART mid-loop, with BOTH legacy JSON files wiped (redeploy):
    # board and pool must come back from the checkpoint store alone.
    persist_env["tasks"].unlink()
    persist_env["pool"].unlink()
    fabric2 = _restart_board()
    reset_stake_ledger(path=str(persist_env["stake"]))
    pool2 = reset_bounty_pool(path=str(persist_env["pool"]), reserve_axm=100.0)
    assert pool2._checkpoint_restored is True

    task2 = fabric2.tasks[task_id]
    assert task2["state"] == "auction"
    assert task2["bounty_axm"] == 10.0
    assert task2["pool_backed"] is True
    assert any(b["bid_id"] == bid["bid_id"] for b in fabric2.bids.values()), \
        "in-flight bid must survive the restart"
    st = _assert_conserved(pool2)
    assert st["allocated_axm"] == 10.0 and st["spent_axm"] == 0.0

    # Close -> assigned to the pre-restart bidder.
    time.sleep(0.6)  # let the 500ms auction window lapse
    closed = close_auction(task_id)
    assert closed["state"] == "assigned"
    assert closed["assigned_to"] == "persist-bidder"

    # Settle: payout staged (fee-bearing settlement record), pool
    # allocation consumed allocated -> spent.
    proof = submit_proof(task_id, "persist-bidder", "0x" + "ab" * 32)
    assert proof["payout"]["ok"] is True
    assert get_fabric().tasks[task_id]["state"] == "settled"
    assert any(h["action"] == "spend" for h in pool2._history), \
        "settlement must be recorded in the pool journal"
    st = _assert_conserved(pool2)
    assert st["spent_axm"] == 10.0 and st["allocated_axm"] == 0.0

    # Second post after the restart, then release its backing.
    task_b = create_task("lead-enrichment", bounty_axm=20.0,
                         poster_id="persist-poster")
    assert task_b["pool_backed"] is True
    st = _assert_conserved(pool2)
    assert st["allocated_axm"] == 20.0
    pool2.release(task_b["pool_allocation_id"])

    # Final exact conservation: funded = allocated + available + spent.
    st = _assert_conserved(pool2)
    assert st["funded_axm"] == 100.0
    assert st["allocated_axm"] == 0.0
    assert st["spent_axm"] == 10.0
    assert st["available_axm"] == 90.0

    # And the whole end state survives one more wipe + restart.
    persist_env["tasks"].unlink()
    persist_env["pool"].unlink()
    fabric3 = _restart_board()
    pool3 = BountyPool(path=str(persist_env["pool"]), reserve_axm=100.0)
    assert fabric3.tasks[task_id]["state"] == "settled"
    assert fabric3.tasks[task_id]["payout_axm"] == 9.0
    assert fabric3.tasks[task_b["task_id"]]["state"] == "open"
    assert fabric3.tasks[task_b["task_id"]]["bounty_axm"] == 20.0
    st = _assert_conserved(pool3)
    assert st["funded_axm"] == 100.0 and st["spent_axm"] == 10.0
    assert st["available_axm"] == 90.0 and st["allocated_axm"] == 0.0


# ---------------------------------------------------------------------------
# Builder B rejection regressions (e3bc2e84): mirror-lag double-spend,
# wall-clock board freshness, snapshot concurrency. The defect tests
# fail on the rejected code and pass on the fix (pin-checked); the
# companion tests pin the safe direction of the new semantics.
# ---------------------------------------------------------------------------

def test_pool_mirror_failure_fails_closed_no_double_spend(
        persist_env, monkeypatch):
    """Defect 1: a spend whose checkpoint mirror fails must not commit
    silently. The operation raises, the instance is poisoned, and a
    later boot with the JSON wiped fails closed over the degraded
    marker instead of resurrecting the allocation for a second spend."""
    import sincor2.workflow_state as ws

    pool = reset_bounty_pool(path=str(persist_env["pool"]), reserve_axm=100.0)
    pool.fund()
    alloc = pool.allocate("task-a", 40.0)

    real_save = ws.save_state_snapshot

    def _boom(*args, **kwargs):
        raise RuntimeError("checkpoint store unavailable (forced)")

    monkeypatch.setattr(ws, "save_state_snapshot", _boom)
    with pytest.raises(PoolError, match="mirror"):
        pool.spend(alloc["allocation_id"])
    # The poisoned instance refuses further money movement.
    with pytest.raises(PoolError, match="degraded"):
        pool.allocate("task-b", 1.0)
    monkeypatch.setattr(ws, "save_state_snapshot", real_save)

    # The degraded marker is durable and survives the JSON wipe.
    marker = persist_env["checkpoints"].with_name(
        persist_env["checkpoints"].name + ".pool-degraded")
    assert marker.is_file()
    persist_env["pool"].unlink()
    with pytest.raises(PoolError, match="degraded"):
        BountyPool(path=str(persist_env["pool"]), reserve_axm=100.0)


def test_pool_mirror_recovery_after_marker_and_json_intact(persist_env):
    """Companion to the fail-closed path: when the JSON survives, it
    stays authoritative even if a degraded marker exists, and normal
    operation (including checkpoint restore after a later clean wipe)
    is unaffected."""
    pool = reset_bounty_pool(path=str(persist_env["pool"]), reserve_axm=100.0)
    pool.fund()
    a = pool.allocate("task-a", 40.0)
    pool.spend(a["allocation_id"])
    # Simulate a leftover marker from some earlier incident; the JSON
    # is present, so boot uses it (present-JSON-wins is preserved).
    marker = persist_env["checkpoints"].with_name(
        persist_env["checkpoints"].name + ".pool-degraded")
    marker.write_text("{}", encoding="utf-8")
    pool2 = BountyPool(path=str(persist_env["pool"]), reserve_axm=100.0)
    st = _assert_conserved(pool2)
    assert st["spent_axm"] == 40.0
    # A clean save on the new instance clears the stale marker.
    pool2.allocate("task-b", 5.0)
    assert not marker.exists()


def _forge_board_checkpoint(persist_env, state):
    from sincor2.workflow_state import (
        BOARD_CHECKPOINT_NAME,
        BOARD_RUN_ID,
        WorkflowStateStore,
        save_state_snapshot,
    )
    store = WorkflowStateStore.open(str(persist_env["checkpoints"]))
    try:
        save_state_snapshot(store, BOARD_RUN_ID, BOARD_CHECKPOINT_NAME, state)
    finally:
        store.close()


def test_board_future_dated_checkpoint_cannot_resurrect(persist_env):
    """Defect 2 (C1): a validly hash-bound checkpoint holding stale
    board content with a far-future saved_at must NOT override the
    present JSON. Freshness is monotonic checkpoint version pairing,
    never payload wall-clock."""
    import copy

    reset_bounty_pool(path=str(persist_env["pool"]), reserve_axm=100.0).fund()
    fabric = get_fabric()
    t1 = create_task("lead-enrichment", bounty_axm=2.0)
    stale_tasks = copy.deepcopy(list(fabric.tasks.values()))
    with fabric.lock:
        fabric.tasks[t1["task_id"]]["state"] = "settled"
    t2 = create_task("lead-enrichment", bounty_axm=3.0)
    _save_tasks(fabric)

    _forge_board_checkpoint(persist_env, {
        "tasks": stale_tasks, "bids": [], "commits": {}, "proofs": [],
        "saved_at": _now_ms() + 10 ** 9,
    })
    fabric2 = _restart_board()
    assert fabric2.tasks[t1["task_id"]]["state"] == "settled"
    assert t2["task_id"] in fabric2.tasks


def test_board_checkpoint_tie_prefers_json(persist_env):
    """Defect 2 (C3): a checkpoint whose saved_at exactly ties the
    JSON's must not win over the present JSON either."""
    import copy

    reset_bounty_pool(path=str(persist_env["pool"]), reserve_axm=100.0).fund()
    fabric = get_fabric()
    t1 = create_task("lead-enrichment", bounty_axm=2.0)
    stale_tasks = copy.deepcopy(list(fabric.tasks.values()))
    with fabric.lock:
        fabric.tasks[t1["task_id"]]["state"] = "settled"
    _save_tasks(fabric)

    raw = json.loads(persist_env["tasks"].read_text(encoding="utf-8"))
    _forge_board_checkpoint(persist_env, {
        "tasks": stale_tasks, "bids": [], "commits": {}, "proofs": [],
        "saved_at": raw["saved_at"],
    })
    fabric2 = _restart_board()
    assert fabric2.tasks[t1["task_id"]]["state"] == "settled"


def test_board_mirror_lag_keeps_json_authoritative(persist_env, monkeypatch):
    """Defect 2 companion: when a save's mirror is lost, the JSON from
    that save carries no checkpoint version, so boot prefers the JSON
    over the stale checkpoint instead of resurrecting old state."""
    import sincor2.a2a_inbound as inbound

    reset_bounty_pool(path=str(persist_env["pool"]), reserve_axm=100.0).fund()
    fabric = get_fabric()
    t1 = create_task("lead-enrichment", bounty_axm=2.0)

    real_mirror = inbound._mirror_board_to_checkpoint
    monkeypatch.setattr(inbound, "_mirror_board_to_checkpoint",
                        lambda state: None)
    with fabric.lock:
        fabric.tasks[t1["task_id"]]["state"] = "settled"
    inbound._save_tasks(fabric)
    monkeypatch.setattr(inbound, "_mirror_board_to_checkpoint", real_mirror)

    fabric2 = _restart_board()
    assert fabric2.tasks[t1["task_id"]]["state"] == "settled"


def test_checkpoint_snapshot_is_hash_bound(persist_env):
    """Tampering with the checkpointed board state inside the DB must
    fail verification on boot — never a silent restore of forged state."""
    import sqlite3

    reset_bounty_pool(path=str(persist_env["pool"]), reserve_axm=100.0).fund()
    create_task("lead-enrichment", bounty_axm=2.0)
    db = str(persist_env["checkpoints"])
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT state FROM ws_checkpoints WHERE run_id = 'marketplace-board'"
    ).fetchone()
    assert row is not None
    forged = json.loads(row[0])
    forged["tasks"][0]["bounty_axm"] = 9999.0
    conn.execute(
        "UPDATE ws_checkpoints SET state = ? WHERE run_id = 'marketplace-board'",
        (json.dumps(forged),))
    conn.commit()
    conn.close()

    persist_env["tasks"].unlink()
    with pytest.raises(RuntimeError, match="checkpoint"):
        _restart_board()
