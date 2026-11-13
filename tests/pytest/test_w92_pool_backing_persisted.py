"""W-92: pool_backed/pool_allocation_id must be persisted, not snapshot-only.

create_task() persisted the task BEFORE the pool allocation ran, then set
pool_backed/pool_allocation_id on the returned snapshot dict only. API
callers saw ``pool_backed: true``; the persisted record had no such field.

Regression: task persistence is opted in via SINCOR_A2A_TASKS_PATH, a
pool-backed task is created, and the task is re-read from persisted
storage (the JSON file on disk, then a fresh fabric reload) — never the
returned snapshot. The persisted record must carry pool_backed=True and
the same pool_allocation_id. The gate-failed (unbacked) path must persist
pool_backed=False with the skip reason.

No Flask server, no chain. KYA + fabric persistence are redirected to
temp dirs and both registries are reset per test (mirrors
test_w98_reject_releases_pool.py).
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from sincor2 import kya_registry as kya
from sincor2.a2a_inbound import (
    _load_tasks,
    _now_ms,
    get_fabric,
    reset_fabric,
)
from sincor2.a2a_identity import register_message as _new_registration_message
from sincor2.a2a_inbound_ext import register_agent_record
from sincor2.a2a_inbound_market import create_task
from sincor2.a2a_bounty_pool import reset_bounty_pool
from sincor2.onchain.stake_ledger import reset_stake_ledger


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch):
    """Hermetic KYA + fabric state per test (mirrors test_sybil_ring_w81.py)."""
    kya_dir = tempfile.mkdtemp(prefix="w92_kya_")
    store_fd, store_path = tempfile.mkstemp(prefix="w92_kya_", suffix=".json")
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


@pytest.fixture
def _tasks_persisted(monkeypatch, tmp_path):
    """Opt into write-through task persistence (off by default under FLASK_ENV=test)."""
    tasks_path = tmp_path / "a2a_tasks.json"
    monkeypatch.setenv("SINCOR_A2A_TASKS_PATH", str(tasks_path))
    return tasks_path


def _body(agent_id: str, wallet: str, **extra):
    body = {
        "agent_id": agent_id,
        "name": agent_id,
        "description": "w92 probe agent",
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


def _read_persisted_task(tasks_path, task_id):
    """Re-read a task record straight from the persisted JSON file."""
    raw = tasks_path.read_text(encoding="utf-8")
    for t in json.loads(raw)["tasks"]:
        if t.get("task_id") == task_id:
            return t
    return None


# ---------------------------------------------------------------------------
# W-92: pool-backing fields must land in the persisted task record
# ---------------------------------------------------------------------------

def test_w92_pool_backed_fields_persisted(_pool_ledger_isolation, _tasks_persisted):
    """REGRESSION (W-92): the persisted task record — not just the returned
    snapshot — must carry pool_backed=True and the pool_allocation_id.

    Before the fix the task was saved before allocation ran and the fields
    were set on the snapshot dict only: API said backed, DB said nothing.
    """
    _register("w92-poster", Account.create())
    get_fabric().agents["w92-poster"]["origin"] = "internal"

    snap = create_task("lead-enrichment", bounty_axm=100.0,
                       poster_id="w92-poster")
    assert snap["pool_backed"] is True
    alloc_id = snap["pool_allocation_id"]
    assert alloc_id
    task_id = snap["task_id"]

    # Read the record from persisted storage, NOT the returned snapshot.
    persisted = _read_persisted_task(_tasks_persisted, task_id)
    assert persisted is not None, "task missing from persisted task file"
    assert persisted["pool_backed"] is True
    assert persisted["pool_allocation_id"] == alloc_id

    # And it must survive the same restore path a fresh process takes.
    reset_fabric()
    _load_tasks(get_fabric())
    reloaded = get_fabric().tasks[task_id]
    assert reloaded["pool_backed"] is True
    assert reloaded["pool_allocation_id"] == alloc_id


def test_w92_unbacked_task_persists_gate_failure(_pool_ledger_isolation,
                                                _tasks_persisted):
    """REGRESSION (W-92): the gate-failed path must also persist its result —
    pool_backed=False with the skip reason — so the stored record matches
    the snapshot the API returned."""
    snap = create_task("lead-enrichment", bounty_axm=100.0,
                       poster_id="w92-nobody-unregistered")
    assert snap["pool_backed"] is False
    assert snap["pool_backing_skipped_reason"]
    task_id = snap["task_id"]

    persisted = _read_persisted_task(_tasks_persisted, task_id)
    assert persisted is not None, "task missing from persisted task file"
    assert persisted["pool_backed"] is False
    assert persisted["pool_allocation_id"] is None
    assert persisted["pool_backing_skipped_reason"] == snap[
        "pool_backing_skipped_reason"]
