"""Hunt 33 — BountyPool spent reconciliation (W-118), rehydrate guard
(W-119), and pool-status ledger-verification wiring (W-101 gap).

W-118: verify_ledger() replayed the journal against funded/allocated
totals only. The spent counter — the exact field allocate() gates on via
available = funded - spent - allocated — was never compared to journal
spend totals, so a drift mutant zeroing spent_wei passed verify green
while inflating available (PoC §A: 160 promised against a 100 reserve).

W-119: losing the pool file auto-rehydrated the full reserve while live
task allocations (kept in the separate task store) were forgotten, so the
same reserve could be promised twice (PoC §B). The fix refuses to
auto-fund when live pool-backed tasks exist and says so loudly (ERROR
log + rehydrate_blocked status flag); clean first boots (no file, no
live tasks) still auto-fund exactly as before.

Wiring gap: verify_ledger() had zero production callers, so W-118-style
drift would sit undetected at runtime. GET /v1/a2a/pool now carries an
aggregate-only `ledger_verification` field (ok + discrepancies, no
secrets, no per-agent data).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import pytest
from flask import Flask

from sincor2.a2a_bounty_pool import (
    BountyPool,
    PoolError,
    reset_bounty_pool,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mkpool(tmp_path, reserve=100.0):
    """Fresh pool on a missing file; auto-fund is idempotent with fund()."""
    p = BountyPool(path=str(tmp_path / "pool.json"), reserve_axm=reserve)
    p.fund()
    return p


def _reload(tmp_path, reserve=100.0):
    return BountyPool(path=str(tmp_path / "pool.json"), reserve_axm=reserve)


def _mutate_pool_file(tmp_path, fn):
    path = tmp_path / "pool.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    fn(raw)
    path.write_text(json.dumps(raw), encoding="utf-8")


def _spent_task_pool(tmp_path):
    """fund 100, allocate 60, spend it — the PoC §A baseline state."""
    p = _mkpool(tmp_path)
    alloc = p.allocate("task-A", 60.0)
    p.spend(alloc["allocation_id"])
    assert p.verify_ledger()["ok"]
    return p


def _task(task_id, state, backed=True, bounty=60.0):
    return {
        "task_id": task_id,
        "state": state,
        "pool_backed": backed,
        "pool_allocation_id": "alloc_test" if backed else None,
        "bounty_axm": bounty,
    }


def _seed_tasks_file(monkeypatch, tmp_path, tasks):
    tasks_path = tmp_path / "tasks.json"
    tasks_path.write_text(
        json.dumps({"tasks": tasks, "saved_at": 0}), encoding="utf-8")
    monkeypatch.setenv("SINCOR_A2A_TASKS_PATH", str(tasks_path))
    return tasks_path


def _pool_with_tasks(monkeypatch, tmp_path, tasks, reserve=100.0):
    _seed_tasks_file(monkeypatch, tmp_path, tasks)
    return BountyPool(path=str(tmp_path / "pool.json"), reserve_axm=reserve)


# ---------------------------------------------------------------------------
# W-118 — spent reconciliation
# ---------------------------------------------------------------------------

def test_w118_spent_counter_zeroed_is_caught(tmp_path):
    """PoC §A shape: spent_wei zeroed in the persisted file."""
    _spent_task_pool(tmp_path)
    _mutate_pool_file(tmp_path, lambda raw: raw.update({"spent_wei": "0"}))
    p2 = _reload(tmp_path)
    # The corrupted counter still inflates available — that is what the
    # verification must now catch, not silently bless.
    assert p2.status()["available_axm"] == 100.0
    v = p2.verify_ledger()
    assert v["ok"] is False
    assert any("spent" in d for d in v["discrepancies"]), v


def test_w118_spent_counter_wrong_value_is_caught(tmp_path):
    """Spent counter drifted to a wrong nonzero value (30 vs journal 60)."""
    _spent_task_pool(tmp_path)
    _mutate_pool_file(
        tmp_path,
        lambda raw: raw.update({"spent_wei": str(30 * 10**18)}))
    v = _reload(tmp_path).verify_ledger()
    assert v["ok"] is False
    assert any("spent" in d for d in v["discrepancies"]), v


def test_w118_spent_journal_entry_tampered_is_caught(tmp_path):
    """Journal spend entry zeroed: journal (0) vs counter (60) disagree."""
    _spent_task_pool(tmp_path)

    def _tamper(raw):
        for entry in raw.get("history", []):
            if entry.get("action") == "spend":
                entry["amount_wei"] = "0"

    _mutate_pool_file(tmp_path, _tamper)
    v = _reload(tmp_path).verify_ledger()
    assert v["ok"] is False
    assert any("spent" in d for d in v["discrepancies"]), v


def test_w118_spent_record_state_tampered_is_caught(tmp_path):
    """Spent allocation record flipped to released: journal and counter
    still agree (60), so ONLY the counter-vs-spent-records comparison
    catches this drift."""
    p = _spent_task_pool(tmp_path)
    alloc_id = [a for a in p.status()["allocations"]
                if a["state"] == "spent"][0]["allocation_id"]

    def _flip(raw):
        raw["allocations"][alloc_id]["state"] = "released"

    _mutate_pool_file(tmp_path, _flip)
    v = _reload(tmp_path).verify_ledger()
    assert v["ok"] is False
    assert any("spent" in d for d in v["discrepancies"]), v


def test_w118_spent_happy_path_still_verifies(tmp_path):
    """Mixed spend/release history must not false-positive."""
    p = _mkpool(tmp_path)
    a1 = p.allocate("task-A", 60.0)
    a2 = p.allocate("task-B", 25.0)
    p.spend(a1["allocation_id"])
    p.release(a2["allocation_id"])
    p.allocate("task-C", 10.0)
    v = p.verify_ledger()
    assert v["ok"] is True, v
    assert v["discrepancies"] == []
    assert v["inconclusive"] is None


# ---------------------------------------------------------------------------
# W-119 — rehydrate guard
# ---------------------------------------------------------------------------

def test_w119_missing_file_with_live_task_refuses_full_fund(
        tmp_path, monkeypatch, caplog):
    """PoC §B shape: live 60 allocation + pool file loss must never
    read available=100, and must fail loudly."""
    p = _pool_with_tasks(
        monkeypatch, tmp_path, [_task("task-A", "open")], reserve=100.0)
    with caplog.at_level(logging.ERROR, logger="sincor.a2a.pool"):
        # Reconstruct under the caplog handler to capture the boot log.
        p = BountyPool(path=str(tmp_path / "pool.json"), reserve_axm=100.0)
    st = p.status()
    assert st["funded_axm"] == 0
    assert st["available_axm"] == 0
    assert st["rehydrate_blocked"] is True
    assert any(o["task_id"] == "task-A"
               for o in st["rehydrate_blocked_obligations"])
    assert any(r.levelno >= logging.ERROR
               for r in caplog.records), "expected an ERROR-level log"
    with pytest.raises(PoolError, match="rehydrate"):
        p.fund()
    # Recovery: once the live task settles, a fresh boot auto-funds again.
    _seed_tasks_file(monkeypatch, tmp_path, [_task("task-A", "settled")])
    p2 = BountyPool(path=str(tmp_path / "pool.json"), reserve_axm=100.0)
    assert p2.status()["funded_axm"] == 100.0
    assert p2.status()["rehydrate_blocked"] is False


def test_w119_corrupt_file_with_live_task_blocks_and_preserves_file(
        tmp_path, monkeypatch, caplog):
    """Corrupt-JSON variant: blocked, funded stays 0, and the corrupt
    file is left in place for operator forensics (never overwritten)."""
    pool_path = tmp_path / "pool.json"
    pool_path.write_text("{not valid json", encoding="utf-8")
    _seed_tasks_file(monkeypatch, tmp_path, [_task("task-A", "auction")])
    with caplog.at_level(logging.ERROR, logger="sincor.a2a.pool"):
        p = BountyPool(path=str(pool_path), reserve_axm=100.0)
    assert p.status()["funded_axm"] == 0
    assert p.status()["available_axm"] == 0
    assert p.status()["rehydrate_blocked"] is True
    assert pool_path.read_text(encoding="utf-8") == "{not valid json"
    assert any(r.levelno >= logging.ERROR for r in caplog.records)
    with pytest.raises(PoolError, match="rehydrate"):
        p.fund()


def test_w119_clean_first_boot_still_auto_funds(tmp_path, monkeypatch, caplog):
    """No pool file, no live tasks: the historical auto-fund behaviour
    is unchanged, and nothing is logged at ERROR or above."""
    _seed_tasks_file(monkeypatch, tmp_path, [])
    with caplog.at_level(logging.ERROR, logger="sincor.a2a.pool"):
        p = BountyPool(path=str(tmp_path / "pool.json"), reserve_axm=100.0)
    assert p.status()["funded_axm"] == 100.0
    assert p.status()["available_axm"] == 100.0
    assert p.status()["rehydrate_blocked"] is False
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


def test_w119_terminal_tasks_do_not_block(tmp_path, monkeypatch):
    """Settled/expired/rejected/canceled tasks already consumed their
    backing (spent or released), so they are not live obligations."""
    tasks = [_task("t-settled", "settled"),
             _task("t-expired", "expired"),
             _task("t-rejected", "rejected"),
             _task("t-canceled", "canceled"),
             _task("t-unbacked", "open", backed=False)]
    p = _pool_with_tasks(monkeypatch, tmp_path, tasks, reserve=100.0)
    assert p.status()["funded_axm"] == 100.0
    assert p.status()["rehydrate_blocked"] is False


def test_w119_failed_task_counts_as_live(tmp_path, monkeypatch):
    """A 'failed' task keeps its allocation: settlement spends only on
    receipt.ok, so the failed task is adjudication-pending, not done."""
    p = _pool_with_tasks(
        monkeypatch, tmp_path, [_task("task-A", "failed")], reserve=100.0)
    assert p.status()["funded_axm"] == 0
    assert p.status()["rehydrate_blocked"] is True


def test_w119_task_store_record_blocks(tmp_path, monkeypatch):
    """The pluggable task store is consulted as a second source: a
    pool-backed live record there blocks rehydrate just like the board
    file does. Cleanup restores the store singleton."""
    import sincor2.a2a_task_store as store_mod

    old_instance = store_mod._store_instance
    try:
        store = store_mod.get_task_store()
        store.put("store-task-1", {
            "task_id": "store-task-1", "state": "open",
            "pool_backed": True, "bounty_axm": 60.0})
        monkeypatch.setenv("SINCOR_A2A_TASKS_PATH",
                           str(tmp_path / "no-such-tasks.json"))
        p = BountyPool(path=str(tmp_path / "pool.json"), reserve_axm=100.0)
        assert p.status()["funded_axm"] == 0
        assert p.status()["rehydrate_blocked"] is True
        store.delete("store-task-1")
        p2 = BountyPool(path=str(tmp_path / "pool.json"), reserve_axm=100.0)
        assert p2.status()["funded_axm"] == 100.0
        assert p2.status()["rehydrate_blocked"] is False
    finally:
        store_mod._store_instance = old_instance


# ---------------------------------------------------------------------------
# Wiring gap — GET /v1/a2a/pool carries ledger_verification
# ---------------------------------------------------------------------------

def _board_client(tmp_path, monkeypatch):
    """Bare Flask app with the A2A blueprint mounted (mirrors
    test_task_board.board_client), pool + tasks redirected to tmp."""
    from sincor2.a2a_inbound import register as register_inbound
    from sincor2.a2a_inbound import reset_fabric

    reset_fabric()
    monkeypatch.setenv("SINCOR_A2A_TASKS_PATH", str(tmp_path / "tasks.json"))
    monkeypatch.setenv("SINCOR_BOUNTY_POOL_PATH", str(tmp_path / "pool.json"))
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-key")
    import sincor2.a2a_bounty_pool as pool_mod

    old_pool = pool_mod._POOL
    reset_bounty_pool(path=str(tmp_path / "pool.json"), reserve_axm=100.0)
    app = Flask(__name__)
    register_inbound(app)
    app.config["TESTING"] = True
    try:
        yield app.test_client()
    finally:
        pool_mod._POOL = old_pool


@pytest.fixture
def board_client(tmp_path, monkeypatch):
    yield from _board_client(tmp_path, monkeypatch)


def test_pool_status_endpoint_includes_ledger_verification(board_client):
    body = board_client.get("/v1/a2a/pool").get_json()
    assert body["reserve_axm"] == 100.0
    lv = body["ledger_verification"]
    assert lv["ok"] is True
    assert lv["discrepancies"] == []
    assert lv["inconclusive"] is None


def test_pool_status_endpoint_surfaces_spent_drift(
        board_client, tmp_path, monkeypatch):
    """Drifted singleton pool: the public endpoint must report ok=False
    rather than silently publishing inflated availability."""
    import sincor2.a2a_bounty_pool as pool_mod

    pool = pool_mod.bounty_pool()
    pool.fund()
    alloc = pool.allocate("task-A", 60.0)
    pool.spend(alloc["allocation_id"])
    assert pool.verify_ledger()["ok"]
    _mutate_pool_file(tmp_path, lambda raw: raw.update({"spent_wei": "0"}))
    reset_bounty_pool(path=str(tmp_path / "pool.json"), reserve_axm=100.0)
    body = board_client.get("/v1/a2a/pool").get_json()
    assert body["available_axm"] == 100.0  # corrupted counter, as before
    lv = body["ledger_verification"]
    assert lv["ok"] is False
    assert any("spent" in d for d in lv["discrepancies"]), lv


def test_verify_ledger_cost_on_large_journal(tmp_path):
    """Wiring cost: verify_ledger replays the in-memory journal on every
    pool-status read, so it must stay cheap at realistic journal sizes."""
    p = _mkpool(tmp_path, reserve=1000.0)
    for i in range(250):
        alloc = p.allocate(f"task-{i}", 1.0)
        if i % 2:
            p.spend(alloc["allocation_id"])
        else:
            p.release(alloc["allocation_id"])
    assert p.verify_ledger()["ok"]
    start = time.monotonic()
    v = p.verify_ledger()
    elapsed = time.monotonic() - start
    assert v["ok"] is True
    # ~500 journal entries; measured cost is reported, bound is generous.
    assert elapsed < 2.0, f"verify_ledger took {elapsed:.3f}s on 500 entries"


# ---------------------------------------------------------------------------
# W-119 follow-up — stateless ("{}") pool file is a corrupt pool file
# ---------------------------------------------------------------------------
# _save() always writes reserve/funded/spent/allocations/history/
# history_writes/saved_at in a single payload, so a file carrying NONE of
# those keys was never written by the pool: it is a truncated/wiped ledger
# (the "{}" shape), not a warm empty state. Before this follow-up it
# parsed cleanly, the rehydrate guard never engaged, and a manual fund()
# re-funded the full reserve over live claims — the W-119 double-promise
# through the side door.

def test_w119_stateless_file_with_live_task_blocks(
        tmp_path, monkeypatch, caplog):
    """{} pool file + live pool-backed task: blocked exactly like the
    corrupt-JSON case — no fund, obligation surfaced, file preserved."""
    pool_path = tmp_path / "pool.json"
    pool_path.write_text("{}", encoding="utf-8")
    _seed_tasks_file(monkeypatch, tmp_path, [_task("task-A", "open")])
    with caplog.at_level(logging.ERROR, logger="sincor.a2a.pool"):
        p = BountyPool(path=str(pool_path), reserve_axm=100.0)
    st = p.status()
    assert st["funded_axm"] == 0
    assert st["available_axm"] == 0
    assert st["rehydrate_blocked"] is True
    assert any(o["task_id"] == "task-A"
               for o in st["rehydrate_blocked_obligations"])
    assert any(r.levelno >= logging.ERROR
               for r in caplog.records), "expected an ERROR-level log"
    assert pool_path.read_text(encoding="utf-8") == "{}"
    with pytest.raises(PoolError, match="rehydrate"):
        p.fund()


def test_w119_stateless_file_no_live_tasks_allows_manual_fund(
        tmp_path, monkeypatch):
    """{} pool file + no live tasks: the corrupt-file asymmetry — no
    auto-fund and the file is left untouched for forensics, but a manual
    fund() is allowed (nothing is being double-promised)."""
    pool_path = tmp_path / "pool.json"
    pool_path.write_text("{}", encoding="utf-8")
    _seed_tasks_file(monkeypatch, tmp_path, [])
    p = BountyPool(path=str(pool_path), reserve_axm=100.0)
    st = p.status()
    assert st["funded_axm"] == 0
    assert st["available_axm"] == 0
    assert st["rehydrate_blocked"] is False
    # Not silently overwritten by the boot: still the operator's evidence.
    assert pool_path.read_text(encoding="utf-8") == "{}"
    st = p.fund()
    assert st["funded_axm"] == 100.0
    assert st["available_axm"] == 100.0


def test_w119_normally_saved_file_warm_boots_unblocked(tmp_path):
    """A file the pool itself wrote (fund/allocate/spend, each saving)
    always carries the full _save key set, so the stateless check can
    never trip on it: warm boot, not blocked, state preserved exactly."""
    p = _spent_task_pool(tmp_path)
    before = p.status()
    assert before["spent_axm"] == 60.0
    p2 = _reload(tmp_path)
    st = p2.status()
    assert st["rehydrate_blocked"] is False
    assert st == before
    assert p2.verify_ledger()["ok"]


def test_w119_missing_file_no_live_tasks_auto_funds_and_saves_full_state(
        tmp_path, monkeypatch):
    """Clean first boot (file MISSING) is untouched by the stateless
    check: auto-fund exactly as before, and the file the boot writes
    carries every _save key — the invariant the check relies on."""
    _seed_tasks_file(monkeypatch, tmp_path, [])
    pool_path = tmp_path / "pool.json"
    assert not pool_path.exists()
    p = BountyPool(path=str(pool_path), reserve_axm=100.0)
    assert p.status()["funded_axm"] == 100.0
    assert p.status()["rehydrate_blocked"] is False
    raw = json.loads(pool_path.read_text(encoding="utf-8"))
    assert {"reserve_wei", "funded_wei", "spent_wei", "allocations",
            "history", "history_writes", "saved_at"} <= set(raw)
