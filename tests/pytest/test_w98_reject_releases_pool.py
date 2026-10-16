"""W-98: _reject_sybil_hold must release the pool allocation.

A held task's bounty backing stays "allocated" in the bounty pool while
the payout is parked for review (only the successful ``submit_proof``
path spends it). Rejecting the hold must return the allocation to
available unspent — without the release the funds are stranded forever
(live PoC: 3x100-AXM held->rejected left allocated=300, available
drained 19900->19700, spent=0).

No Flask server, no chain. KYA + fabric persistence are redirected to
temp dirs and both registries are reset per test (mirrors
test_sybil_ring_w81.py).
"""
from __future__ import annotations

import os
import shutil
import tempfile

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from sincor2 import kya_registry as kya
from sincor2.a2a_inbound import (
    _now_ms,
    get_fabric,
    reset_fabric,
)
from sincor2.a2a_identity import register_message as _new_registration_message
from sincor2.a2a_inbound_ext import register_agent_record
from sincor2.a2a_inbound_market import (
    _reject_sybil_hold,
    create_task,
    submit_proof,
)
from sincor2.a2a_bounty_pool import bounty_pool, reset_bounty_pool
from sincor2.onchain.stake_ledger import reset_stake_ledger


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch):
    """Hermetic KYA + fabric state per test (mirrors test_sybil_ring_w81.py)."""
    kya_dir = tempfile.mkdtemp(prefix="w98_kya_")
    store_fd, store_path = tempfile.mkstemp(prefix="w98_kya_", suffix=".json")
    os.close(store_fd)
    if os.path.exists(store_path):
        os.unlink(store_path)
    monkeypatch.setenv("KYA_STORE_PATH", store_path)
    monkeypatch.setenv("SINCOR_DATA_DIR", kya_dir)
    kya.reset()
    reset_fabric()
    yield
    kya.reset()
    reset_fabric()
    if os.path.exists(store_path):
        os.unlink(store_path)
    shutil.rmtree(kya_dir, ignore_errors=True)


@pytest.fixture
def _pool_ledger_isolation(tmp_path):
    """Funded pool + fresh stake ledger; singletons restored after."""
    import sincor2.a2a_bounty_pool as pool_mod
    import sincor2.onchain.stake_ledger as ledger_mod
    old_pool, old_ledger = pool_mod._POOL, ledger_mod._LEDGER
    pool_mod._POOL = None
    ledger_mod._LEDGER = None
    reset_bounty_pool(path=str(tmp_path / "pool.json"), reserve_axm=20000)
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    yield
    pool_mod._POOL, ledger_mod._LEDGER = old_pool, old_ledger


def _body(agent_id: str, wallet: str, **extra):
    body = {
        "agent_id": agent_id,
        "name": agent_id,
        "description": "w98 probe agent",
        "version": "0.1.0",
        "capability_tags": ["lead-enrichment"],
        "skills": [{"id": "lead-enrichment", "name": "Lead Enrichment"}],
        "rpc_callback": "https://probe.example/rpc",
        "wallet": wallet,
        "chain_id": 8453,
    }
    body.update(extra)
    return body


def _register(agent_id: str, key, **extra):
    """Register with a VALID EIP-191 wallet proof."""
    body = _body(agent_id, key.address, **extra)
    ts = _now_ms()
    message = _new_registration_message(agent_id, ts)
    sig = key.sign_message(encode_defunct(text=message)).signature
    body["registration_wallet"] = key.address
    body["registration_ts"] = ts
    body["registration_signature"] = "0x" + bytes(sig).hex()
    return register_agent_record(body)


def _pool_backed_held_task(bounty_axm: float = 100.0, tag: str = "") -> str:
    """Create a pool-backed task, win it with a newborn burst, and get it
    into the 'held' state via submit_proof (mirrors the W-81 hold
    driver). Returns the task_id. ``tag`` namespaces agent ids so
    multiple held tasks can coexist in one test."""
    poster = f"w98-poster{tag}"
    winner = f"w98-w0{tag}"
    _register(poster, Account.create())
    get_fabric().agents[poster]["origin"] = "internal"
    for i in range(3):
        _register(f"w98-w{i}{tag}", Account.create())
    task = create_task("lead-enrichment", bounty_axm=bounty_axm,
                       poster_id=poster)
    assert task["pool_backed"] is True, task
    task_id = task["task_id"]
    fabric = get_fabric()
    t = fabric.tasks[task_id]
    t.update({
        "state": "assigned",
        "assigned_to": winner,
        "assigned_at": _now_ms() - 1_000,
        "time_est_ms": 60_000,
        "winning_bid_axm": bounty_axm,
    })
    proof = submit_proof(task_id, winner, "0x" + "ab" * 16)
    assert proof["status"] == "held", proof
    return task_id


# ---------------------------------------------------------------------------
# W-98: rejecting a hold returns the pool allocation to available
# ---------------------------------------------------------------------------

def test_w98_reject_sybil_hold_releases_pool_allocation(_pool_ledger_isolation):
    """REGRESSION (W-98): create -> pool allocates 100 AXM -> hold ->
    admin reject. The allocation must return to available; allocated
    must drop back to zero. Before the fix the allocation stayed
    stranded (allocated=300 after 3 rejects, available drained)."""
    task_id = _pool_backed_held_task(bounty_axm=100.0)
    pool = bounty_pool()
    avail_before = pool.status()["available_axm"]
    alloc_before = pool.status()["allocated_axm"]
    assert alloc_before == 100.0, pool.status()
    assert len(pool.allocations_for_task(task_id)) == 1

    snap = _reject_sybil_hold(task_id, decided_by="test-admin")
    assert snap["status"] == "rejected"
    assert get_fabric().tasks[task_id]["state"] == "rejected"

    status = pool.status()
    assert status["available_axm"] == avail_before + 100.0, status
    assert status["allocated_axm"] == alloc_before - 100.0, status
    # Nothing stranded on the task.
    assert pool.allocations_for_task(task_id) == [], status


def test_w98_reject_sybil_hold_three_tasks_all_released(_pool_ledger_isolation):
    """The live PoC shape: 3x100-AXM held->rejected must leave available
    whole again and allocated back at zero (spent stays 0)."""
    task_ids = [_pool_backed_held_task(bounty_axm=100.0, tag=f"-t{i}")
                for i in range(3)]
    pool = bounty_pool()
    avail_start = pool.status()["available_axm"]
    assert avail_start == 19700.0, pool.status()

    for tid in task_ids:
        snap = _reject_sybil_hold(tid, decided_by="test-admin")
        assert snap["status"] == "rejected"

    status = pool.status()
    assert status["allocated_axm"] == 0.0, status
    assert status["available_axm"] == 20000.0, status
    assert status["spent_axm"] == 0.0, status


def test_w98_reject_twice_is_rejected_once_no_double_release(
        _pool_ledger_isolation):
    """The hold guard raises KeyError once the hold is decided, so a
    second reject cannot run the release loop again. The pool state is
    identical after the failed second call (no double-release)."""
    task_id = _pool_backed_held_task(bounty_axm=100.0)
    pool = bounty_pool()

    _reject_sybil_hold(task_id, decided_by="test-admin")
    status_after_first = pool.status()
    assert status_after_first["available_axm"] == 20000.0

    with pytest.raises(KeyError, match="no held payout"):
        _reject_sybil_hold(task_id, decided_by="test-admin")

    assert pool.status() == status_after_first


def test_w98_reject_non_held_task_raises_no_pool_side_effects(
        _pool_ledger_isolation):
    """A task that was never held cannot reach the release path: the
    guard raises KeyError and the pool is untouched."""
    task = create_task("lead-enrichment", bounty_axm=50.0,
                       poster_id="w98-poster")
    pool = bounty_pool()
    status_before = pool.status()

    with pytest.raises(KeyError, match="no held payout"):
        _reject_sybil_hold(task["task_id"])

    assert pool.status() == status_before
