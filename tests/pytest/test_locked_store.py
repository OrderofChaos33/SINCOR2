"""Tests for the shared LockedJsonStore primitive and the migrated
kya JsonStore (persistence-research-2026-11-23, Phase 0 + Phase 1 step 3).

The two-process contention tests are the hunts' lost-update PoC pattern
(W-203/W-214 family): two writers in separate processes mutate the same
file; with load-once + blind rewrite one writer's updates are silently
dropped. Under LockedJsonStore both must survive.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
from pathlib import Path

import pytest

import sincor2.locked_store as locked_store_module
from sincor2.kya.store import JsonStore
from sincor2.locked_store import LockedJsonStore, LockedStoreError


# ---------------------------------------------------------------------------
# LockedJsonStore unit tests
# ---------------------------------------------------------------------------


def test_write_read_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    store = LockedJsonStore(path, default={})
    store.write({"b": 1, "a": [1, 2, 3]})
    assert store.read() == {"a": [1, 2, 3], "b": 1}
    # On-disk format: compact, sorted keys (JsonStore-compatible).
    assert path.read_text(encoding="utf-8") == '{"a":[1,2,3],"b":1}'


def test_read_missing_file_returns_default(tmp_path):
    store = LockedJsonStore(tmp_path / "nope.json", default={"x": 1})
    assert store.read() == {"x": 1}
    store_none = LockedJsonStore(tmp_path / "nope2.json")
    assert store_none.read() is None


def test_mutate_reloads_under_lock_two_instances(tmp_path):
    path = tmp_path / "shared.json"
    a = LockedJsonStore(path, default={})
    b = LockedJsonStore(path, default={})
    a.mutate(lambda s: s.update({"from_a": 1}))
    # b was constructed before a's write; its mutate must see a's state.
    b.mutate(lambda s: s.update({"from_b": 2}))
    assert LockedJsonStore(path, default={}).read() == {"from_a": 1, "from_b": 2}


def test_mutate_return_value_replaces_state(tmp_path):
    store = LockedJsonStore(tmp_path / "s.json", default={"n": 0})
    out = store.mutate(lambda s: {"n": s["n"] + 1})
    assert out == {"n": 1}
    assert store.read() == {"n": 1}


def test_mutate_exception_writes_nothing(tmp_path):
    path = tmp_path / "s.json"
    store = LockedJsonStore(path, default={"n": 0})
    store.write({"n": 5})

    def boom(state):
        state["n"] = 99
        raise ValueError("boom")

    with pytest.raises(ValueError):
        store.mutate(boom)
    assert store.read() == {"n": 5}


def test_corrupt_file_fail_closed_raises(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    store = LockedJsonStore(path, default={}, fail_closed=True)
    with pytest.raises(LockedStoreError):
        store.read()
    with pytest.raises(LockedStoreError):
        store.mutate(lambda s: s)


def test_corrupt_file_not_fail_closed_returns_default(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    store = LockedJsonStore(path, default={"d": 1}, fail_closed=False)
    assert store.read() == {"d": 1}


def test_no_tmp_leftovers_and_lock_sidecar(tmp_path):
    path = tmp_path / "s.json"
    store = LockedJsonStore(path, default={})
    store.write({"k": "v"})
    names = sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("s.json"))
    assert names == ["s.json", "s.json.lock"]


def test_fcntl_unavailable_fail_closed_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(locked_store_module, "fcntl", None)
    store = LockedJsonStore(tmp_path / "s.json", default={}, fail_closed=True)
    with pytest.raises(LockedStoreError):
        store.write({"a": 1})


def test_fcntl_unavailable_degrades_loudly_not_closed(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(locked_store_module, "fcntl", None)
    store = LockedJsonStore(tmp_path / "s.json", default={}, fail_closed=False)
    with caplog.at_level("WARNING"):
        store.write({"a": 1})
    assert store.read() == {"a": 1}
    assert any("fcntl" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Two-process contention (the lost-update PoC pattern)
# ---------------------------------------------------------------------------


def _mp_mutate_worker(path_str: str, tag: str, count: int) -> None:
    store = LockedJsonStore(Path(path_str), default={"keys": {}, "n": 0})

    def bump(state):
        state["n"] += 1
        return state

    for i in range(count):
        store.mutate(bump)
        store.mutate(lambda s, i=i: s["keys"].__setitem__(f"{tag}-{i}", i))


def _mp_jsonstore_worker(name: str, tag: str, count: int) -> None:
    store = JsonStore(name)
    for i in range(count):
        store.update(lambda s, i=i: {**(s or {}), f"{tag}-{i}": i})


def _run_two_processes(target, args_a, args_b):
    ctx = mp.get_context("fork")
    pa = ctx.Process(target=target, args=args_a)
    pb = ctx.Process(target=target, args=args_b)
    pa.start()
    pb.start()
    pa.join(60)
    pb.join(60)
    assert pa.exitcode == 0, f"worker A exitcode={pa.exitcode}"
    assert pb.exitcode == 0, f"worker B exitcode={pb.exitcode}"


def test_two_process_contention_both_writers_survive(tmp_path):
    path = tmp_path / "contended.json"
    LockedJsonStore(path, default={"keys": {}, "n": 0}).write({"keys": {}, "n": 0})
    _run_two_processes(
        _mp_mutate_worker, (str(path), "a", 25), (str(path), "b", 25)
    )
    final = LockedJsonStore(path, default={}).read()
    assert final["n"] == 50
    assert len(final["keys"]) == 50
    assert final["keys"]["a-0"] == 0 and final["keys"]["b-24"] == 24


def test_two_process_jsonstore_update_both_writers_survive():
    # JsonStore resolves its path from SINCOR_DATA_DIR (conftest tmp dir),
    # inherited by the forked workers.
    JsonStore("contention").save({})
    _run_two_processes(
        _mp_jsonstore_worker, ("contention", "a", 20), ("contention", "b", 20)
    )
    final = JsonStore("contention").load()
    assert len(final) == 40
    assert final["a-0"] == 0 and final["b-19"] == 19


# ---------------------------------------------------------------------------
# JsonStore migration: API / format compatibility
# ---------------------------------------------------------------------------


def test_jsonstore_api_and_byte_format_unchanged():
    store = JsonStore("compat_check")
    assert isinstance(store.path, Path)
    assert store.path.name == "compat_check.json"
    assert hasattr(store, "lock")
    store.save({"z": 1, "a": {"nested": [True, None]}})
    raw = store.path.read_text(encoding="utf-8")
    # Exactly the pre-migration serialization: compact separators, sort_keys.
    assert raw == json.dumps(
        {"z": 1, "a": {"nested": [True, None]}}, separators=(",", ":"), sort_keys=True
    )
    assert store.load() == {"a": {"nested": [True, None]}, "z": 1}


def test_jsonstore_load_missing_and_corrupt_return_none():
    store = JsonStore("missing_check")
    assert store.load() is None
    store.path.write_text("garbage{{{", encoding="utf-8")
    assert store.load() is None


def test_jsonstore_save_is_atomic_no_partial_file():
    store = JsonStore("atomic_check")
    store.save({"v": 1})
    store.save({"v": 2})
    assert store.load() == {"v": 2}
    leftovers = [p.name for p in store.path.parent.iterdir() if ".tmp." in p.name]
    assert leftovers == []
    assert os.path.exists(str(store.path) + ".lock")
